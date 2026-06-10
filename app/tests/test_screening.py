"""Composable screening engine — POST /api/research/screen."""

from __future__ import annotations

from app.db import get_db
from app.repositories import Repository
from app.services.fundamentals import FundamentalsService
from app.services.screening import run_composable_screen
from app.services.prices import PricesService


def _seed_aapl_fundamentals(repo: Repository) -> dict:
    repo.upsert_companies(
        [{"ticker": "AAPL", "name": "Apple Inc", "cik": "0000320193", "sector": "Tech", "industry": "Hardware"}]
    )
    company = repo.get_company_by_ticker("AAPL")
    fundamentals = [
        ("revenue", 1000.0),
        ("netinc", 200.0),
        ("assets", 3000.0),
        ("liabilities", 1200.0),
        ("equity", 1800.0),
        ("ncfo", 250.0),
        ("ebit", 300.0),
        ("retearn", 800.0),
        ("sharesbas", 100.0),
        ("gp", 400.0),
        ("assetscurrent", 1000.0),
        ("liabilitiescurrent", 500.0),
        ("workingcapital", 500.0),
        ("receivables", 120.0),
        ("ppnenet", 900.0),
        ("depamor", 60.0),
        ("sgna", 100.0),
        ("debt", 400.0),
        ("cashneq", 300.0),
        ("interestexp", 15.0),
        ("fcf", 180.0),
        ("eps", 2.0),
    ]
    for metric, value in fundamentals:
        repo.upsert_fundamentals(
            [
                {
                    "company_id": company["id"],
                    "metric": metric,
                    "value": value,
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
                    "xbrl_concept": metric,
                }
            ]
        )
    repo.upsert_prices(
        "AAPL",
        [{"date": "2025-01-20", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 100}],
        source="test",
    )

    class StubSec:
        def fetch_company_facts(self, cik):
            return {"facts": {}}

        def fetch_submissions(self, cik):
            return {"filings": {"recent": {}}}

    FundamentalsService(repo, StubSec())._refresh_company_scores(company["id"], "AAPL")[0]
    return company


def test_screen_rejects_invalid_spec(app):
    with app.app_context():
        repo = Repository(get_db())
        payload, status, error = run_composable_screen(repo, PricesService(repo), {"filters": "bad"})
        assert status == 400
        assert error


def test_screen_accepts_field_alias_for_filter_metric(app, client):
    with app.app_context():
        _seed_aapl_fundamentals(Repository(get_db()))

    response = client.post(
        "/api/research/screen",
        json={
            "tickers": ["AAPL"],
            "filters": [{"field": "survivability", "op": "gte", "value": 50}],
            "limit": 10,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["results"][0]["ticker"] == "AAPL"


def test_screen_filters_by_score_and_metric(app, client):
    with app.app_context():
        _seed_aapl_fundamentals(Repository(get_db()))

    response = client.post(
        "/api/research/screen",
        json={
            "tickers": ["AAPL"],
            "filters": [
                {"metric": "survivability", "op": "gte", "value": 50},
                {"metric": "pb", "op": "lt", "value": 20},
            ],
            "sort": {"metric": "survivability", "dir": "desc"},
            "limit": 10,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["meta"]["matched"] >= 1
    assert len(payload["results"]) >= 1
    row = payload["results"][0]
    assert row["ticker"] == "AAPL"
    assert len(row["filterEvidence"]) == 2
    assert all(item["passed"] for item in row["filterEvidence"])


def test_screen_returns_evidence_for_failed_filter(app, client):
    with app.app_context():
        _seed_aapl_fundamentals(Repository(get_db()))

    response = client.post(
        "/api/research/screen",
        json={
            "tickers": ["AAPL"],
            "filters": [{"metric": "pb", "op": "lt", "value": 0.01}],
            "limit": 5,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["meta"]["matched"] == 0
    assert payload["results"] == []


def test_screen_insider_buy6m_filter(app, client):
    from datetime import date, timedelta

    today = date.today()
    recent = (today - timedelta(days=10)).isoformat()

    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "BUY1", "name": "Buyer Co", "cik": "0000000001"}])
        company = repo.get_company_by_ticker("BUY1")
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
                    "filing_date": "2025-01-01",
                    "form": "10-K",
                    "accession": "1",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "revenue",
                }
            ]
        )
        repo.upsert_insider_transactions(
            company["id"],
            [
                {
                    "accession": "a1",
                    "filing_date": recent,
                    "transaction_date": recent,
                    "owner_name": "CEO",
                    "owner_title": "CEO",
                    "transaction_code": "P",
                    "transaction_value": 250000.0,
                    "shares": 1000,
                    "price_per_share": 250.0,
                    "security_title": "Common",
                }
            ],
        )

    response = client.post(
        "/api/research/screen",
        json={
            "tickers": ["BUY1"],
            "filters": [{"metric": "buy6m", "op": "gte", "value": 100000}],
            "limit": 5,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["meta"]["matched"] == 1
    assert payload["results"][0]["insider"]["buy6m"] >= 100000
