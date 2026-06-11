"""Tests for Phase 1 non-compensatory gate stack."""

from __future__ import annotations

from app.services.gate_engine import (
    evaluate_accounting_integrity,
    evaluate_gate_stack,
    evaluate_margin_of_safety,
    evaluate_secular_decline,
    evaluate_solvency_runway,
    summarize_gate_stack,
)
from app.services.metric_primitives import (
    BENEISH_MANIPULATION_THRESHOLD,
    GATE_RUNWAY_PASS_MONTHS,
    SLOAN_ACCRUALS_HIGH_THRESHOLD,
)
from app.services.verification_spec import GOLDEN_CURRENT_ROW, GOLDEN_PRIOR_ROW


def _base_inputs(**overrides):
    payload = {
        "ticker": "TEST",
        "scores": {
            "survivability": 90.0,
            "survivability_bucket": "strong",
            "beneish_m": -2.5,
        },
        "latent": {
            "runway_months": 24.0,
            "price_to_conservative_nav": 0.75,
            "conservative_nav_per_share": 40.0,
            "time_cheap_periods": 1,
            "time_cheap_classification": "recent",
            "peer_industry_declining": False,
            "sloan_accruals": 0.03,
            "raw": {"peer_industry_trend": {"peer_count": 5}},
        },
        "metrics": {
            "pe": 18.0,
            "pb": 3.0,
            "earnings_yield": 0.05,
            "current_ratio": 1.5,
            "cash_to_debt": 0.8,
        },
        "derived": {
            "fcf": 100.0,
            "fcf_yield": 0.09,
            "owner_earnings_yield": 0.11,
            "fcf_positive_streak": 2,
            "interest_coverage": 5.0,
        },
        "edgar_triggers": {
            "going_concern": False,
            "nt_filing": False,
            "restatement": False,
            "auditor_change_12m": False,
        },
        "operational_recovery": {"operationalRecovery": True, "periodsReviewed": 4},
        "margin_trends": {"gross_margin_3yr_delta": 0.02, "operating_margin_3yr_delta": 0.01},
        "narrative_states": [],
        "price": 30.0,
        "row": GOLDEN_CURRENT_ROW,
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            merged = dict(payload[key])
            merged.update(value)
            payload[key] = merged
        else:
            payload[key] = value
    return payload


def test_all_gates_pass_on_healthy_inputs():
    gates = evaluate_gate_stack(_base_inputs())
    assert [gate["status"] for gate in gates] == ["pass", "pass", "pass", "pass"]
    summary = summarize_gate_stack(gates)
    assert summary["allPassed"] is True
    assert summary["skipPillars"] is False


def test_gate_stack_deterministic():
    inputs = _base_inputs()
    first = evaluate_gate_stack(inputs)
    second = evaluate_gate_stack(inputs)
    assert first == second


def test_solvency_runway_passes_on_strong_survivability_and_fcf():
    result = evaluate_solvency_runway(
        _base_inputs(
            latent={"runway_months": None},
            derived={"fcf": 50.0, "interest_coverage": 1.2},
        )
    )
    assert result["status"] == "pass"
    assert result["triggered_by"] == "survivability_strong_positive_fcf"


def test_solvency_runway_fails_on_going_concern():
    result = evaluate_solvency_runway(
        _base_inputs(edgar_triggers={"going_concern": True})
    )
    assert result["status"] == "fail"
    assert result["triggered_by"] == "going_concern_opinion"


def test_solvency_runway_unknown_without_data():
    result = evaluate_solvency_runway(
        _base_inputs(
            scores={"survivability": None, "survivability_bucket": None},
            latent={"runway_months": None},
            derived={"fcf": None, "interest_coverage": None},
        )
    )
    assert result["status"] == "unknown"


def test_solvency_runway_fails_distressed_profile():
    result = evaluate_solvency_runway(
        _base_inputs(
            scores={"survivability": 20.0, "survivability_bucket": "critical"},
            latent={"runway_months": 8.0},
            derived={"fcf": -20.0, "interest_coverage": 0.6, "fcf_yield": None, "owner_earnings_yield": None, "fcf_positive_streak": 0},
        )
    )
    assert result["status"] == "fail"
    assert "runway_below_18_months" in result["evidence"]["watchlistFlags"]


def test_accounting_integrity_fails_beneish_hard_trigger():
    result = evaluate_accounting_integrity(
        _base_inputs(scores={"beneish_m": BENEISH_MANIPULATION_THRESHOLD + 0.1})
    )
    assert result["status"] == "fail"
    assert result["triggered_by"] == "beneish_manipulation_probable"


def test_accounting_integrity_fails_restatement():
    result = evaluate_accounting_integrity(
        _base_inputs(edgar_triggers={"restatement": True})
    )
    assert result["status"] == "fail"
    assert result["triggered_by"] == "restatement_item_4_02"


def test_accounting_integrity_unknown_missing_beneish_and_sloan():
    result = evaluate_accounting_integrity(
        _base_inputs(
            scores={"beneish_m": None},
            latent={"sloan_accruals": None},
        )
    )
    assert result["status"] == "unknown"


def test_accounting_integrity_passes_clean_beneish_and_sloan():
    result = evaluate_accounting_integrity(_base_inputs())
    assert result["status"] == "pass"
    assert result["triggered_by"] == "beneish_and_sloan_clean"


def test_accounting_integrity_fails_high_sloan():
    result = evaluate_accounting_integrity(
        _base_inputs(latent={"sloan_accruals": SLOAN_ACCRUALS_HIGH_THRESHOLD + 0.01})
    )
    assert result["status"] == "fail"
    assert result["triggered_by"] == "high_sloan_accruals"


def test_secular_decline_fails_structural_cheapness_with_peer_decline():
    result = evaluate_secular_decline(
        _base_inputs(
            latent={
                "time_cheap_periods": 6,
                "time_cheap_classification": "structural",
                "peer_industry_declining": True,
                "raw": {"peer_industry_trend": {"peer_count": 8}},
            },
            operational_recovery={"operationalRecovery": False, "periodsReviewed": 4},
            margin_trends={"gross_margin_3yr_delta": -0.05, "operating_margin_3yr_delta": -0.04},
        )
    )
    assert result["status"] == "fail"
    assert result["triggered_by"] == "structural_cheapness_with_peer_decline"


def test_secular_decline_unknown_insufficient_peer_history():
    result = evaluate_secular_decline(
        _base_inputs(
            latent={
                "peer_industry_declining": None,
                "raw": {"peer_industry_trend": {"peer_count": 0}},
            }
        )
    )
    assert result["status"] == "unknown"
    assert result["triggered_by"] == "insufficient_peer_history"


def test_secular_decline_passes_operational_recovery():
    result = evaluate_secular_decline(
        _base_inputs(
            latent={
                "time_cheap_periods": 6,
                "time_cheap_classification": "structural",
                "peer_industry_declining": True,
                "raw": {"peer_industry_trend": {"peer_count": 8}},
            },
            operational_recovery={"operationalRecovery": True, "periodsReviewed": 4},
        )
    )
    assert result["status"] == "pass"
    assert result["triggered_by"] == "operational_recovery_evidence"


def test_margin_of_safety_passes_nav_discount():
    result = evaluate_margin_of_safety(
        _base_inputs(
            latent={"price_to_conservative_nav": 0.8, "conservative_nav_per_share": 50.0},
            derived={"owner_earnings_yield": None, "fcf_yield": None, "fcf_positive_streak": 0},
        )
    )
    assert result["status"] == "pass"
    assert result["triggered_by"] == "price_below_conservative_nav"


def test_margin_of_safety_fails_multiples_only_above_nav():
    result = evaluate_margin_of_safety(
        _base_inputs(
            metrics={"pe": 8.0, "pb": 0.7, "earnings_yield": 0.12},
            latent={"price_to_conservative_nav": 1.25, "conservative_nav_per_share": 20.0},
            derived={"owner_earnings_yield": 0.03, "fcf_yield": 0.02, "fcf_positive_streak": 0},
        )
    )
    assert result["status"] == "fail"
    assert result["triggered_by"] == "multiples_only_cheapness_above_haircut_nav"


def test_margin_of_safety_unknown_missing_anchors():
    result = evaluate_margin_of_safety(
        _base_inputs(
            metrics={"pe": None, "pb": None, "earnings_yield": None},
            latent={"price_to_conservative_nav": None, "conservative_nav_per_share": None},
            derived={"owner_earnings_yield": None, "fcf_yield": None, "fcf_positive_streak": 0},
        )
    )
    assert result["status"] == "unknown"


def test_gate_failure_suppresses_pillar_analysis():
    gates = evaluate_gate_stack(
        _base_inputs(
            scores={"survivability": 15.0, "survivability_bucket": "critical", "beneish_m": -2.5},
            latent={"runway_months": 6.0},
            derived={"fcf": -10.0, "interest_coverage": 0.5, "fcf_positive_streak": 0},
        )
    )
    summary = summarize_gate_stack(gates)
    assert summary["skipPillars"] is True
    assert "solvency_runway" in summary["failedGates"]


def test_unknown_gate_does_not_block_investable():
    gates = [
        {"gate": "solvency_runway", "status": "pass", "evidence": {}, "triggered_by": "runway"},
        {"gate": "accounting_integrity", "status": "unknown", "evidence": {}, "triggered_by": "missing"},
        {"gate": "secular_decline", "status": "pass", "evidence": {}, "triggered_by": "ok"},
        {"gate": "margin_of_safety", "status": "pass", "evidence": {}, "triggered_by": "nav"},
    ]
    summary = summarize_gate_stack(gates)
    assert summary["investable"] is True
    assert summary["unknownGates"] == ["accounting_integrity"]


def test_auditor_change_within_12_months_fails_accounting_gate():
    result = evaluate_accounting_integrity(
        _base_inputs(
            edgar_triggers={
                "going_concern": False,
                "nt_filing": False,
                "restatement": False,
                "auditor_change_12m": True,
            }
        )
    )
    assert result["status"] == "fail"
    assert result["triggered_by"] == "auditor_change_12m"


def test_research_gates_route_not_found(app, client):
    response = client.get("/api/research/gates/ZZZZ")
    assert response.status_code == 404
    payload = response.get_json()
    assert payload["error"] == "not_found"


def test_research_gates_route_shape(app, client):
    from app.db import get_db
    from app.repositories import Repository

    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "GATE1", "name": "Gate Test Co", "cik": "0000000001"}])
        company = repo.get_company_by_ticker("GATE1")
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
                {
                    "company_id": company["id"],
                    "metric": "netinc",
                    "value": 120.0,
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
                    "xbrl_concept": "NetIncomeLoss",
                },
            ]
        )

    response = client.get("/api/research/gates/GATE1")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ticker"] == "GATE1"
    assert len(payload["gates"]) == 4
    assert "summary" in payload
    assert "investable" in payload["summary"]
    assert payload["meta"]["source"] == "sqlite"


def test_golden_fixture_scores_produce_passing_solvency_gate():
    from app.services.scoring import compute_scores_for_periods

    records = compute_scores_for_periods(
        [GOLDEN_CURRENT_ROW, GOLDEN_PRIOR_ROW],
        prices_by_period={"2024-12-31": 20.0, "2023-12-31": 15.0},
    )
    latest = records[0]
    result = evaluate_solvency_runway(
        _base_inputs(
            scores={
                "survivability": latest["survivability"],
                "survivability_bucket": latest.get("survivability_bucket"),
                "beneish_m": latest["beneish_m"],
            },
            latent={"runway_months": GATE_RUNWAY_PASS_MONTHS + 1},
            derived={"fcf": 100.0, "interest_coverage": 8.0, "fcf_positive_streak": 2},
        )
    )
    assert result["status"] == "pass"
