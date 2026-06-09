"""Canonical metric registry — metadata for engine, API, and UI heatmaps."""

from __future__ import annotations

from typing import Any, TypedDict


class MetricDef(TypedDict, total=False):
    category: str
    label: str
    format: str
    api_key: str
    higher_is_better: bool
    heatmap_mode: str
    danger_threshold: float | None
    excellent_threshold: float | None
    screener_supported: bool
    time_series: bool
    trend_capable: bool


METRIC_REGISTRY: dict[str, MetricDef] = {
    "gross_margin": {
        "category": "profitability",
        "label": "Gross Margin",
        "format": "percent",
        "api_key": "grossMargin",
        "higher_is_better": True,
        "heatmap_mode": "percentile",
        "danger_threshold": 0.0,
        "excellent_threshold": 0.50,
        "screener_supported": True,
        "time_series": True,
        "trend_capable": True,
    },
    "operating_margin": {
        "category": "profitability",
        "label": "Operating Margin",
        "format": "percent",
        "api_key": "operatingMargin",
        "higher_is_better": True,
        "heatmap_mode": "percentile",
        "danger_threshold": 0.0,
        "excellent_threshold": 0.25,
        "screener_supported": False,
        "time_series": True,
        "trend_capable": True,
    },
    "ebitda_margin": {
        "category": "profitability",
        "label": "EBITDA Margin",
        "format": "percent",
        "api_key": "ebitdaMargin",
        "higher_is_better": True,
        "heatmap_mode": "percentile",
        "danger_threshold": 0.0,
        "excellent_threshold": 0.30,
        "screener_supported": False,
        "time_series": True,
        "trend_capable": True,
    },
    "net_margin": {
        "category": "profitability",
        "label": "Net Margin",
        "format": "percent",
        "api_key": "netMargin",
        "higher_is_better": True,
        "heatmap_mode": "percentile",
        "danger_threshold": 0.0,
        "excellent_threshold": 0.20,
        "screener_supported": True,
        "time_series": True,
        "trend_capable": True,
    },
    "fcf_margin": {
        "category": "profitability",
        "label": "FCF Margin",
        "format": "percent",
        "api_key": "fcfMargin",
        "higher_is_better": True,
        "heatmap_mode": "percentile",
        "danger_threshold": 0.0,
        "excellent_threshold": 0.15,
        "screener_supported": False,
        "time_series": True,
        "trend_capable": True,
    },
    "cfo_margin": {
        "category": "profitability",
        "label": "CFO Margin",
        "format": "percent",
        "api_key": "cfoMargin",
        "higher_is_better": True,
        "heatmap_mode": "percentile",
        "danger_threshold": 0.0,
        "excellent_threshold": 0.20,
        "screener_supported": False,
        "time_series": True,
        "trend_capable": True,
    },
    "roa": {
        "category": "profitability",
        "label": "ROA",
        "format": "percent",
        "api_key": "roa",
        "higher_is_better": True,
        "heatmap_mode": "percentile",
        "danger_threshold": 0.0,
        "excellent_threshold": 0.10,
        "screener_supported": True,
        "time_series": True,
        "trend_capable": True,
    },
    "roe": {
        "category": "profitability",
        "label": "ROE",
        "format": "percent",
        "api_key": "roe",
        "higher_is_better": True,
        "heatmap_mode": "percentile",
        "danger_threshold": 0.0,
        "excellent_threshold": 0.15,
        "screener_supported": True,
        "time_series": True,
        "trend_capable": True,
    },
    "pe": {
        "category": "valuation",
        "label": "P/E",
        "format": "decimal",
        "api_key": "pe",
        "higher_is_better": False,
        "heatmap_mode": "fixed_threshold",
        "danger_threshold": 40.0,
        "excellent_threshold": 8.0,
        "screener_supported": True,
        "time_series": True,
        "trend_capable": False,
    },
    "pb": {
        "category": "valuation",
        "label": "P/B",
        "format": "decimal",
        "api_key": "pb",
        "higher_is_better": False,
        "heatmap_mode": "fixed_threshold",
        "danger_threshold": 3.0,
        "excellent_threshold": 0.7,
        "screener_supported": False,
        "time_series": True,
        "trend_capable": False,
    },
    "ebitda_ev": {
        "category": "valuation",
        "label": "EBITDA/EV",
        "format": "decimal",
        "api_key": "ebitdaEv",
        "higher_is_better": True,
        "heatmap_mode": "percentile",
        "danger_threshold": 0.05,
        "excellent_threshold": 0.20,
        "screener_supported": False,
        "time_series": True,
        "trend_capable": False,
    },
    "earnings_yield": {
        "category": "valuation",
        "label": "Earnings Yield",
        "format": "percent",
        "api_key": "earningsYield",
        "higher_is_better": True,
        "heatmap_mode": "percentile",
        "screener_supported": False,
        "time_series": True,
        "trend_capable": False,
    },
    "current_ratio": {
        "category": "liquidity",
        "label": "Current Ratio",
        "format": "decimal",
        "api_key": "currentRatio",
        "higher_is_better": True,
        "heatmap_mode": "fixed_threshold",
        "danger_threshold": 1.0,
        "excellent_threshold": 2.5,
        "screener_supported": False,
        "time_series": True,
        "trend_capable": True,
    },
    "quick_ratio": {
        "category": "liquidity",
        "label": "Quick Ratio",
        "format": "decimal",
        "api_key": "quickRatio",
        "higher_is_better": True,
        "heatmap_mode": "fixed_threshold",
        "danger_threshold": 0.8,
        "excellent_threshold": 2.0,
        "screener_supported": False,
        "time_series": True,
        "trend_capable": False,
    },
    "debt_equity": {
        "category": "liquidity",
        "label": "Debt/Equity",
        "format": "decimal",
        "api_key": "de",
        "higher_is_better": False,
        "heatmap_mode": "fixed_threshold",
        "danger_threshold": 2.0,
        "excellent_threshold": 0.5,
        "screener_supported": True,
        "time_series": True,
        "trend_capable": True,
    },
    "debt_assets": {
        "category": "liquidity",
        "label": "Debt/Assets",
        "format": "decimal",
        "api_key": "debtAssets",
        "higher_is_better": False,
        "heatmap_mode": "fixed_threshold",
        "danger_threshold": 0.6,
        "excellent_threshold": 0.2,
        "screener_supported": False,
        "time_series": True,
        "trend_capable": False,
    },
    "interest_coverage": {
        "category": "liquidity",
        "label": "Interest Coverage",
        "format": "decimal",
        "api_key": "interestCoverage",
        "higher_is_better": True,
        "heatmap_mode": "fixed_threshold",
        "danger_threshold": 1.0,
        "excellent_threshold": 8.0,
        "screener_supported": False,
        "time_series": True,
        "trend_capable": False,
    },
    "cash_to_debt": {
        "category": "liquidity",
        "label": "Cash/Debt",
        "format": "decimal",
        "api_key": "cashToDebt",
        "higher_is_better": True,
        "heatmap_mode": "fixed_threshold",
        "danger_threshold": 0.25,
        "excellent_threshold": 1.5,
        "screener_supported": False,
        "time_series": True,
        "trend_capable": False,
    },
    "piotroski_f": {
        "category": "score",
        "label": "Piotroski F",
        "format": "integer",
        "api_key": "piotroskiF",
        "higher_is_better": True,
        "heatmap_mode": "score_tier",
        "screener_supported": True,
        "time_series": True,
        "trend_capable": False,
    },
    "altman_z": {
        "category": "score",
        "label": "Altman Z",
        "format": "decimal",
        "api_key": "altmanZ",
        "higher_is_better": True,
        "heatmap_mode": "score_tier",
        "screener_supported": True,
        "time_series": True,
        "trend_capable": False,
    },
    "beneish_m": {
        "category": "score",
        "label": "Beneish M",
        "format": "decimal",
        "api_key": "beneishM",
        "higher_is_better": False,
        "heatmap_mode": "score_tier",
        "screener_supported": True,
        "time_series": True,
        "trend_capable": False,
    },
    "survivability": {
        "category": "score",
        "label": "Survivability",
        "format": "integer",
        "api_key": "survivability",
        "higher_is_better": True,
        "heatmap_mode": "score_tier",
        "screener_supported": True,
        "time_series": True,
        "trend_capable": False,
    },
}

_API_TO_CANONICAL: dict[str, str] = {
    meta["api_key"]: key for key, meta in METRIC_REGISTRY.items() if meta.get("api_key")
}


def registry_for_api() -> list[dict[str, Any]]:
    """Serialize registry for JSON API."""
    items = []
    for key, meta in METRIC_REGISTRY.items():
        items.append({"key": key, **meta})
    return items


def canonical_key(api_key: str) -> str | None:
    return _API_TO_CANONICAL.get(api_key)


def api_key_for(canonical: str) -> str | None:
    meta = METRIC_REGISTRY.get(canonical)
    return meta.get("api_key") if meta else None
