"""Research queue — prioritized events for analyst review."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..repositories import Repository

from .feature_flags import is_enabled

logger = logging.getLogger(__name__)

RANK_DELTA_THRESHOLD = 5
CATALYST_EVENT_TYPES = (
    "earnings_beat",
    "earnings_miss",
    "guidance_increase",
    "guidance_cut",
    "mergers_acquisitions",
    "stock_buyback",
    "insider_buying",
    "debt_reduction",
    "capital_raise",
    "regulation_legal_risk",
)
THESIS_CATALYST_TYPES = ("activist_13d", "fcf_inflection", "margin_recovery_burst")
NARRATIVE_SIGNALS = ("rerating_candidate", "high_conviction")


def _iso_cutoff(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _detect_rank_changes(repo: Repository) -> list[dict[str, Any]]:
    rows = repo.conn.execute(
        """
        WITH ranked_snapshots AS (
            SELECT
                ticker,
                composite,
                snapshot_date,
                rank_in_universe,
                ROW_NUMBER() OVER (
                    PARTITION BY ticker, composite
                    ORDER BY snapshot_date DESC
                ) AS rn
            FROM company_rank_snapshots
            WHERE rank_in_universe IS NOT NULL
        ),
        paired AS (
            SELECT
                cur.ticker,
                cur.composite,
                cur.snapshot_date AS event_date,
                cur.rank_in_universe AS current_rank,
                prev.rank_in_universe AS prior_rank,
                (prev.rank_in_universe - cur.rank_in_universe) AS rank_delta
            FROM ranked_snapshots cur
            JOIN ranked_snapshots prev
              ON prev.ticker = cur.ticker
             AND prev.composite = cur.composite
             AND prev.rn = 2
            WHERE cur.rn = 1
        )
        SELECT *
        FROM paired
        WHERE ABS(rank_delta) >= ?
        """,
        (RANK_DELTA_THRESHOLD,),
    ).fetchall()

    events: list[dict[str, Any]] = []
    for row in rows:
        delta = int(row["rank_delta"])
        event_type = "rank_up" if delta > 0 else "rank_down"
        events.append(
            {
                "ticker": row["ticker"],
                "event_type": event_type,
                "event_date": row["event_date"],
                "priority": max(10, 50 - abs(delta)),
                "details": {
                    "composite": row["composite"],
                    "currentRank": row["current_rank"],
                    "priorRank": row["prior_rank"],
                    "rankDelta": delta,
                },
            }
        )
    return events


def _detect_new_insider_clusters(repo: Repository, *, window_days: int = 14) -> list[dict[str, Any]]:
    cutoff = _iso_cutoff(window_days)
    rows = repo.conn.execute(
        """
        SELECT
            c.ticker,
            ica.window_end,
            ica.window_start,
            ica.intensity_score,
            ica.buy_count,
            ica.unique_buyers,
            ica.total_buy_value,
            ica.computed_at
        FROM insider_cluster_analysis ica
        JOIN companies c ON c.id = ica.company_id
        WHERE COALESCE(ica.computed_at, '') >= ?
          AND COALESCE(ica.intensity_score, 0) > 0
        ORDER BY ica.intensity_score DESC
        """,
        (cutoff,),
    ).fetchall()

    events: list[dict[str, Any]] = []
    best_by_ticker: dict[str, dict[str, Any]] = {}
    for row in rows:
        intensity = float(row["intensity_score"] or 0)
        event_date = (row["window_end"] or row["computed_at"] or cutoff)[:10]
        ticker = row["ticker"]
        candidate = {
            "ticker": ticker,
            "event_type": "new_insider_cluster",
            "event_date": event_date,
            "priority": max(10, int(40 - intensity * 30)),
            "details": {
                "windowStart": row["window_start"],
                "windowEnd": row["window_end"],
                "intensityScore": intensity,
                "buyCount": row["buy_count"],
                "uniqueBuyers": row["unique_buyers"],
                "totalBuyValue": row["total_buy_value"],
            },
        }
        existing = best_by_ticker.get(ticker)
        if existing is None:
            best_by_ticker[ticker] = candidate
            continue
        existing_intensity = float((existing.get("details") or {}).get("intensityScore") or 0)
        existing_date = existing.get("event_date") or ""
        if intensity > existing_intensity or (
            intensity == existing_intensity and event_date > existing_date
        ):
            best_by_ticker[ticker] = candidate
    events.extend(best_by_ticker.values())
    return events


def _detect_narrative_divergence(repo: Repository, *, window_days: int = 7) -> list[dict[str, Any]]:
    cutoff = _iso_cutoff(window_days)
    rows = repo.conn.execute(
        """
        SELECT ticker, snapshot_date, divergence_signal, divergence_score
        FROM company_narrative_snapshots
        WHERE divergence_signal IN (?, ?)
          AND snapshot_date >= ?
        ORDER BY divergence_score DESC, snapshot_date DESC
        """,
        (*NARRATIVE_SIGNALS, cutoff),
    ).fetchall()

    events: list[dict[str, Any]] = []
    for row in rows:
        score = row["divergence_score"]
        priority = 45
        if score is not None:
            priority = max(10, int(55 - float(score) * 20))
        events.append(
            {
                "ticker": row["ticker"],
                "event_type": "narrative_divergence",
                "event_date": row["snapshot_date"],
                "priority": priority,
                "details": {
                    "divergenceSignal": row["divergence_signal"],
                    "divergenceScore": score,
                },
            }
        )
    return events


def _detect_score_improvements(repo: Repository) -> list[dict[str, Any]]:
    rows = repo.conn.execute(
        """
        WITH ranked_scores AS (
            SELECT
                c.ticker,
                cs.period_end,
                cs.piotroski_f,
                ROW_NUMBER() OVER (
                    PARTITION BY cs.company_id
                    ORDER BY cs.period_end DESC
                ) AS rn
            FROM company_scores cs
            JOIN companies c ON c.id = cs.company_id
            WHERE cs.dimension = 'ARY'
              AND cs.piotroski_f IS NOT NULL
        )
        SELECT
            cur.ticker,
            cur.period_end AS event_date,
            prev.piotroski_f AS prior_score,
            cur.piotroski_f AS current_score,
            (cur.piotroski_f - prev.piotroski_f) AS improvement
        FROM ranked_scores cur
        JOIN ranked_scores prev
          ON prev.ticker = cur.ticker
         AND prev.rn = 2
        WHERE cur.rn = 1
          AND (cur.piotroski_f - prev.piotroski_f) >= 2
        """,
    ).fetchall()

    events: list[dict[str, Any]] = []
    for row in rows:
        improvement = int(row["improvement"])
        events.append(
            {
                "ticker": row["ticker"],
                "event_type": "score_improvement",
                "event_date": row["event_date"],
                "priority": max(10, 50 - improvement * 3),
                "details": {
                    "metric": "piotroski_f",
                    "priorScore": row["prior_score"],
                    "currentScore": row["current_score"],
                    "improvement": improvement,
                },
            }
        )
    return events


def _detect_thesis_catalysts(repo: Repository, *, window_days: int = 7) -> list[dict[str, Any]]:
    import json

    cutoff = _iso_cutoff(window_days)
    rows = repo.conn.execute(
        """
        SELECT ticker, snapshot_date, thesis_json
        FROM company_thesis_snapshots
        WHERE snapshot_date >= ?
        ORDER BY snapshot_date DESC
        """,
        (cutoff,),
    ).fetchall()

    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        thesis_json = row["thesis_json"]
        if not thesis_json:
            continue
        try:
            thesis = json.loads(thesis_json) if isinstance(thesis_json, str) else thesis_json
        except json.JSONDecodeError:
            continue
        watchlist = thesis.get("catalystWatchlist") or []
        for item in watchlist:
            catalyst_type = item.get("type")
            if catalyst_type not in THESIS_CATALYST_TYPES:
                continue
            key = (row["ticker"], catalyst_type, row["snapshot_date"])
            if key in seen:
                continue
            seen.add(key)
            events.append(
                {
                    "ticker": row["ticker"],
                    "event_type": "thesis_catalyst",
                    "event_date": row["snapshot_date"],
                    "priority": 42,
                    "details": {
                        "catalystType": catalyst_type,
                        "source": "thesis_engine",
                        "direction": item.get("direction"),
                        "horizon": item.get("horizon"),
                    },
                }
            )
    return events


def _detect_new_catalysts(repo: Repository, *, window_days: int = 3) -> list[dict[str, Any]]:
    cutoff = _iso_cutoff(window_days)
    placeholders = ",".join("?" for _ in CATALYST_EVENT_TYPES)
    rows = repo.conn.execute(
        f"""
        SELECT DISTINCT
            c.ticker,
            ec.event_type,
            DATE(ec.created_at) AS event_date,
            ec.confidence,
            a.id AS article_id,
            a.title AS article_title,
            a.published_at
        FROM article_event_classifications ec
        JOIN articles a ON a.id = ec.article_id
        JOIN article_company ac ON ac.article_id = a.id AND ac.confidence >= 0.80
        JOIN companies c ON c.id = ac.company_id
        WHERE ec.event_type IN ({placeholders})
          AND ec.created_at >= ?
          AND a.duplicate_of_article_id IS NULL
        ORDER BY ec.created_at DESC
        """,
        (*CATALYST_EVENT_TYPES, cutoff),
    ).fetchall()

    events: list[dict[str, Any]] = []
    for row in rows:
        confidence = float(row["confidence"] or 0)
        events.append(
            {
                "ticker": row["ticker"],
                "event_type": "new_catalyst",
                "event_date": row["event_date"],
                "priority": max(15, int(60 - confidence * 20)),
                "details": {
                    "catalystType": row["event_type"],
                    "confidence": confidence,
                    "articleId": row["article_id"],
                    "articleTitle": row["article_title"],
                    "publishedAt": row["published_at"],
                },
            }
        )
    return events


def build_research_queue(
    repo: Repository,
    *,
    limit: int = 50,
    max_age_days: int = 30,
) -> dict[str, Any]:
    """Run event detectors, upsert queue rows, and return the active queue."""
    expires_at = (datetime.utcnow() + timedelta(days=max_age_days)).strftime("%Y-%m-%d %H:%M:%S")
    detectors = (
        _detect_rank_changes,
        _detect_new_insider_clusters,
        _detect_narrative_divergence,
        _detect_score_improvements,
        _detect_new_catalysts,
        _detect_thesis_catalysts,
    )

    collected: list[dict[str, Any]] = []
    for detector in detectors:
        try:
            collected.extend(detector(repo))
        except Exception:
            logger.exception("research_queue detector failed: %s", detector.__name__)

    for item in collected:
        item["expires_at"] = expires_at

    written = repo.upsert_research_queue_items(collected)
    items = repo.fetch_research_queue(limit=limit, dismissed=False)
    logger.info(
        "build_research_queue detected=%d upserted=%d returned=%d",
        len(collected),
        written,
        len(items),
    )
    return {
        "detected": len(collected),
        "upserted": written,
        "returned": len(items),
        "items": items,
    }


def get_catalyst_feed(
    repo: Repository,
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """Return catalyst-oriented research queue items with source metadata."""
    if not is_enabled("experimental_research_queue", repo):
        return {"returned": 0, "items": [], "skipped": True, "reason": "feature_flag_disabled"}

    event_types = ["new_catalyst", "thesis_catalyst"]
    items = repo.fetch_research_queue(limit=limit, event_types=event_types, dismissed=False)
    enriched: list[dict[str, Any]] = []
    for item in items:
        details = item.get("details") or {}
        catalyst_type = details.get("catalystType")
        source_weight = None
        cluster_source_count = None
        article_id = details.get("articleId")
        if article_id:
            row = repo.conn.execute(
                """
                SELECT a.raw_source, a.event_cluster_id, c.source_count
                FROM articles a
                LEFT JOIN article_event_clusters c ON c.id = a.event_cluster_id
                WHERE a.id = ?
                """,
                (article_id,),
            ).fetchone()
            if row:
                source_weight = repo.get_feed_source_weight(row["raw_source"])
                cluster_source_count = row["source_count"]
        enriched.append(
            {
                **item,
                "sourceWeight": source_weight,
                "eventConfidence": details.get("confidence"),
                "clusterSourceCount": cluster_source_count,
                "catalystType": catalyst_type,
            }
        )
    return {
        "returned": len(enriched),
        "limit": limit,
        "items": enriched,
    }


def get_research_queue(
    repo: Repository,
    *,
    limit: int = 50,
    event_types: list[str] | None = None,
    dismissed: bool = False,
) -> dict[str, Any]:
    items = repo.fetch_research_queue(
        limit=limit,
        event_types=event_types,
        dismissed=dismissed,
    )
    return {
        "returned": len(items),
        "limit": limit,
        "items": items,
    }


def dismiss_research_queue_item(
    repo: Repository,
    ticker: str,
    *,
    event_type: str | None = None,
    event_date: str | None = None,
) -> tuple[dict[str, Any], int, str | None]:
    symbol = (ticker or "").strip().upper()
    if not symbol:
        return {"error": "ticker is required"}, 400, "invalid_ticker"

    dismissed = repo.dismiss_research_queue_items(
        symbol,
        event_type=event_type,
        event_date=event_date,
    )
    if dismissed == 0:
        return {"error": "not_found", "ticker": symbol}, 404, "not_found"
    return {"ticker": symbol, "dismissed": dismissed}, 200, None
