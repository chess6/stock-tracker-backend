"""Tests for Phase 3 disconfirmation-first thesis engine."""

from __future__ import annotations

from app.services.gate_engine import evaluate_gate_stack, summarize_gate_stack
from app.services.pillar_engine import evaluate_pillars
from app.services.thesis_engine import build_thesis, evaluate_thesis_for_ticker, thesis_to_api
from app.tests.test_gate_engine import _base_inputs
from app.tests.test_pillar_engine import _pillar_candidate


def _thesis_inputs(**gate_overrides):
    gate_inputs = _base_inputs(**gate_overrides)
    gates = evaluate_gate_stack(gate_inputs)
    summary = summarize_gate_stack(gates)
    gate_payload = {"ticker": "TEST", "gates": gates, "summary": summary}
    pillars = evaluate_pillars(_pillar_candidate())
    pillar_payload = {
        "ticker": "TEST",
        "skipped": summary["skipPillars"],
        "pillars": [] if summary["skipPillars"] else pillars,
    }
    return gate_inputs, gate_payload, pillar_payload


def test_thesis_pre_mortem_leads():
    gate_inputs, gate_payload, pillar_payload = _thesis_inputs()
    thesis = build_thesis(
        ticker="TEST",
        gate_payload=gate_payload,
        gate_inputs=gate_inputs,
        pillar_payload=pillar_payload,
    )
    sections = thesis["sections"]
    assert sections["preMortem"]["headline"]
    assert sections["preMortem"]["statements"]


def test_disqualified_thesis_suppresses_bull_case():
    gate_inputs, gate_payload, pillar_payload = _thesis_inputs(
        scores={"survivability": 15.0, "survivability_bucket": "critical", "beneish_m": -2.5},
        latent={"runway_months": 6.0, "time_cheap_periods": 5, "time_cheap_classification": "structural"},
        derived={"fcf": -10.0, "interest_coverage": 0.5, "fcf_positive_streak": 0},
    )
    pillar_payload["skipped"] = True
    pillar_payload["pillars"] = []
    thesis = build_thesis(
        ticker="TEST",
        gate_payload=gate_payload,
        gate_inputs=gate_inputs,
        pillar_payload=pillar_payload,
    )
    assert thesis["disqualified"] is True
    assert thesis["sections"]["bullCase"] == []


def test_thesis_emits_minimum_two_disconfirming_conditions():
    gate_inputs, gate_payload, pillar_payload = _thesis_inputs()
    thesis = build_thesis(
        ticker="TEST",
        gate_payload=gate_payload,
        gate_inputs=gate_inputs,
        pillar_payload=pillar_payload,
    )
    conditions = thesis["sections"]["disconfirmingConditions"]
    assert len(conditions) >= 2
    for item in conditions:
        assert item.get("text")
        assert item.get("factorKey")


def test_thesis_evidence_coverage_and_signal_independence():
    gate_inputs, gate_payload, pillar_payload = _thesis_inputs()
    thesis = build_thesis(
        ticker="TEST",
        gate_payload=gate_payload,
        gate_inputs=gate_inputs,
        pillar_payload=pillar_payload,
    )
    coverage = thesis["sections"]["evidenceCoverage"]
    independence = thesis["sections"]["signalIndependence"]
    assert "overall" in coverage
    assert "orthogonalClassCount" in independence
    assert "confidence" not in str(independence).lower()


def test_bull_case_structured_as_rebuttals():
    gate_inputs, gate_payload, pillar_payload = _thesis_inputs()
    thesis = build_thesis(
        ticker="TEST",
        gate_payload=gate_payload,
        gate_inputs=gate_inputs,
        pillar_payload=pillar_payload,
    )
    bull = thesis["sections"]["bullCase"]
    if bull:
        assert "rebuttal" in bull[0]


def test_thesis_deterministic():
    gate_inputs, gate_payload, pillar_payload = _thesis_inputs()
    first = build_thesis(
        ticker="TEST",
        gate_payload=gate_payload,
        gate_inputs=gate_inputs,
        pillar_payload=pillar_payload,
    )
    second = build_thesis(
        ticker="TEST",
        gate_payload=gate_payload,
        gate_inputs=gate_inputs,
        pillar_payload=pillar_payload,
    )
    assert first == second


def test_thesis_to_api_shape():
    gate_inputs, gate_payload, pillar_payload = _thesis_inputs()
    thesis = build_thesis(
        ticker="TEST",
        gate_payload=gate_payload,
        gate_inputs=gate_inputs,
        pillar_payload=pillar_payload,
    )
    api = thesis_to_api(thesis)
    assert api["ticker"] == "TEST"
    assert "sections" in api
    assert "preMortem" in api["sections"]
    assert "signalIndependence" in api["sections"]


def test_research_thesis_route_shape(app, client):
    from app.db import get_db
    from app.repositories import Repository

    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "THES1", "name": "Thesis Test", "cik": "0000000100"}])
        company = repo.get_company_by_ticker("THES1")
        repo.upsert_fundamentals(
            [
                {
                    "company_id": company["id"],
                    "metric": "revenue",
                    "value": 500.0,
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

    response = client.get("/api/research/thesis/THES1")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ticker"] == "THES1"
    assert payload["sections"]["preMortem"]
    assert len(payload["sections"]["disconfirmingConditions"]) >= 2


def test_evaluate_thesis_for_ticker_not_found(app):
    from app.db import get_db
    from app.repositories import Repository
    from app.services.prices import PricesService

    with app.app_context():
        repo = Repository(get_db())
        result = evaluate_thesis_for_ticker(repo, "ZZZZZ", prices_service=PricesService(repo))
        assert result is None
