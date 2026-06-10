"""Sector percentile distributions for heatmap sector mode."""

from __future__ import annotations

from typing import Any

from ..repositories import Repository
from .fundamentals import fetch_resolved_wide_rows, resolve_financial_dimension
from .metric_registry import METRIC_REGISTRY
from .metrics_engine import build_company_metrics
def _finite(value: float) -> bool:
    return value == value and abs(value) != float("inf")


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = (len(sorted_vals) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def percentile_breakpoints(values: list[float]) -> dict[str, Any]:
    nums = sorted(v for v in values if _finite(v))
    if not nums:
        return {"count": 0, "min": None, "max": None}
    return {
        "count": len(nums),
        "min": nums[0],
        "max": nums[-1],
        "p20": _percentile(nums, 0.2),
        "p40": _percentile(nums, 0.4),
        "p60": _percentile(nums, 0.6),
        "p80": _percentile(nums, 0.8),
        "p95": _percentile(nums, 0.95),
    }


def _default_metric_api_keys() -> list[str]:
    return [
        meta["api_key"]
        for meta in METRIC_REGISTRY.values()
        if meta.get("api_key") and meta.get("screener_supported")
    ]


def build_sector_stats(
    repo: Repository,
    *,
    sectors: list[str] | None = None,
    metric_api_keys: list[str] | None = None,
    peers_per_sector: int = 150,
) -> dict[str, Any]:
    """
    Aggregate latest ARY metrics by sector for percentile heatmaps.
    Returns { bySector: { sector: { metricKey: breakpoints } }, meta: {...} }.
    """
    keys = metric_api_keys or _default_metric_api_keys()
    target_sectors = [s.strip() for s in (sectors or []) if s and s.strip()]
    if not target_sectors:
        rows = repo.conn.execute(
            """
            SELECT DISTINCT sector FROM companies
            WHERE sector IS NOT NULL AND TRIM(sector) != ''
            ORDER BY sector
            """,
        ).fetchall()
        target_sectors = [row["sector"] for row in rows]

    resolved = resolve_financial_dimension("ARY", most_recent=False)
    by_sector: dict[str, dict[str, dict]] = {}

    for sector in target_sectors:
        peer_tickers = repo.fetch_sector_tickers(sector, limit=peers_per_sector)
        if not peer_tickers:
            by_sector[sector] = {}
            continue
        wide_rows = fetch_resolved_wide_rows(repo, peer_tickers, gte=None, resolved=resolved)
        metric_values: dict[str, list[float]] = {key: [] for key in keys}
        for row in wide_rows:
            metrics = build_company_metrics(row, price=None)
            for key in keys:
                val = metrics.get(key)
                if val is not None and _finite(float(val)):
                    metric_values[key].append(float(val))
        by_sector[sector] = {
            key: percentile_breakpoints(metric_values[key])
            for key in keys
            if metric_values[key]
        }

    return {
        "bySector": by_sector,
        "meta": {
            "dimension": "ARY",
            "metricCount": len(keys),
            "sectorCount": len(by_sector),
            "peersPerSector": peers_per_sector,
        },
    }


def sector_stats_for_tickers(
    repo: Repository,
    tickers: list[str],
    *,
    metric_api_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Limit sector stats to sectors represented by the given tickers."""
    sectors: list[str] = []
    seen: set[str] = set()
    for ticker in tickers:
        company = repo.get_company_by_ticker(ticker)
        sector = (company or {}).get("sector")
        if sector and sector not in seen:
            seen.add(sector)
            sectors.append(sector)
    return build_sector_stats(repo, sectors=sectors, metric_api_keys=metric_api_keys)
