"""Tests for canonical metrics engine and registry."""

import pytest

from app.services.metric_primitives import gross_margin, gross_profit, operating_margin, total_debt
from app.services.metric_registry import canonical_key
from app.services.metrics_engine import build_company_metrics, compute_period_metrics


def _sample_row():
    return {
        "ticker": "TEST",
        "revenue": 1000.0,
        "gp": 400.0,
        "opinc": 150.0,
        "ebitda": 180.0,
        "netinc": 80.0,
        "ncfo": 120.0,
        "capex": 30.0,
        "assets": 2000.0,
        "equity": 800.0,
        "liabilities": 1200.0,
        "assetscurrent": 500.0,
        "liabilitiescurrent": 250.0,
        "inventory": 50.0,
        "debt": 300.0,
        "cashneq": 100.0,
        "sharesbas": 100.0,
        "eps": 0.80,
        "interestexp": 10.0,
        "ncfdiv": -20.0,
    }


def test_compute_period_metrics_margins():
    row = _sample_row()
    metrics = compute_period_metrics(row, price=20.0)
    assert metrics["gross_margin"] == 0.4
    assert metrics["operating_margin"] == 0.15
    assert metrics["ebitda_margin"] == 0.18
    assert metrics["net_margin"] == 0.08
    assert metrics["fcf_margin"] == 0.09
    assert metrics["cfo_margin"] == 0.12


def test_compute_period_metrics_valuation_and_liquidity():
    row = _sample_row()
    metrics = compute_period_metrics(row, price=20.0)
    assert metrics["pe"] == 25.0
    assert metrics["pb"] == 2.5
    assert metrics["market_cap"] == 2000.0
    assert metrics["interest_coverage"] == 15.0
    assert metrics["cash_to_debt"] == 100.0 / 300.0
    assert metrics["current_ratio"] == 2.0
    assert metrics["quick_ratio"] == (500.0 - 50.0) / 250.0


def test_build_company_metrics_preserves_api_contract():
    row = _sample_row()
    api = build_company_metrics(row, price=20.0)
    assert api["grossMargin"] == 0.4
    assert api["netMargin"] == 0.08
    assert api["de"] == 300.0 / 800.0
    assert api["tbp"] == api["bp"] == 8.0
    assert api["operatingMargin"] == 0.15
    assert api["fcfMargin"] == 0.09
    assert api["interestCoverage"] == 15.0


def test_metric_primitives_debt_fallback():
    row = {"debtcurrent": 50.0, "debtlt": 150.0}
    assert total_debt(row) == 200.0


def test_metric_registry_api_keys():
    assert canonical_key("grossMargin") == "gross_margin"
    assert canonical_key("unknown") is None


def test_gross_margin_derives_from_cor():
    row = {"revenue": 100.0, "cor": 60.0}
    assert gross_margin(row) == 0.4
    assert operating_margin({"revenue": 100.0, "opinc": 10.0}) == 0.1


def test_gross_margin_prefers_tagged_gp_when_coherent():
    row = {"revenue": 100.0, "cor": 60.0, "gp": 40.0}
    assert gross_margin(row) == 0.4


def test_gross_margin_rejects_gp_above_revenue():
    """SBAC-style mismatch: tagged GrossProfit with a narrow revenue concept."""
    row = {"revenue": 244.0, "cor": 691.0, "gp": 2124.0}
    assert gross_profit(row) is None
    assert gross_margin(row) is None


def test_gross_margin_rejects_cor_above_revenue():
    row = {"revenue": 100.0, "cor": 150.0}
    assert gross_margin(row) is None


def test_gross_margin_allows_high_software_margins():
    row = {"revenue": 23_769.0, "cor": 2_551.0, "gp": 21_218.0}
    assert gross_margin(row) == pytest.approx(21_218.0 / 23_769.0, rel=1e-6)


