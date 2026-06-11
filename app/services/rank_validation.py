"""Validate composite rank persistence via forward price returns."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from ..repositories import Repository
from .composite_ranking import _COMPOSITE_PRESETS, known_composites

logger = logging.getLogger(__name__)

_DEFAULT_HORIZONS = (30, 90, 180)


def _forward_return(repo: Repository, ticker: str, start_date: str, horizon_days: int) -> float | None:
    start_price = repo.fetch_price_near_date(ticker, start_date)
    if start_price is None or start_price <= 0:
        return None
    try:
        end_day = date.fromisoformat(start_date[:10]) + timedelta(days=horizon_days)
    except ValueError:
        return None
    end_price = repo.fetch_price_near_date(ticker, end_day.isoformat())
    if end_price is None:
        return None
    return (end_price - start_price) / start_price


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def validate_composite_rank(
    repo: Repository,
    *,
    composite: str,
    horizons: tuple[int, ...] = _DEFAULT_HORIZONS,
    top_pct: float = 0.2,
    bottom_pct: float = 0.2,
    snapshot_limit: int = 8,
) -> tuple[dict[str, Any] | None, int, str | None]:
    composite_key = (composite or "deep_value").strip().lower()
    if composite_key not in _COMPOSITE_PRESETS:
        return None, 400, f"Unknown composite: {composite_key}"

    snapshot_dates = repo.fetch_distinct_rank_snapshot_dates(
        composite=composite_key,
        limit=snapshot_limit,
    )
    if not snapshot_dates:
        return None, 404, "insufficient_history"

    preset = _COMPOSITE_PRESETS[composite_key]
    horizon_payload: dict[str, dict[str, Any]] = {}

    for horizon in horizons:
        try:
            horizon_days = int(horizon)
        except (TypeError, ValueError):
            continue
        if horizon_days < 1:
            continue

        top_returns: list[float] = []
        bottom_returns: list[float] = []
        spreads: list[float] = []
        snapshots_evaluated = 0

        for snap_date in snapshot_dates:
            rows = repo.fetch_rank_snapshot_rows(composite=composite_key, snapshot_date=snap_date)
            if len(rows) < 10:
                continue

            top_n = max(1, int(len(rows) * top_pct))
            bottom_n = max(1, int(len(rows) * bottom_pct))
            top_tickers = [row["ticker"] for row in rows[:top_n]]
            bottom_tickers = [row["ticker"] for row in rows[-bottom_n:]]

            top_vals = [
                value
                for value in (_forward_return(repo, ticker, snap_date, horizon_days) for ticker in top_tickers)
                if value is not None
            ]
            bottom_vals = [
                value
                for value in (_forward_return(repo, ticker, snap_date, horizon_days) for ticker in bottom_tickers)
                if value is not None
            ]
            if not top_vals or not bottom_vals:
                continue

            top_avg = sum(top_vals) / len(top_vals)
            bottom_avg = sum(bottom_vals) / len(bottom_vals)
            top_returns.append(top_avg)
            bottom_returns.append(bottom_avg)
            spreads.append(top_avg - bottom_avg)
            snapshots_evaluated += 1

        positive_spread_pct = None
        if spreads:
            positive_spread_pct = round(
                100.0 * sum(1 for spread in spreads if spread > 0) / len(spreads),
                1,
            )

        horizon_payload[str(horizon_days)] = {
            "snapshotsEvaluated": snapshots_evaluated,
            "topQuartileAvgReturn": _avg(top_returns),
            "bottomQuartileAvgReturn": _avg(bottom_returns),
            "spread": _avg(spreads),
            "positiveSpreadPct": positive_spread_pct,
        }

    if not any(item["snapshotsEvaluated"] > 0 for item in horizon_payload.values()):
        return None, 404, "insufficient_price_history"

    logger.info(
        "validate_composite_rank composite=%s snapshots=%d horizons=%s",
        composite_key,
        len(snapshot_dates),
        list(horizon_payload.keys()),
    )

    return {
        "meta": {
            "composite": composite_key,
            "label": preset["label"],
            "snapshotDates": snapshot_dates,
            "horizons": list(horizon_payload.keys()),
            "knownComposites": known_composites(),
        },
        "horizons": horizon_payload,
    }, 200, None
