"""Canonical verification fixtures and seed-ticker score ranges (Phase A P2)."""

from __future__ import annotations

from typing import Any

# Deterministic wide rows used by unit tests and validate_scores --mode fixture.
GOLDEN_CURRENT_ROW: dict[str, Any] = {
    "calendardate": "2024-12-31",
    "dimension": "ARY",
    "revenue": 1000.0,
    "gp": 400.0,
    "opinc": 200.0,
    "ebit": 200.0,
    "netinc": 120.0,
    "ncfo": 150.0,
    "assets": 2000.0,
    "assetscurrent": 800.0,
    "liabilities": 900.0,
    "liabilitiescurrent": 400.0,
    "equity": 1100.0,
    "debt": 300.0,
    "cashneq": 200.0,
    "retearn": 500.0,
    "workingcapital": 400.0,
    "receivables": 100.0,
    "ppnenet": 600.0,
    "depamor": 50.0,
    "sgna": 120.0,
    "interestexp": 10.0,
    "sharesbas": 100.0,
    "fcf": 100.0,
}

GOLDEN_PRIOR_ROW: dict[str, Any] = {
    **GOLDEN_CURRENT_ROW,
    "calendardate": "2023-12-31",
    "netinc": 50.0,
    "ncfo": 80.0,
    "debt": 500.0,
    "sharesbas": 110.0,
    "gp": 300.0,
    "revenue": 900.0,
    "assetscurrent": 700.0,
    "liabilitiescurrent": 500.0,
    "receivables": 90.0,
    "ppnenet": 550.0,
    "depamor": 45.0,
    "sgna": 110.0,
    "liabilities": 950.0,
}

GOLDEN_PRICES_BY_PERIOD = {"2024-12-31": 20.0, "2023-12-31": 15.0}

# Recomputed from GOLDEN_* rows; tolerances are ≤1% relative unless noted.
GOLDEN_EXPECTED_LATEST: dict[str, Any] = {
    "period_end": "2024-12-31",
    "piotroski_f": 9,
    "altman_z": 2.753333333333333,
    "beneish_m": -2.60146886295507,
    "survivability": 89.67,
}

GOLDEN_TOLERANCE: dict[str, dict[str, float]] = {
    "piotroski_f": {"rel": 0.0, "abs": 0.0},
    "altman_z": {"rel": 0.01, "abs": 0.01},
    "beneish_m": {"rel": 0.01, "abs": 0.02},
    "survivability": {"rel": 0.01, "abs": 0.5},
}

# Seed tickers — ranges pinned from local SQLite baseline (2026-06-09 recompute).
SEED_TICKER_SPECS: list[dict[str, Any]] = [
    {
        "ticker": "AAPL",
        "profile": "healthy_compounder",
        "latest_period": "2025-09-27",
        "metrics": {
            "piotroski_f": {"min": 6, "max": 8, "value": 7},
            "altman_z": {"min": 9.0, "max": 11.5, "value": 10.3815},
            "beneish_m": {"min": -2.8, "max": -2.0, "value": -2.2937},
            "survivability": {"min": 70.0, "max": 78.0, "value": 73.99},
        },
    },
    {
        "ticker": "GME",
        "profile": "volatile_turnaround",
        "latest_period": "2026-01-31",
        "metrics": {
            "piotroski_f": {"min": 6, "max": 8, "value": 7},
            "altman_z": {"min": 2.0, "max": 3.5, "value": 2.8271},
            "beneish_m": {"min": -4.0, "max": -3.2, "value": -3.7009},
            "survivability": {"min": 70.0, "max": 78.0, "value": 74.42},
        },
    },
    {
        "ticker": "MSFT",
        "profile": "large_compounder",
        "latest_period": "2025-06-30",
        "metrics": {
            "piotroski_f": {"min": 4, "max": 6, "value": 5},
            "altman_z": {"min": 9.0, "max": 10.5, "value": 9.8597},
            "beneish_m": {"min": -2.8, "max": -2.3, "value": -2.5708},
            "survivability": {"min": 90.0, "max": 98.0, "value": 94.76},
        },
    },
    {
        "ticker": "AMC",
        "profile": "distressed_small_cap",
        "latest_period": "2025-12-31",
        "metrics": {
            "piotroski_f": {"min": 2, "max": 4, "value": 3},
            "altman_z": {"min": -1.5, "max": -0.5, "value": -1.1104},
            "beneish_m": {"nullable": True},
            "survivability": {"min": 18.0, "max": 24.0, "value": 20.18},
        },
    },
]

DEFAULT_REL_TOLERANCE = 0.01


def score_within_range(value: float | int | None, spec: dict[str, Any]) -> bool:
    if value is None:
        return bool(spec.get("nullable"))
    if "min" not in spec or "max" not in spec:
        return value is None and spec.get("nullable")
    return spec["min"] <= value <= spec["max"]


def score_within_tolerance(
    actual: float | int | None,
    expected: float | int | None,
    *,
    rel_tol: float = DEFAULT_REL_TOLERANCE,
    abs_tol: float = 0.0,
) -> bool:
    if actual is None and expected is None:
        return True
    if actual is None or expected is None:
        return False
    delta = abs(float(actual) - float(expected))
    return delta <= max(abs_tol, abs(float(expected)) * rel_tol)
