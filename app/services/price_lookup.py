"""Map statement period ends to nearest prior daily close (bulk, in-memory)."""

from __future__ import annotations

import bisect
from typing import Iterable


def _source_rank(source: str | None) -> int:
    return 0 if source == "stooq" else 1


def dedupe_closes_by_date(price_rows: list[dict]) -> list[tuple[str, float]]:
    """Return sorted (date, close) rows, preferring stooq when dates collide."""
    best: dict[str, tuple[int, float]] = {}
    for row in price_rows:
        date = (row.get("date") or "")[:10]
        close = row.get("close")
        if not date or close is None:
            continue
        rank = _source_rank(row.get("source"))
        prev = best.get(date)
        if prev is None or rank < prev[0]:
            best[date] = (rank, float(close))
    return sorted((date, close) for date, (_, close) in best.items())


def map_prices_by_period_end(
    period_ends: Iterable[str],
    price_rows: list[dict],
) -> dict[str, float | None]:
    """Map each period_end to the latest close on or before that date."""
    dates_closes = dedupe_closes_by_date(price_rows)
    if not dates_closes:
        return {period[:10]: None for period in period_ends if period}

    dates = [item[0] for item in dates_closes]
    closes = [item[1] for item in dates_closes]
    result: dict[str, float | None] = {}
    for raw in period_ends:
        if not raw:
            continue
        key = raw[:10]
        idx = bisect.bisect_right(dates, key) - 1
        result[key] = closes[idx] if idx >= 0 else None
    return result
