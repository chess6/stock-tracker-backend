"""Tests for Phase 0 latent thesis metrics (A1–A6)."""

from app.services.latent_metrics import latent_metrics_to_api
from app.services.metric_primitives import (
    capital_allocation_track_record,
    conservative_nav_per_share,
    conservative_nav_total,
    peer_industry_secular_trend,
    price_to_conservative_nav,
    quarterly_cash_runway_months,
    sloan_accruals,
    time_cheap_persistence,
)
from app.services.metric_registry import METRIC_REGISTRY


def _balance_sheet_row(**overrides):
    base = {
        "calendardate": "2024-12-31",
        "cashneq": 100.0,
        "receivables": 200.0,
        "inventory": 80.0,
        "ppnenet": 400.0,
        "assetscurrent": 500.0,
        "goodwill": 150.0,
        "intangibles": 50.0,
        "liabilities": 600.0,
        "sharesbas": 100.0,
        "assets": 1200.0,
        "netinc": 50.0,
        "ncfo": 30.0,
        "ncfcommon": -20.0,
        "ncfdiv": -10.0,
        "retearn": 300.0,
        "revenue": 1000.0,
        "cor": 600.0,
    }
    base.update(overrides)
    return base


def test_conservative_nav_haircuts():
    row = _balance_sheet_row()
    nav = conservative_nav_total(row)
    assert nav is not None
    # cash 100 + recv*0.8 160 + inv*0.5 40 + ppne*0.375 150 + other_current 120 - liabilities 600
    expected_assets = 100 + 160 + 40 + 150 + 120
    assert nav == expected_assets - 600
    nav_ps = conservative_nav_per_share(row)
    assert nav_ps == nav / 100.0
    assert price_to_conservative_nav(50.0, nav_ps) == 50.0 / nav_ps


def test_sloan_accruals_with_prior():
    row = _balance_sheet_row(netinc=80.0, ncfo=40.0, assets=1000.0)
    prior = _balance_sheet_row(netinc=70.0, ncfo=35.0, assets=900.0)
    sloan = sloan_accruals(row, prior)
    avg_assets = (1000.0 + 900.0) / 2.0
    assert sloan == (80.0 - 40.0) / avg_assets


def test_quarterly_runway_months_burn():
    row = {
        "cashneq": 90.0,
        "ncfo": -30.0,
        "capex": 0.0,
    }
    # quarterly burn 30 → monthly 10 → 9 months runway
    assert quarterly_cash_runway_months(row) == 9.0


def test_quarterly_runway_positive_fcf_returns_none():
    row = {"cashneq": 100.0, "ncfo": 50.0, "capex": 10.0}
    assert quarterly_cash_runway_months(row) is None


def test_time_cheap_structural_classification():
    history = []
    for year in range(2024, 2018, -1):
        history.append(
            {
                "calendardate": f"{year}-12-31",
                "pe": 8.0,
                "pb": 0.6,
                "earnings_yield": 0.12,
            }
        )
    result = time_cheap_persistence(history)
    assert result["consecutive_periods"] >= 5
    assert result["classification"] == "structural"


def test_peer_industry_secular_trend_declining():
    def history(revenues, margins):
        rows = []
        for idx, (rev, gm) in enumerate(zip(revenues, margins)):
            rows.append(
                {
                    "calendardate": f"{2024 - idx}-12-31",
                    "revenue": rev,
                    "gp": rev * gm,
                    "cor": rev * (1 - gm),
                }
            )
        return rows

    peer_a = history([850, 900, 950, 1000], [0.24, 0.26, 0.28, 0.30])
    peer_b = history([740, 760, 780, 800], [0.22, 0.23, 0.24, 0.25])
    trend = peer_industry_secular_trend([peer_a, peer_b])
    assert trend["peer_count"] == 2
    assert trend["median_revenue_cagr_3yr"] is not None
    assert trend["median_revenue_cagr_3yr"] < 0
    assert trend["peer_declining"] is True


def test_capital_allocation_track_record():
    rows = [
        _balance_sheet_row(calendardate="2024-12-31", sharesbas=110.0, ncfcommon=-30.0, retearn=350.0),
        _balance_sheet_row(calendardate="2023-12-31", sharesbas=105.0, ncfcommon=0.0),
        _balance_sheet_row(calendardate="2022-12-31", sharesbas=100.0, ncfcommon=0.0),
        _balance_sheet_row(calendardate="2021-12-31", sharesbas=100.0, retearn=250.0),
    ]
    prices = {"2024-12-31": 5.0}
    cap = capital_allocation_track_record(rows, prices_by_period=prices)
    assert cap["score"] is not None
    assert cap["buyback_at_discount_pct"] == 1.0
    assert cap["dilution_rate_3yr"] is not None


def test_latent_metrics_registry_thesis_category():
    thesis_keys = [k for k, v in METRIC_REGISTRY.items() if v.get("category") == "thesis"]
    assert "conservative_nav_per_share" in thesis_keys
    assert "sloan_accruals" in thesis_keys
    assert len(thesis_keys) >= 6


def test_latent_metrics_to_api_shape():
    payload = {
        "conservative_nav_per_share": 10.0,
        "price_to_conservative_nav": 0.8,
        "time_cheap_periods": 3,
        "runway_months": 18.0,
        "capital_allocation_score": 65.0,
        "sloan_accruals": 0.05,
        "raw": {"conservative_nav": {"navPerShare": 10.0}},
    }
    api = latent_metrics_to_api(payload)
    assert api["conservativeNavPerShare"] == 10.0
    assert api["priceToConservativeNav"] == 0.8
    assert api["timeCheapPeriods"] == 3
    assert api["raw"]["conservative_nav"]["navPerShare"] == 10.0
