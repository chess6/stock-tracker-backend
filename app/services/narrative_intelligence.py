"""Phase D — narrative state detection, event clustering, fundamentals divergence."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..repositories import Repository
from .event_classification import classify_narrative_states
from .scoring import _gross_margin, margin_trend_delta

logger = logging.getLogger("stock_tracker.narrative_intelligence")

NEGATIVE_STATES = frozenset({"bankruptcy_fear", "liquidity_concern"})
POSITIVE_STATES = frozenset({
    "turnaround_optimism",
    "cyclical_recovery",
    "ai_optimism",
    "margin_stabilization",
})
FEAR_STATES = frozenset({"bankruptcy_fear", "liquidity_concern"})


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _article_text(article: dict) -> str:
    parts = [article.get("title") or "", article.get("summary") or ""]
    return " ".join(part for part in parts if part).strip()


def _sentiment_value(article: dict) -> float | None:
    score = article.get("sentimentScore")
    if score is not None:
        return float(score)
    reaction = article.get("reactionSentiment")
    if reaction is not None:
        return float(reaction)
    return None


def aggregate_narrative_states(articles: list[dict], *, days: int = 180) -> list[dict[str, Any]]:
    """D1 — aggregate detected narrative states across recent articles."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    tallies: dict[str, list[float]] = defaultdict(list)

    for article in articles:
        pub = _parse_dt(article.get("publishedAt"))
        if pub is not None and pub < cutoff:
            continue
        text = _article_text(article)
        if not text:
            continue
        for hit in classify_narrative_states(text):
            tallies[hit.state].append(hit.confidence)

    states: list[dict[str, Any]] = []
    for state, confidences in tallies.items():
        states.append({
            "state": state,
            "score": round(sum(confidences) / len(confidences), 4),
            "articleCount": len(confidences),
            "maxConfidence": round(max(confidences), 4),
        })
    states.sort(key=lambda item: (item["score"], item["articleCount"]), reverse=True)
    return states


def _articles_in_window(articles: list[dict], days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return [
        article for article in articles
        if (pub := _parse_dt(article.get("publishedAt"))) is not None and pub >= cutoff
    ]


def _avg_sentiment(articles: list[dict]) -> float | None:
    values = [v for article in articles if (v := _sentiment_value(article)) is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _news_bursts(articles: list[dict], *, window_days: int = 7, min_count: int = 3) -> list[dict[str, Any]]:
    recent = sorted(
        _articles_in_window(articles, 90),
        key=lambda item: item.get("publishedAt") or "",
    )
    bursts: list[dict[str, Any]] = []
    window: list[dict] = []
    window_start: datetime | None = None

    for article in recent:
        pub = _parse_dt(article.get("publishedAt"))
        if pub is None:
            continue
        if not window:
            window = [article]
            window_start = pub
            continue
        if (pub - window_start).days <= window_days:
            window.append(article)
        else:
            if len(window) >= min_count:
                bursts.append({
                    "startDate": window_start.date().isoformat(),
                    "endDate": _parse_dt(window[-1].get("publishedAt")).date().isoformat(),
                    "articleCount": len(window),
                    "avgSentiment": _avg_sentiment(window),
                    "dominantEvent": _dominant_event_type(window),
                })
            window = [article]
            window_start = pub

    if len(window) >= min_count:
        bursts.append({
            "startDate": window_start.date().isoformat() if window_start else None,
            "endDate": _parse_dt(window[-1].get("publishedAt")).date().isoformat(),
            "articleCount": len(window),
            "avgSentiment": _avg_sentiment(window),
            "dominantEvent": _dominant_event_type(window),
        })
    bursts.sort(key=lambda item: item.get("articleCount") or 0, reverse=True)
    return bursts


def _dominant_event_type(articles: list[dict]) -> str | None:
    counts: dict[str, int] = defaultdict(int)
    for article in articles:
        event_type = article.get("primaryEvent") or article.get("eventType") or "other"
        counts[event_type] += 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def build_emerging_situations(
    articles: list[dict],
    insider_clusters: list[dict],
    *,
    margin_trend: float | None = None,
    buy6m: float | None = None,
) -> list[dict[str, Any]]:
    """D2 — correlate news bursts, insider clusters, and fundamental inflection."""
    situations: list[dict[str, Any]] = []
    bursts = _news_bursts(articles)

    for cluster in insider_clusters[:10]:
        buy_value = cluster.get("total_buy_value") or 0
        if buy_value <= 0:
            continue
        cluster_start = cluster.get("windowStart") or cluster.get("window_start")
        cluster_end = cluster.get("windowEnd") or cluster.get("window_end")
        overlapping = [
            burst for burst in bursts
            if burst.get("startDate") and cluster_end and burst["startDate"] <= cluster_end[:10]
            and burst.get("endDate") and cluster_start and burst["endDate"] >= cluster_start[:10]
        ]
        if overlapping or buy_value >= 250_000:
            situations.append({
                "type": "insider_news_cluster",
                "signal": "emerging_situation",
                "confidence": round(min(1.0, 0.5 + (len(overlapping) * 0.15)), 4),
                "insiderBuyValue": buy_value,
                "buyCount": cluster.get("buy_count") or cluster.get("buyCount"),
                "windowStart": cluster_start,
                "windowEnd": cluster_end,
                "newsBurstCount": len(overlapping),
                "description": "Insider buying cluster with overlapping news activity.",
            })

    if margin_trend is not None and margin_trend > 0 and bursts:
        situations.append({
            "type": "margin_recovery_burst",
            "signal": "fundamental_inflection",
            "confidence": round(min(1.0, 0.45 + margin_trend * 2), 4),
            "marginTrend": margin_trend,
            "newsBurstCount": len(bursts),
            "description": "Positive margin trend coinciding with elevated news flow.",
        })

    recent_states = aggregate_narrative_states(articles, days=90)
    if buy6m and buy6m >= 100_000 and recent_states:
        fear_hits = [item for item in recent_states if item["state"] in FEAR_STATES]
        if fear_hits:
            situations.append({
                "type": "insider_accumulation_fear_cycle",
                "signal": "high_conviction",
                "confidence": round(min(1.0, 0.55 + fear_hits[0]["score"] * 0.3), 4),
                "buy6m": buy6m,
                "fearState": fear_hits[0]["state"],
                "description": "Insider accumulation during negative narrative cycle.",
            })

    situations.sort(key=lambda item: item.get("confidence") or 0, reverse=True)
    return situations[:10]


def compute_narrative_divergence(
    *,
    sentiment_90d: float | None,
    margin_trend: float | None,
    survivability: float | None,
    narrative_states: list[dict[str, Any]],
    insider_buy6m: float | None = None,
) -> dict[str, Any]:
    """D3 — score fundamentals vs narrative tone divergence."""
    fundamentals_improving = (
        (margin_trend is not None and margin_trend > 0.01)
        or (survivability is not None and survivability >= 45)
    )
    fundamentals_deteriorating = (
        (margin_trend is not None and margin_trend < -0.01)
        or (survivability is not None and survivability < 30)
    )

    negative_tone = sentiment_90d is not None and sentiment_90d < -0.05
    positive_tone = sentiment_90d is not None and sentiment_90d > 0.15
    fear_states = [item for item in narrative_states if item.get("state") in FEAR_STATES]
    optimism_states = [item for item in narrative_states if item.get("state") in POSITIVE_STATES]

    if fear_states:
        negative_tone = True
    if optimism_states and not negative_tone:
        positive_tone = True

    signal = "neutral"
    score = 0.5
    description = "Fundamentals and narrative are broadly aligned."

    if fundamentals_improving and negative_tone:
        signal = "rerating_candidate"
        score = 0.75 + min(0.2, abs(sentiment_90d or 0) * 0.5)
        description = "Improving fundamentals with negative or fearful narrative — potential rerating setup."
    elif fundamentals_deteriorating and positive_tone:
        signal = "risk_flag"
        score = 0.25 - min(0.15, (sentiment_90d or 0) * 0.3)
        description = "Deteriorating fundamentals with optimistic narrative — risk flag."
    elif insider_buy6m and insider_buy6m >= 100_000 and fear_states:
        signal = "high_conviction"
        score = 0.85
        description = "Insider accumulation during fear-cycle narrative."
    elif fundamentals_improving and positive_tone:
        signal = "aligned_positive"
        score = 0.65
        description = "Improving fundamentals with constructive narrative."
    elif fundamentals_deteriorating and negative_tone:
        signal = "aligned_negative"
        score = 0.35
        description = "Weak fundamentals with negative narrative."

    score = max(0.0, min(1.0, round(score, 4)))
    return {
        "divergenceScore": score,
        "signal": signal,
        "description": description,
        "inputs": {
            "sentiment90d": sentiment_90d,
            "marginTrend": margin_trend,
            "survivability": survivability,
            "insiderBuy6m": insider_buy6m,
            "topStates": [item["state"] for item in narrative_states[:3]],
        },
    }


def _margin_trend_for_ticker(repo: Repository, ticker: str) -> float | None:
    rows = repo.fetch_fundamentals_rows([ticker], dimension="ARY")
    if not rows:
        return None
    from .fundamentals import collapse_narrow_fundamentals_rows, pivot_fundamentals_rows

    annual = pivot_fundamentals_rows(
        collapse_narrow_fundamentals_rows(rows, annual=True),
        canonical_annual=True,
    )
    annual.sort(key=lambda row: row.get("calendardate") or "", reverse=True)
    return margin_trend_delta(annual, 3, _gross_margin)


def build_narrative_intelligence(
    repo: Repository,
    ticker: str,
    articles: list[dict],
) -> dict[str, Any]:
    symbol = ticker.upper()
    company = repo.get_company_by_ticker(symbol)
    if not company:
        return {"ticker": symbol, "error": "not_found"}

    narrative_states = aggregate_narrative_states(articles)
    recent_90d = _articles_in_window(articles, 90)
    sentiment_90d = _avg_sentiment(recent_90d)
    margin_trend = _margin_trend_for_ticker(repo, symbol)
    scores = repo.fetch_latest_company_scores([symbol], dimension="ARY").get(symbol) or {}
    survivability = scores.get("survivability")
    insider_summary = repo.fetch_insider_summary_90d([symbol])
    buy6m = insider_summary[0].get("totalBuyValue90d") if insider_summary else None
    insider_clusters = repo.fetch_insider_clusters_for_company(company["id"], limit=10)

    divergence = compute_narrative_divergence(
        sentiment_90d=sentiment_90d,
        margin_trend=margin_trend,
        survivability=survivability,
        narrative_states=narrative_states,
        insider_buy6m=buy6m,
    )
    emerging_situations = build_emerging_situations(
        articles,
        insider_clusters,
        margin_trend=margin_trend,
        buy6m=buy6m,
    )

    return {
        "ticker": symbol,
        "narrativeStates": narrative_states,
        "narrativeDivergence": divergence,
        "emergingSituations": emerging_situations,
        "newsBursts": _news_bursts(articles),
    }


def snapshot_narrative_intelligence(
    repo: Repository,
    tickers: list[str],
    *,
    snapshot_date: str | None = None,
) -> dict[str, Any]:
    snapshot_day = snapshot_date or date.today().isoformat()
    written = 0
    per_ticker: dict[str, str] = {}

    for ticker in tickers:
        symbol = ticker.strip().upper()
        if not symbol:
            continue
        articles = repo.fetch_narrative_articles_for_ticker(symbol, limit=200)
        payload = build_narrative_intelligence(repo, symbol, articles)
        if payload.get("error"):
            per_ticker[symbol] = payload["error"]
            continue
        count = repo.upsert_company_narrative_snapshots([
            {
                "ticker": symbol,
                "snapshot_date": snapshot_day,
                "states": payload.get("narrativeStates"),
                "divergence_score": payload.get("narrativeDivergence", {}).get("divergenceScore"),
                "divergence_signal": payload.get("narrativeDivergence", {}).get("signal"),
                "emerging_situations": payload.get("emergingSituations"),
            }
        ])
        written += count
        per_ticker[symbol] = "ok"
        logger.info("snapshot_narrative_intelligence ticker=%s signal=%s", symbol, payload["narrativeDivergence"]["signal"])

    return {"snapshotDate": snapshot_day, "written": written, "tickers": per_ticker}
