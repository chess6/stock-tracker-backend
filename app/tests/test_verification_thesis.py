"""Live-DB verification specs for thesis engine gates and thesis shape (R5)."""

from __future__ import annotations

import pytest

from app.services.gate_engine import evaluate_gates_for_ticker
from app.services.thesis_engine import evaluate_thesis_for_ticker
from app.services.verification_spec import THESIS_GATE_SPECS, thesis_expectations_met


@pytest.mark.parametrize("spec", THESIS_GATE_SPECS, ids=lambda item: item["ticker"])
def test_verification_ticker_gate_and_thesis_shape(app, spec):
    from app.db import get_db
    from app.repositories import Repository
    from app.services.prices import PricesService

    with app.app_context():
        repo = Repository(get_db())
        prices = PricesService(repo)
        ticker = spec["ticker"]
        if not repo.get_company_by_ticker(ticker):
            pytest.skip(f"{ticker} not in database")

        gate_payload = evaluate_gates_for_ticker(repo, ticker, prices_service=prices)
        if gate_payload is None:
            pytest.skip(f"{ticker} missing fundamentals for gate evaluation")

        thesis_payload = None
        if spec.get("thesis"):
            thesis_payload = evaluate_thesis_for_ticker(repo, ticker, prices_service=prices)

        failures = thesis_expectations_met(
            gate_payload=gate_payload,
            thesis_payload=thesis_payload,
            spec=spec,
        )
        assert not failures, "; ".join(failures)


def test_pillars_with_debt_maturity_buckets_no_500(app, client):
    """Regression: XBRL debt buckets (year_1) must not crash pillars route."""
    from app.db import get_db
    from app.repositories import Repository

    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "DEBT1", "name": "Debt Maturity Co", "cik": "0000000200"}])
        company = repo.get_company_by_ticker("DEBT1")
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
                    "metric": "cashneq",
                    "value": 50.0,
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
                    "xbrl_concept": "CashAndCashEquivalentsAtCarryingValue",
                },
                {
                    "company_id": company["id"],
                    "metric": "debt",
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
                    "xbrl_concept": "LongTermDebt",
                },
            ]
        )
        repo.upsert_company_debt_maturities(
            company["id"],
            "2024-12-31",
            [
                {"maturity_year": "year_1", "amount": 250.0},
                {"maturity_year": "year_2", "amount": 100.0},
            ],
        )

    response = client.get("/api/research/pillars/DEBT1")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ticker"] == "DEBT1"
    assert "pillars" in payload
