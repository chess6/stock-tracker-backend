"""Server-side trend helpers — YoY, QoQ, CAGR, margin deltas."""

from __future__ import annotations

from typing import Any, Callable


def yoy_pct(current: float | None, prior: float | None) -> float | None:
    """Percent change between two values (e.g. 10.0 = +10%)."""
    if current is None or prior is None:
        return None
    cur = float(current)
    prev = float(prior)
    if not _finite(cur) or not _finite(prev) or prev == 0:
        return None
    return ((cur - prev) / abs(prev)) * 100.0


def qoq_pct(current: float | None, prior: float | None) -> float | None:
    return yoy_pct(current, prior)


def cagr_pct(start: float | None, end: float | None, years: float | None) -> float | None:
    """Compound annual growth rate as percent."""
    if start is None or end is None or years is None:
        return None
    s = float(start)
    e = float(end)
    n = float(years)
    if not _finite(s) or not _finite(e) or not _finite(n) or n <= 0:
        return None
    if s <= 0 or e <= 0:
        return None
    return (pow(e / s, 1.0 / n) - 1.0) * 100.0


def margin_delta(current_margin: float | None, prior_margin: float | None) -> float | None:
    if current_margin is None or prior_margin is None:
        return None
    cur = float(current_margin)
    prev = float(prior_margin)
    if not _finite(cur) or not _finite(prev):
        return None
    return cur - prev


def trend_summary(values_newest_first: list[float | None]) -> dict[str, float | None]:
    """Build YoY and CAGR summaries from a newest-first value series."""
    vals = list(values_newest_first)
    summary: dict[str, float | None] = {
        "yoy": yoy_pct(vals[0], vals[1]) if len(vals) > 1 else None,
        "qoq": yoy_pct(vals[0], vals[1]) if len(vals) > 1 else None,
    }
    for years in (3, 5):
        if len(vals) > years:
            summary[f"cagr{years}y"] = cagr_pct(vals[years], vals[0], years)
        else:
            summary[f"cagr{years}y"] = None
    return summary


def build_metric_trends(
    periods_newest_first: list[dict],
    metric_keys: list[str],
    *,
    value_reader: Callable[[dict, str], float | None] | None = None,
) -> dict[str, dict[str, float | None]]:
    """
    Compute trend summaries per API metric key from research period payloads.
    periods_newest_first: items with `metrics` dict (camelCase API keys).
    """
    reader = value_reader or (lambda period, key: (period.get("metrics") or {}).get(key))
    trends: dict[str, dict[str, float | None]] = {}
    for key in metric_keys:
        series = [reader(period, key) for period in periods_newest_first]
        if not any(v is not None for v in series):
            continue
        trends[key] = trend_summary(series)
    return trends


def _finite(value: float) -> bool:
    return value == value and abs(value) != float("inf")
