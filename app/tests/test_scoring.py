from __future__ import annotations

from app.db import get_db
from app.repositories import Repository
from app.services.fundamentals import FundamentalsService
from app.services.scoring import (
    altman_zone,
    compute_altman_z,
    compute_beneish_m,
    compute_piotroski_f,
    compute_scores_for_periods,
    compute_survivability,
    survivability_bucket,
)


def _base_row(**overrides) -> dict:
    row = {
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
    row.update(overrides)
    return row


def test_piotroski_f_scores_improving_company():
    current = _base_row()
    prior = _base_row(
        calendardate="2023-12-31",
        netinc=50.0,
        ncfo=80.0,
        debt=500.0,
        sharesbas=110.0,
        gp=300.0,
        revenue=900.0,
        assetscurrent=700.0,
        liabilitiescurrent=500.0,
    )
    score, components = compute_piotroski_f(current, prior)
    assert score == 9
    assert components["roa"] == 1
    assert components["cfo"] == 1
    assert components["delta_roa"] == 1
    assert components["accruals"] == 1
    assert components["delta_leverage"] == 1
    assert components["no_dilution"] == 1


def test_piotroski_f_returns_none_without_core_inputs():
    current = _base_row(netinc=None, ncfo=None)
    prior = _base_row(calendardate="2023-12-31")
    score, components = compute_piotroski_f(current, prior)
    assert score is None
    assert components == {}


def test_altman_z_safe_zone():
    row = _base_row(
        workingcapital=800.0,
        retearn=1200.0,
        ebit=400.0,
        revenue=2500.0,
        liabilities=600.0,
        assets=2500.0,
    )
    z, components = compute_altman_z(row, market_cap=5000.0)
    assert z is not None
    assert z > 2.99
    assert altman_zone(z) == "safe"
    assert "wc_ta" in components


def test_altman_z_distress_zone():
    row = _base_row(
        workingcapital=-200.0,
        retearn=-400.0,
        ebit=-50.0,
        revenue=200.0,
        liabilities=1800.0,
    )
    z, _ = compute_altman_z(row, market_cap=100.0)
    assert z is not None
    assert z < 1.81
    assert altman_zone(z) == "distress"


def test_beneish_m_computes_with_full_inputs():
    current = _base_row()
    prior = _base_row(
        calendardate="2023-12-31",
        revenue=900.0,
        receivables=90.0,
        gp=320.0,
        assetscurrent=700.0,
        ppnenet=550.0,
        depamor=45.0,
        sgna=110.0,
        liabilities=950.0,
        netinc=100.0,
        ncfo=120.0,
    )
    m, components = compute_beneish_m(current, prior)
    assert m is not None
    assert set(components) == {"dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi", "tata"}


def test_beneish_m_returns_none_when_receivables_missing():
    current = _base_row(receivables=None)
    prior = _base_row(calendardate="2023-12-31", receivables=None)
    m, components = compute_beneish_m(current, prior)
    assert m is None
    assert components == {}


def test_survivability_bucket_mapping():
    assert survivability_bucket(10) == "critical"
    assert survivability_bucket(30) == "distressed"
    assert survivability_bucket(50) == "watchlist"
    assert survivability_bucket(70) == "stable"
    assert survivability_bucket(90) == "strong"


def test_survivability_strong_company_scores_high():
    row = _base_row()
    prior = _base_row(calendardate="2023-12-31", debt=500.0)
    score, bucket = compute_survivability(row, prior=prior, altman_z=3.5, fcf_positive_streak=3)
    assert score is not None
    assert score >= 70
    assert bucket in {"stable", "strong"}


def test_compute_scores_for_periods_produces_history():
    rows = [
        _base_row(calendardate="2024-12-31"),
        _base_row(
            calendardate="2023-12-31",
            revenue=900.0,
            netinc=80.0,
            ncfo=100.0,
            sharesbas=105.0,
            gp=350.0,
            receivables=90.0,
            depamor=45.0,
            sgna=110.0,
            liabilities=950.0,
        ),
    ]
    records = compute_scores_for_periods(rows, prices_by_period={"2024-12-31": 20.0, "2023-12-31": 15.0})
    assert len(records) == 2
    latest = records[0]
    assert latest["period_end"] == "2024-12-31"
    assert latest["piotroski_f"] is not None
    assert latest["altman_z"] is not None
    assert latest["beneish_m"] is not None
    assert latest["survivability"] is not None


def test_refresh_fundamentals_persists_company_scores(app):
    class FakeRepo:
        def __init__(self) -> None:
            self.company_id = 1
            self.scores: list[dict] = []

        def get_company_by_ticker(self, ticker: str):
            return {"id": self.company_id, "ticker": "GOOD", "cik": "0000000001"}

        def upsert_fundamentals(self, records):
            return len(records)

        def update_company_metadata(self, ticker, meta):
            return None

        def fetch_fundamentals_rows(self, tickers, gte=None, dimension=None):
            return [
                {
                    "ticker": "GOOD",
                    "company_name": "Good Co",
                    "metric": "revenue",
                    "value": 1000.0,
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                },
                {
                    "ticker": "GOOD",
                    "company_name": "Good Co",
                    "metric": "netinc",
                    "value": 120.0,
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                },
                {
                    "ticker": "GOOD",
                    "company_name": "Good Co",
                    "metric": "assets",
                    "value": 2000.0,
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                },
                {
                    "ticker": "GOOD",
                    "company_name": "Good Co",
                    "metric": "ncfo",
                    "value": 150.0,
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                },
                {
                    "ticker": "GOOD",
                    "company_name": "Good Co",
                    "metric": "liabilities",
                    "value": 900.0,
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                },
                {
                    "ticker": "GOOD",
                    "company_name": "Good Co",
                    "metric": "retearn",
                    "value": 500.0,
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                },
                {
                    "ticker": "GOOD",
                    "company_name": "Good Co",
                    "metric": "ebit",
                    "value": 200.0,
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                },
                {
                    "ticker": "GOOD",
                    "company_name": "Good Co",
                    "metric": "sharesbas",
                    "value": 100.0,
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                },
            ]

        def fetch_price_near_date(self, ticker, target_date):
            return 10.0

        def upsert_company_scores(self, company_id, records):
            self.scores.extend(records)
            return len(records)

    class FakeSecClient:
        def fetch_company_facts(self, cik: str) -> dict:
            return {"facts": {"us-gaap": {}, "dei": {}}}

        def fetch_submissions(self, cik: str) -> dict:
            return {"filings": {"recent": {}}}

    fake_repo = FakeRepo()
    FundamentalsService(fake_repo, FakeSecClient()).refresh_fundamentals(["GOOD"])
    assert len(fake_repo.scores) == 1
    assert fake_repo.scores[0]["survivability"] is not None


def test_research_routes_return_screener_and_ticker_payload(app, client):
    with app.app_context():
        repo = Repository(get_db())
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
        from app.services.fundamentals import FundamentalsService

        class StubSec:
            def fetch_company_facts(self, cik):
                return {"facts": {}}

            def fetch_submissions(self, cik):
                return {"filings": {"recent": {}}}

        FundamentalsService(repo, StubSec())._refresh_company_scores(company["id"], "AAPL")

    screener = client.get("/api/research/screener?tickers=AAPL&dimension=MRY")
    assert screener.status_code == 200
    payload = screener.get_json()
    assert "AAPL" in payload["results"]
    assert payload["results"]["AAPL"]["scores"]["altmanZ"] is not None

    detail = client.get("/api/research/ticker/AAPL")
    assert detail.status_code == 200
    detail_payload = detail.get_json()
    assert detail_payload["ticker"] == "AAPL"
    assert len(detail_payload["periods"]) >= 1
    assert len(detail_payload["scoreHistory"]) >= 1
