"""Unified Signal read-layer — normalizes research_queue, insider, narrative, and EDGAR silos."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from .signal_scoring import SignalScoreInputs, compute_research_importance, research_importance_breakdown

if TYPE_CHECKING:
    from ..repositories import Repository

logger = logging.getLogger(__name__)

QUEUE_EVENT_TO_SIGNAL_TYPE = {
    "new_insider_cluster": "insider_cluster_buy",
}

NARRATIVE_SIGNAL_TYPES = frozenset({"rerating_candidate", "high_conviction", "risk_flag"})


def build_dedup_key(ticker: str, signal_type: str, event_date: str | None) -> str:
    return f"{(ticker or '').upper()}:{(signal_type or '').lower()}:{event_date or ''}"


def _normalize_queue_signal_type(event_type: str, details: dict[str, Any] | None) -> str:
    details = details or {}
    if event_type == "new_catalyst":
        return str(details.get("catalystType") or "new_catalyst").lower()
    if event_type == "thesis_catalyst":
        return str(details.get("catalystType") or "thesis_catalyst").lower()
    if event_type == "narrative_divergence":
        return str(details.get("divergenceSignal") or "narrative_divergence").lower()
    return QUEUE_EVENT_TO_SIGNAL_TYPE.get(event_type, event_type)


def _why_it_matters(signal_type: str, details: dict[str, Any] | None, *, fallback: str | None = None) -> str:
    details = details or {}
    st = signal_type.lower()
    if st in ("insider_cluster_buy", "new_insider_cluster"):
        buyers = details.get("uniqueBuyers")
        intensity = details.get("intensityScore")
        if buyers is not None and intensity is not None:
            return f"{buyers} insiders bought in cluster (intensity {float(intensity):.2f})"
        return fallback or "Insider buying cluster detected"
    if st in NARRATIVE_SIGNAL_TYPES or st == "narrative_divergence":
        score = details.get("divergenceScore")
        signal = details.get("divergenceSignal") or st
        if score is not None:
            return f"Narrative divergence ({signal}, score {float(score):.2f})"
        return fallback or f"Narrative divergence signal: {signal}"
    if st in ("rank_up", "rank_down"):
        delta = details.get("rankDelta")
        composite = details.get("composite") or "composite"
        if delta is not None:
            direction = "improved" if int(delta) > 0 else "declined"
            return f"Rank {direction} by {abs(int(delta))} in {composite}"
        return fallback or f"Material rank move ({st})"
    if st == "score_improvement":
        improvement = details.get("improvement")
        metric = details.get("metric") or "score"
        if improvement is not None:
            return f"{metric} improved by {improvement}"
        return fallback or "Fundamental score improvement"
    if st == "going_concern_8k":
        return fallback or "Going concern opinion flagged in SEC filing"
    if st == "activist_13d":
        return fallback or "Activist 13D stake filed"
    if st == "new_catalyst" or st in (
        "earnings_beat",
        "earnings_miss",
        "guidance_increase",
        "guidance_cut",
        "mergers_acquisitions",
    ):
        title = details.get("articleTitle")
        if title:
            return f"Catalyst: {title}"
        return fallback or f"Material catalyst: {st.replace('_', ' ')}"
    if details.get("summary"):
        return str(details["summary"])
    return fallback or f"Material signal: {st.replace('_', ' ')}"


def _magnitude_for_signal(signal_type: str, details: dict[str, Any] | None) -> float | None:
    details = details or {}
    st = signal_type.lower()
    if st in ("insider_cluster_buy", "new_insider_cluster"):
        raw = details.get("intensityScore")
        return float(raw) if raw is not None else None
    if st in NARRATIVE_SIGNAL_TYPES or st == "narrative_divergence":
        raw = details.get("divergenceScore")
        return float(raw) if raw is not None else None
    if st in ("rank_up", "rank_down"):
        raw = details.get("rankDelta")
        if raw is None:
            return None
        return min(1.0, abs(float(raw)) / 20.0)
    if st == "score_improvement":
        raw = details.get("improvement")
        if raw is None:
            return None
        return min(1.0, float(raw) / 9.0)
    confidence = details.get("confidence")
    return float(confidence) if confidence is not None else None


def _evidence_from_queue(details: dict[str, Any] | None) -> list[dict[str, Any]]:
    details = details or {}
    evidence: list[dict[str, Any]] = []
    article_id = details.get("articleId")
    if article_id is not None:
        item: dict[str, Any] = {"type": "article", "id": article_id}
        if details.get("articleTitle"):
            item["title"] = details["articleTitle"]
        if details.get("publishedAt"):
            item["publishedAt"] = details["publishedAt"]
        evidence.append(item)
    if details.get("windowStart") or details.get("windowEnd"):
        evidence.append(
            {
                "type": "insider_cluster",
                "windowStart": details.get("windowStart"),
                "windowEnd": details.get("windowEnd"),
                "uniqueBuyers": details.get("uniqueBuyers"),
            }
        )
    return evidence


def _evidence_from_edgar(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": "filing",
            "formType": row.get("form_type"),
            "accession": row.get("accession"),
            "itemNumber": row.get("item_number"),
            "summary": row.get("summary"),
        }
    ]


def _score_signal(
    *,
    signal_type: str,
    ticker: str,
    event_date: str | None,
    detected_at: str | None,
    details: dict[str, Any] | None,
    context: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    details = details or {}
    abnormal = details.get("abnormalReturn1d")
    article_id = details.get("articleId")
    if abnormal is None and article_id is not None:
        abnormal = context.get("article_returns", {}).get(int(article_id))

    portfolio = context.get("portfolio_tickers", set())
    watchlist = context.get("watchlist_tickers", set())
    fundamentals = context.get("fundamentals_tickers", set())
    symbol = ticker.upper()

    inputs = SignalScoreInputs(
        signal_type=signal_type,
        event_date=event_date,
        detected_at=detected_at,
        magnitude=_magnitude_for_signal(signal_type, details),
        abnormal_return_1d=float(abnormal) if abnormal is not None else None,
        divergence_score=details.get("divergenceScore"),
        in_portfolio=symbol in portfolio,
        in_watchlist=symbol in watchlist and symbol not in portfolio,
        has_fundamentals=symbol in fundamentals,
        confidence=details.get("confidence"),
    )
    return compute_research_importance(inputs), research_importance_breakdown(inputs)


def _signal_from_queue_item(item: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    details = item.get("details") or {}
    signal_type = _normalize_queue_signal_type(item.get("eventType") or "", details)
    ticker = item.get("ticker") or ""
    event_date = item.get("eventDate")
    importance, breakdown = _score_signal(
        signal_type=signal_type,
        ticker=ticker,
        event_date=event_date,
        detected_at=item.get("createdAt"),
        details=details,
        context=context,
    )
    return {
        "id": item.get("id"),
        "tickers": [ticker],
        "ticker": ticker,
        "companyName": context.get("company_names", {}).get(ticker.upper()),
        "signalType": signal_type,
        "eventDate": event_date,
        "detectedAt": item.get("createdAt"),
        "magnitude": _magnitude_for_signal(signal_type, details),
        "direction": details.get("direction"),
        "state": "new",
        "researchImportance": importance,
        "importanceBreakdown": breakdown,
        "whyItMatters": _why_it_matters(signal_type, details),
        "evidence": _evidence_from_queue(details),
        "dedupKey": build_dedup_key(ticker, signal_type, event_date),
        "source": "research_queue",
        "dismissed": bool(item.get("dismissed")),
        "priority": item.get("priority"),
    }


def _signal_from_insider_cluster(cluster: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    ticker = cluster.get("ticker") or ""
    event_date = (
        cluster.get("windowEnd")
        or cluster.get("clusterDetectedAt")
        or cluster.get("computedAt")
        or ""
    )[:10]
    details = {
        "uniqueBuyers": cluster.get("uniqueBuyers"),
        "intensityScore": cluster.get("intensityScore"),
        "totalBuyValue": cluster.get("totalBuyValue"),
        "windowStart": cluster.get("windowStart"),
        "windowEnd": cluster.get("windowEnd"),
    }
    signal_type = "insider_cluster_buy"
    importance, breakdown = _score_signal(
        signal_type=signal_type,
        ticker=ticker,
        event_date=event_date,
        detected_at=cluster.get("computedAt"),
        details=details,
        context=context,
    )
    return {
        "tickers": [ticker],
        "ticker": ticker,
        "companyName": cluster.get("companyName"),
        "signalType": signal_type,
        "eventDate": event_date,
        "detectedAt": cluster.get("computedAt"),
        "magnitude": details.get("intensityScore"),
        "direction": "buy",
        "state": "new",
        "researchImportance": importance,
        "importanceBreakdown": breakdown,
        "whyItMatters": _why_it_matters(signal_type, details),
        "evidence": _evidence_from_queue(details),
        "dedupKey": build_dedup_key(ticker, signal_type, event_date),
        "source": "insider_cluster",
        "dismissed": False,
    }


def _signal_from_narrative(alert: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    ticker = alert.get("ticker") or ""
    signal_type = str(alert.get("divergenceSignal") or "narrative_divergence").lower()
    event_date = alert.get("snapshotDate")
    details = {
        "divergenceScore": alert.get("divergenceScore"),
        "divergenceSignal": alert.get("divergenceSignal"),
    }
    importance, breakdown = _score_signal(
        signal_type=signal_type,
        ticker=ticker,
        event_date=event_date,
        detected_at=event_date,
        details=details,
        context=context,
    )
    return {
        "tickers": [ticker],
        "ticker": ticker,
        "companyName": alert.get("companyName"),
        "signalType": signal_type,
        "eventDate": event_date,
        "detectedAt": event_date,
        "magnitude": alert.get("divergenceScore"),
        "direction": "divergence",
        "state": "new",
        "researchImportance": importance,
        "importanceBreakdown": breakdown,
        "whyItMatters": alert.get("context") or _why_it_matters(signal_type, details),
        "evidence": [{"type": "narrative_snapshot", "snapshotDate": event_date}],
        "dedupKey": build_dedup_key(ticker, signal_type, event_date),
        "source": "narrative",
        "dismissed": False,
    }


def _signal_from_edgar(row: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    ticker = row.get("ticker") or ""
    signal_type = str(row.get("signal_type") or "edgar_event").lower()
    event_date = row.get("event_date")
    details = {"summary": row.get("summary")}
    importance, breakdown = _score_signal(
        signal_type=signal_type,
        ticker=ticker,
        event_date=event_date,
        detected_at=event_date,
        details=details,
        context=context,
    )
    return {
        "tickers": [ticker],
        "ticker": ticker,
        "companyName": row.get("company_name"),
        "signalType": signal_type,
        "eventDate": event_date,
        "detectedAt": event_date,
        "magnitude": None,
        "direction": None,
        "state": "new",
        "researchImportance": importance,
        "importanceBreakdown": breakdown,
        "whyItMatters": _why_it_matters(signal_type, details, fallback=row.get("summary")),
        "evidence": _evidence_from_edgar(row),
        "dedupKey": build_dedup_key(ticker, signal_type, event_date),
        "source": row.get("source") or "edgar",
        "dismissed": False,
    }


def _signal_from_price_anomaly(row: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    ticker = row.get("ticker") or ""
    signal_type = "unusual_volume"
    event_date = row.get("event_date")
    details = row.get("details") or {}
    importance, breakdown = _score_signal(
        signal_type=signal_type,
        ticker=ticker,
        event_date=event_date,
        detected_at=event_date,
        details=details,
        context=context,
    )
    return {
        "tickers": [ticker],
        "ticker": ticker,
        "companyName": context.get("company_names", {}).get(ticker.upper()),
        "signalType": signal_type,
        "eventDate": event_date,
        "detectedAt": event_date,
        "magnitude": min(1.0, float(details.get("volumeRatio") or 0) / 5.0),
        "direction": "volume_spike",
        "state": "new",
        "researchImportance": importance,
        "importanceBreakdown": breakdown,
        "whyItMatters": details.get("summary") or "Unusual volume vs trailing average",
        "evidence": [{"type": "price", "volumeRatio": details.get("volumeRatio")}],
        "dedupKey": build_dedup_key(ticker, signal_type, event_date),
        "source": "price_anomaly",
        "dismissed": False,
    }


def _signal_from_catalyst(row: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    ticker = row.get("ticker") or ""
    event_date = row.get("event_date")
    event_d = None
    if event_date:
        try:
            event_d = date.fromisoformat(str(event_date)[:10])
        except ValueError:
            event_d = None
    days_until = (event_d - date.today()).days if event_d else None
    if days_until is not None and -3 <= days_until <= 1:
        signal_type = "earnings_today" if days_until <= 0 else "earnings_upcoming"
    else:
        signal_type = "earnings_upcoming" if row.get("event_type") == "earnings" else str(row.get("event_type") or "catalyst")
    details = row.get("details") or {}
    if row.get("confidence") is not None:
        details["confidence"] = row.get("confidence")
    if days_until is not None:
        details["daysUntilEarnings"] = days_until
    detected_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat() if days_until is not None and days_until <= 0 else event_date
    importance, breakdown = _score_signal(
        signal_type=signal_type,
        ticker=ticker,
        event_date=event_date,
        detected_at=detected_at,
        details=details,
        context=context,
    )
    if days_until is not None and days_until <= 1:
        importance = round(min(1.0, float(importance) + 0.08), 4)
        breakdown = {**breakdown, "total": importance}
    if days_until == 0:
        why = f"Earnings today ({event_date})"
    elif days_until is not None and days_until < 0:
        why = f"Earnings reporting window ({event_date})"
    elif days_until is not None and days_until <= 7:
        why = f"Earnings in {days_until} day{'s' if days_until != 1 else ''} ({event_date})"
    else:
        why = f"Upcoming {signal_type.replace('_', ' ')} on {event_date}"
    return {
        "tickers": [ticker],
        "ticker": ticker,
        "companyName": context.get("company_names", {}).get(ticker.upper()),
        "signalType": signal_type,
        "eventDate": event_date,
        "detectedAt": detected_at,
        "magnitude": float(row.get("confidence") or 0.55),
        "direction": "forward",
        "state": "new",
        "researchImportance": importance,
        "importanceBreakdown": breakdown,
        "whyItMatters": why,
        "evidence": [{"type": "calendar", "source": row.get("source")}],
        "dedupKey": build_dedup_key(ticker, signal_type, event_date),
        "source": "catalyst_calendar",
        "dismissed": False,
    }


def _fetch_short_interest_signals(repo: Repository, *, limit: int = 30) -> list[dict[str, Any]]:
    rows = repo.conn.execute(
        """
        SELECT ticker, settlement_date, short_interest, days_to_cover, avg_daily_volume
        FROM short_interest_snapshots
        WHERE days_to_cover IS NOT NULL AND days_to_cover >= 2.5
        ORDER BY days_to_cover DESC, settlement_date DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [
        {
            "ticker": row["ticker"],
            "signal_type": "short_interest_spike",
            "event_date": row["settlement_date"],
            "details": {
                "daysToCover": row["days_to_cover"],
                "shortInterest": row["short_interest"],
                "summary": f"Short interest days-to-cover {float(row['days_to_cover']):.1f}",
            },
        }
        for row in rows
    ]


RANK_MOVE_TYPES = frozenset({"rank_up", "rank_down"})
MAX_RANK_MOVE_SIGNALS = 20


def _cap_rank_move_signals(candidates: list[dict[str, Any]], *, enabled: bool = True) -> list[dict[str, Any]]:
    """Limit cross-sectional rank-move flood after nightly composite snapshots."""
    if not enabled:
        return candidates
    rank_moves = [c for c in candidates if c.get("signalType") in RANK_MOVE_TYPES]
    if len(rank_moves) <= MAX_RANK_MOVE_SIGNALS:
        return candidates
    other = [c for c in candidates if c.get("signalType") not in RANK_MOVE_TYPES]
    rank_moves.sort(
        key=lambda s: (
            -(float(s.get("researchImportance") or 0)),
            -(float(s.get("magnitude") or 0)),
        ),
    )
    return other + rank_moves[:MAX_RANK_MOVE_SIGNALS]


def _merge_signals(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for signal in candidates:
        key = signal.get("dedupKey") or ""
        if not key:
            continue
        existing = best.get(key)
        if existing is None:
            best[key] = signal
            continue
        if float(signal.get("researchImportance") or 0) > float(existing.get("researchImportance") or 0):
            best[key] = signal
    return sorted(best.values(), key=lambda item: (-(item.get("researchImportance") or 0), item.get("eventDate") or ""), reverse=False)


def _build_context(repo: Repository, tickers: set[str]) -> dict[str, Any]:
    portfolio = repo.fetch_portfolio_tickers()
    watchlist = repo.get_boosted_tickers()
    fundamentals = repo.fetch_tickers_with_fundamentals(list(tickers)) if tickers else set()
    company_names: dict[str, str | None] = {}
    if tickers:
        placeholders = ",".join("?" for _ in tickers)
        rows = repo.conn.execute(
            f"SELECT ticker, name FROM companies WHERE ticker IN ({placeholders})",
            [t.upper() for t in tickers],
        ).fetchall()
        company_names = {row["ticker"].upper(): row["name"] for row in rows}
    return {
        "portfolio_tickers": portfolio,
        "watchlist_tickers": watchlist,
        "fundamentals_tickers": fundamentals,
        "company_names": company_names,
        "article_returns": {},
    }


def get_signals(
    repo: Repository,
    *,
    limit: int = 50,
    signal_types: list[str] | None = None,
    tickers: list[str] | None = None,
    portfolio_only: bool = False,
    include_dismissed: bool = False,
    max_age_days: int = 30,
    min_importance: float | None = None,
) -> dict[str, Any]:
    """Assemble normalized signals from all silos, ranked by research_importance."""
    from .catalyst_calendar import fetch_upcoming_catalysts
    from .insider_analysis import build_insider_conviction_alerts
    from .narrative import build_narrative_divergence_alerts
    from .price_anomalies import detect_unusual_volume_signals

    normalized_types = [t.strip().lower() for t in (signal_types or []) if t and str(t).strip()] or None
    ticker_filter = {t.strip().upper() for t in (tickers or []) if t and str(t).strip()} or None

    items = repo.fetch_research_queue(limit=max(limit * 3, 100), dismissed=include_dismissed)
    candidates: list[dict[str, Any]] = []

    article_ids: list[int] = []
    for item in items:
        details = item.get("details") or {}
        aid = details.get("articleId")
        if aid is not None:
            article_ids.append(int(aid))

    insider_payload = build_insider_conviction_alerts(repo, min_intensity=0.25, limit=limit * 2)
    narrative_payload = build_narrative_divergence_alerts(repo, min_divergence=0.5, limit=limit * 2)
    edgar_rows = repo.fetch_recent_edgar_signals(limit=limit * 2, max_age_days=max_age_days)
    volume_rows = detect_unusual_volume_signals(repo, limit=limit)
    catalyst_rows = fetch_upcoming_catalysts(repo, limit=max(limit * 4, 120), horizon_days=30)
    short_rows = _fetch_short_interest_signals(repo, limit=limit)

    all_tickers: set[str] = set()
    for item in items:
        if item.get("ticker"):
            all_tickers.add(item["ticker"].upper())
    for alert in insider_payload.get("alerts") or []:
        if alert.get("ticker"):
            all_tickers.add(alert["ticker"].upper())
    for alert in narrative_payload.get("alerts") or []:
        if alert.get("ticker"):
            all_tickers.add(alert["ticker"].upper())
    for row in edgar_rows:
        if row.get("ticker"):
            all_tickers.add(row["ticker"].upper())
    for row in volume_rows:
        if row.get("ticker"):
            all_tickers.add(row["ticker"].upper())
    for row in catalyst_rows:
        if row.get("ticker"):
            all_tickers.add(row["ticker"].upper())
    for row in short_rows:
        if row.get("ticker"):
            all_tickers.add(row["ticker"].upper())

    context = _build_context(repo, all_tickers)
    context["article_returns"] = repo.fetch_abnormal_returns_for_articles(article_ids)

    for item in items:
        if ticker_filter and item.get("ticker", "").upper() not in ticker_filter:
            continue
        if portfolio_only and item.get("ticker", "").upper() not in context["portfolio_tickers"]:
            continue
        signal = _signal_from_queue_item(item, context)
        if normalized_types and signal["signalType"] not in normalized_types:
            continue
        candidates.append(signal)

    for cluster in insider_payload.get("alerts") or []:
        ticker = (cluster.get("ticker") or "").upper()
        if ticker_filter and ticker not in ticker_filter:
            continue
        if portfolio_only and ticker not in context["portfolio_tickers"]:
            continue
        signal = _signal_from_insider_cluster(cluster, context)
        if normalized_types and signal["signalType"] not in normalized_types:
            continue
        candidates.append(signal)

    for alert in narrative_payload.get("alerts") or []:
        ticker = (alert.get("ticker") or "").upper()
        if ticker_filter and ticker not in ticker_filter:
            continue
        if portfolio_only and ticker not in context["portfolio_tickers"]:
            continue
        signal = _signal_from_narrative(alert, context)
        if normalized_types and signal["signalType"] not in normalized_types:
            continue
        candidates.append(signal)

    cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
    for row in edgar_rows:
        ticker = (row.get("ticker") or "").upper()
        event_date = row.get("event_date") or ""
        if event_date and event_date < cutoff:
            continue
        if ticker_filter and ticker not in ticker_filter:
            continue
        if portfolio_only and ticker not in context["portfolio_tickers"]:
            continue
        signal = _signal_from_edgar(row, context)
        if normalized_types and signal["signalType"] not in normalized_types:
            continue
        candidates.append(signal)

    for row in volume_rows:
        ticker = (row.get("ticker") or "").upper()
        if ticker_filter and ticker not in ticker_filter:
            continue
        if portfolio_only and ticker not in context["portfolio_tickers"]:
            continue
        signal = _signal_from_price_anomaly(row, context)
        if normalized_types and signal["signalType"] not in normalized_types:
            continue
        candidates.append(signal)

    for row in catalyst_rows:
        ticker = (row.get("ticker") or "").upper()
        if ticker_filter and ticker not in ticker_filter:
            continue
        if portfolio_only and ticker not in context["portfolio_tickers"]:
            continue
        signal = _signal_from_catalyst(row, context)
        if normalized_types and signal["signalType"] not in normalized_types:
            continue
        candidates.append(signal)

    for row in short_rows:
        ticker = (row.get("ticker") or "").upper()
        if ticker_filter and ticker not in ticker_filter:
            continue
        if portfolio_only and ticker not in context["portfolio_tickers"]:
            continue
        signal_type = row.get("signal_type") or "short_interest_spike"
        event_date = row.get("event_date")
        details = row.get("details") or {}
        importance, breakdown = _score_signal(
            signal_type=signal_type,
            ticker=ticker,
            event_date=event_date,
            detected_at=event_date,
            details=details,
            context=context,
        )
        signal = {
            "tickers": [ticker],
            "ticker": ticker,
            "companyName": context.get("company_names", {}).get(ticker),
            "signalType": signal_type,
            "eventDate": event_date,
            "detectedAt": event_date,
            "magnitude": min(1.0, float(details.get("daysToCover") or 0) / 10.0),
            "direction": "short",
            "state": "new",
            "researchImportance": importance,
            "importanceBreakdown": breakdown,
            "whyItMatters": details.get("summary") or "Elevated short interest",
            "evidence": [{"type": "short_interest"}],
            "dedupKey": build_dedup_key(ticker, signal_type, event_date),
            "source": "short_interest",
            "dismissed": False,
        }
        if normalized_types and signal["signalType"] not in normalized_types:
            continue
        candidates.append(signal)

    merged = _merge_signals(
        _cap_rank_move_signals(candidates, enabled=normalized_types is None),
    )
    if min_importance is not None:
        merged = [s for s in merged if float(s.get("researchImportance") or 0) >= min_importance]
    trimmed = merged[: max(1, int(limit))]

    return {
        "returned": len(trimmed),
        "limit": limit,
        "totalCandidates": len(candidates),
        "uniqueAfterDedup": len(merged),
        "items": trimmed,
    }
