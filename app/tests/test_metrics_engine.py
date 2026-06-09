"""Tests for canonical metrics engine and registry."""

from app.services.metric_primitives import gross_margin, operating_margin, total_debt
from app.services.metric_registry import METRIC_REGISTRY, canonical_key, registry_for_api
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
    entries = registry_for_api()
    assert len(entries) == len(METRIC_REGISTRY)
    gross = next(item for item in entries if item["key"] == "gross_margin")
    assert gross["api_key"] == "grossMargin"
    assert gross["higher_is_better"] is True


def test_gross_margin_derives_from_cor():
    row = {"revenue": 100.0, "cor": 60.0}
    assert gross_margin(row) == 0.4
    assert operating_margin({"revenue": 100.0, "opinc": 10.0}) == 0.1


def test_metrics_registry_route(client):
    response = client.get("/api/research/metrics/registry")
    assert response.status_code == 200
    payload = response.get_json()
    assert "metrics" in payload
    assert any(item["key"] == "gross_margin" for item in payload["metrics"])
