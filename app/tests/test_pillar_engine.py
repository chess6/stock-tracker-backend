"""Tests for Phase 2 pillar dimension dashboard."""

from __future__ import annotations

from app.services.gate_engine import evaluate_gate_stack, summarize_gate_stack
from app.services.pillar_engine import (
    PILLAR_KEYS,
    evaluate_pillars,
    evaluate_pillars_for_ticker,
    pillars_to_api,
)
from app.tests.test_gate_engine import _base_inputs


def _pillar_candidate(**overrides):
    payload = {
        "ticker": "TEST",
        "sector": "Technology",
        "price": 30.0,
        "scores": {
            "survivability": 85.0,
            "altmanZ": 3.2,
            "beneishM": -2.5,
        },
        "metrics": {
            "pe": 12.0,
            "pb": 1.1,
            "earnings_yield": 0.08,
            "interest_coverage": 4.0,
            "fcfMargin": 0.12,
        },
        "latent": {
            "price_to_conservative_nav": 0.8,
            "conservative_nav_per_share": 40.0,
            "runway_months": 24.0,
            "peer_industry_declining": False,
            "capital_allocation_score": 65.0,
            "sloan_accruals": 0.02,
            "raw": {
                "capital_allocation": {
                    "buyback_at_discount_pct": 0.6,
                    "dilution_rate_3yr": -0.01,
                    "dividend_fcf_coverage": 1.2,
                    "equity_raises_vs_retained_earnings": 50_000_000.0,
                },
                "peer_industry_trend": {"peer_count": 6},
            },
        },
        "derived": {
            "gross_margin_trend": 0.03,
            "operating_margin_trend": 0.02,
            "fcf_yield": 0.09,
            "owner_earnings_yield": 0.11,
            "gross_margin_stability": 0.04,
            "revenue_trajectory": 0.05,
            "dilution_rate": -0.01,
            "altman_delta": 0.2,
            "de_delta": -0.05,
        },
        "insider": {"buy6m": 1_500_000.0},
        "insider_cluster": {"intensity_score": 0.7, "buy_count": 4},
        "narrative": {"divergence_score": 0.72, "divergence_signal": "rerating_candidate"},
        "narrative_states": [{"state": "turnaround_optimism", "score": 0.6}],
        "operational_recovery": {"marginRecovery": True, "grossMarginDelta": 0.02},
        "edgar": {},
        "annual_rows": [],
        "_sector_stats": {"bySector": {}},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            merged = dict(payload[key])
            merged.update(value)
            payload[key] = merged
        else:
            payload[key] = value
    return payload


def test_eight_independent_pillars_no_cross_pillar_sum():
    pillars = evaluate_pillars(_pillar_candidate())
    assert len(pillars) == 8
    assert [pillar["pillar"] for pillar in pillars] == list(PILLAR_KEYS)
    assert all("score" in pillar or pillar["tier"] == "unknown" for pillar in pillars)
    assert not any("compositeScore" in pillar for pillar in pillars)
    assert not any("rollup" in str(pillar).lower() for pillar in pillars)


def test_pillar_factors_preserve_contract_shape():
    pillars = evaluate_pillars(_pillar_candidate())
    valuation = next(item for item in pillars if item["pillar"] == "valuation")
    assert valuation["factorsTotal"] >= 1
    for factor in valuation["factors"]:
        assert {"key", "weight", "normalized", "contribution", "raw"} <= set(factor.keys())


def test_gate_failure_skips_pillars():
    gates = evaluate_gate_stack(
        _base_inputs(
            scores={"survivability": 15.0, "survivability_bucket": "critical", "beneish_m": -2.5},
            latent={"runway_months": 6.0},
            derived={"fcf": -10.0, "interest_coverage": 0.5, "fcf_positive_streak": 0},
        )
    )
    summary = summarize_gate_stack(gates)
    assert summary["skipPillars"] is True

    payload = {
        "ticker": "TEST",
        "skipped": True,
        "skipReason": "gate_failure",
        "failedGates": summary["failedGates"],
        "gates": gates,
        "pillars": [],
    }
    api = pillars_to_api(payload)
    assert api["skipped"] is True
    assert api["pillars"] == []


def test_pillars_deterministic():
    candidate = _pillar_candidate()
    first = evaluate_pillars(candidate)
    second = evaluate_pillars(candidate)
    assert first == second


def test_research_pillars_route_shape(app, client):
    from app.db import get_db
    from app.repositories import Repository

    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "PILR1", "name": "Pillar Test", "cik": "0000000099"}])
        company = repo.get_company_by_ticker("PILR1")
        repo.upsert_fundamentals(
            [
                {
                    "company_id": company["id"],
                    "metric": "revenue",
                    "value": 1000.0,
                    "unit": "USD",
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                    "form": "10-K",
                    "accession": "1",
                    "source": "sec_companyfacts",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "Revenues",
                },
            ]
        )

    response = client.get("/api/research/pillars/PILR1")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ticker"] == "PILR1"
    assert "pillars" in payload
    assert "skipped" in payload


def test_evaluate_pillars_for_ticker_not_found(app):
    from app.db import get_db
    from app.repositories import Repository
    from app.services.prices import PricesService

    with app.app_context():
        repo = Repository(get_db())
        result = evaluate_pillars_for_ticker(repo, "ZZZZZ", prices_service=PricesService(repo))
        assert result is None
