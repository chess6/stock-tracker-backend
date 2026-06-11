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
from app.services.verification_spec import (
    GOLDEN_CURRENT_ROW,
    GOLDEN_EXPECTED_LATEST,
    GOLDEN_PRICES_BY_PERIOD,
    GOLDEN_PRIOR_ROW,
    GOLDEN_TOLERANCE,
    SEED_TICKER_SPECS,
    score_within_range,
    score_within_tolerance,
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


def test_golden_fixture_scores_match_baseline():
    records = compute_scores_for_periods(
        [GOLDEN_CURRENT_ROW, GOLDEN_PRIOR_ROW],
        prices_by_period=GOLDEN_PRICES_BY_PERIOD,
    )
    latest = records[0]
    assert latest["period_end"] == GOLDEN_EXPECTED_LATEST["period_end"]
    for metric, expected in GOLDEN_EXPECTED_LATEST.items():
        if metric == "period_end":
            continue
        tol = GOLDEN_TOLERANCE[metric]
        assert score_within_tolerance(
            latest[metric],
            expected,
            rel_tol=tol["rel"],
            abs_tol=tol["abs"],
        ), f"{metric} drift: {latest[metric]} vs {expected}"


def test_altman_z_returns_none_without_market_cap():
    row = _base_row()
    z, components = compute_altman_z(row, market_cap=None)
    assert z is None
    assert components == {}


def test_altman_z_returns_none_for_zero_assets_or_liabilities():
    row = _base_row(assets=0, liabilities=900.0)
    z, components = compute_altman_z(row, market_cap=1000.0)
    assert z is None
    assert components == {}

    row = _base_row(assets=2000.0, liabilities=0)
    z, components = compute_altman_z(row, market_cap=1000.0)
    assert z is None
    assert components == {}


def test_altman_z_returns_none_when_core_ratios_missing():
    row = _base_row(workingcapital=None, assetscurrent=None, liabilitiescurrent=None, retearn=None, ebit=None, revenue=None)
    z, components = compute_altman_z(row, market_cap=1000.0)
    assert z is None
    assert components == {}


def test_beneish_m_returns_none_for_zero_revenue_or_assets():
    current = _base_row(revenue=0)
    prior = _base_row(calendardate="2023-12-31", revenue=900.0)
    m, components = compute_beneish_m(current, prior)
    assert m is None
    assert components == {}

    current = _base_row(assets=0)
    prior = _base_row(calendardate="2023-12-31")
    m, components = compute_beneish_m(current, prior)
    assert m is None
    assert components == {}


def test_survivability_returns_none_without_usable_inputs():
    row = {
        "cashneq": None,
        "ebit": None,
        "opinc": None,
        "interestexp": None,
        "equity": None,
    }
    score, bucket = compute_survivability(row)
    assert score is None
    assert bucket is None


def test_compute_scores_for_periods_skips_piotroski_without_prior():
    rows = [_base_row(calendardate="2024-12-31")]
    records = compute_scores_for_periods(rows, prices_by_period={"2024-12-31": 10.0})
    assert len(records) == 1
    assert records[0]["piotroski_f"] is None
    assert records[0]["beneish_m"] is None
    assert records[0]["altman_z"] is not None


def test_seed_ticker_specs_are_well_formed():
    assert len(SEED_TICKER_SPECS) >= 4
    for spec in SEED_TICKER_SPECS:
        assert spec["ticker"]
        assert spec["metrics"]
        for metric_spec in spec["metrics"].values():
            if metric_spec.get("nullable"):
                continue
            assert "min" in metric_spec and "max" in metric_spec
            assert metric_spec["min"] <= metric_spec["max"]
            if "value" in metric_spec:
                assert score_within_range(metric_spec["value"], metric_spec)


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

        def fetch_prices_by_period_ends(self, ticker, period_ends):
            return {period[:10]: 10.0 for period in period_ends if period}

        def upsert_company_scores(self, company_id, records):
            self.scores.extend(records)
            return len(records)

        def should_skip_score_recompute(self, company_id, period_ends, scoring_version, *, dimension="ARY"):
            return False

        def fetch_fundamentals_overwrite_state(self, company_id):
            return {}

        def delete_fundamentals_snapshots(self, company_id, dimensions):
            return None

    class FakeSecClient:
        def fetch_company_facts(self, cik: str) -> dict:
            return {"facts": {"us-gaap": {}, "dei": {}}}

        def fetch_submissions(self, cik: str) -> dict:
            return {"filings": {"recent": {}}}

    fake_repo = FakeRepo()
    FundamentalsService(fake_repo, FakeSecClient()).refresh_fundamentals(["GOOD"])
    assert len(fake_repo.scores) == 1
    assert fake_repo.scores[0]["survivability"] is not None


def test_refresh_company_scores_batch(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies(
            [{"ticker": "GOOD", "name": "Good Co", "cik": "0000000001", "sector": "Tech", "industry": "Hardware"}]
        )
        company = repo.get_company_by_ticker("GOOD")
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
                    "accession": "test",
                    "source": "test",
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
                    "accession": "test",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "NetIncomeLoss",
                },
                {
                    "company_id": company["id"],
                    "metric": "assets",
                    "value": 2000.0,
                    "unit": "USD",
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                    "form": "10-K",
                    "accession": "test",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "Assets",
                },
                {
                    "company_id": company["id"],
                    "metric": "revenue",
                    "value": 900.0,
                    "unit": "USD",
                    "period_end": "2023-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2023,
                    "fiscal_quarter": "FY",
                    "filing_date": "2024-01-20",
                    "form": "10-K",
                    "accession": "test2",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "Revenues",
                },
                {
                    "company_id": company["id"],
                    "metric": "netinc",
                    "value": 100.0,
                    "unit": "USD",
                    "period_end": "2023-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2023,
                    "fiscal_quarter": "FY",
                    "filing_date": "2024-01-20",
                    "form": "10-K",
                    "accession": "test2",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "NetIncomeLoss",
                },
                {
                    "company_id": company["id"],
                    "metric": "assets",
                    "value": 1800.0,
                    "unit": "USD",
                    "period_end": "2023-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2023,
                    "fiscal_quarter": "FY",
                    "filing_date": "2024-01-20",
                    "form": "10-K",
                    "accession": "test2",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "Assets",
                },
                {
                    "company_id": company["id"],
                    "metric": "ncfo",
                    "value": 150.0,
                    "unit": "USD",
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                    "form": "10-K",
                    "accession": "test",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "NetCashProvidedByUsedInOperatingActivities",
                },
                {
                    "company_id": company["id"],
                    "metric": "liabilities",
                    "value": 800.0,
                    "unit": "USD",
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                    "form": "10-K",
                    "accession": "test",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "Liabilities",
                },
                {
                    "company_id": company["id"],
                    "metric": "equity",
                    "value": 1200.0,
                    "unit": "USD",
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                    "form": "10-K",
                    "accession": "test",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "Equity",
                },
                {
                    "company_id": company["id"],
                    "metric": "ebit",
                    "value": 200.0,
                    "unit": "USD",
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                    "form": "10-K",
                    "accession": "test",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "Ebit",
                },
                {
                    "company_id": company["id"],
                    "metric": "cashneq",
                    "value": 300.0,
                    "unit": "USD",
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                    "form": "10-K",
                    "accession": "test",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "Cash",
                },
                {
                    "company_id": company["id"],
                    "metric": "equity",
                    "value": 1000.0,
                    "unit": "USD",
                    "period_end": "2023-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2023,
                    "fiscal_quarter": "FY",
                    "filing_date": "2024-01-20",
                    "form": "10-K",
                    "accession": "test2",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "Equity",
                },
                {
                    "company_id": company["id"],
                    "metric": "ebit",
                    "value": 180.0,
                    "unit": "USD",
                    "period_end": "2023-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2023,
                    "fiscal_quarter": "FY",
                    "filing_date": "2024-01-20",
                    "form": "10-K",
                    "accession": "test2",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "Ebit",
                },
                {
                    "company_id": company["id"],
                    "metric": "cashneq",
                    "value": 250.0,
                    "unit": "USD",
                    "period_end": "2023-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2023,
                    "fiscal_quarter": "FY",
                    "filing_date": "2024-01-20",
                    "form": "10-K",
                    "accession": "test2",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "Cash",
                },
                {
                    "company_id": company["id"],
                    "metric": "ncfo",
                    "value": 140.0,
                    "unit": "USD",
                    "period_end": "2023-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2023,
                    "fiscal_quarter": "FY",
                    "filing_date": "2024-01-20",
                    "form": "10-K",
                    "accession": "test2",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "Ncfo",
                },
                {
                    "company_id": company["id"],
                    "metric": "liabilities",
                    "value": 800.0,
                    "unit": "USD",
                    "period_end": "2023-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2023,
                    "fiscal_quarter": "FY",
                    "filing_date": "2024-01-20",
                    "form": "10-K",
                    "accession": "test2",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "Liabilities",
                },
            ]
        )
        repo.upsert_prices(
            "GOOD",
            [
                {"date": "2024-12-31", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 100},
                {"date": "2023-12-31", "open": 8.0, "high": 9.0, "low": 7.0, "close": 8.0, "volume": 100},
            ],
            source="test",
        )

        class StubSec:
            def fetch_company_facts(self, cik):
                return {"facts": {}}

            def fetch_submissions(self, cik):
                return {"filings": {"recent": {}}}

        service = FundamentalsService(repo, StubSec())
        result = service.refresh_company_scores_batch(["GOOD"])
        assert "GOOD" in result["tickers"]
        assert result["periodsWritten"] >= 1
        assert result["recomputed"] >= 1

        second = service.refresh_company_scores_batch(["GOOD"])
        assert second["skipped_unchanged"] == 1
        assert second["recomputed"] == 0
        assert second["periodsWritten"] == 0

        scores = repo.fetch_company_scores(company["id"], dimension="ARY")
        assert len(scores) >= 1
        assert scores[0]["piotroskiF"] is not None


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
            [
                {"date": "2024-12-31", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 100},
                {"date": "2025-01-20", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 100},
            ],
            source="test",
        )
        from app.services.fundamentals import FundamentalsService

        class StubSec:
            def fetch_company_facts(self, cik):
                return {"facts": {}}

            def fetch_submissions(self, cik):
                return {"filings": {"recent": {}}}

        FundamentalsService(repo, StubSec())._refresh_company_scores(company["id"], "AAPL")[0]

    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_company_narrative_snapshots([
            {
                "ticker": "AAPL",
                "snapshot_date": "2025-01-01",
                "states": [],
                "divergence_score": 0.75,
                "divergence_signal": "rerating_candidate",
                "emerging_situations": [],
            }
        ])

    screener = client.get("/api/research/screener?tickers=AAPL&dimension=MRY")
    assert screener.status_code == 200
    payload = screener.get_json()
    assert "AAPL" in payload["results"]
    assert payload["results"]["AAPL"]["scores"]["altmanZ"] is not None
    assert payload["results"]["AAPL"]["narrativeDivergence"]["signal"] == "rerating_candidate"

    detail = client.get("/api/research/ticker/AAPL")
    assert detail.status_code == 200
    detail_payload = detail.get_json()
    assert detail_payload["ticker"] == "AAPL"
    assert len(detail_payload["periods"]) >= 1
    assert len(detail_payload["scoreHistory"]) >= 1
    assert "metricTrends" in detail_payload
    assert isinstance(detail_payload["metricTrends"], dict)


def test_research_insider_routes(app, client):
    from datetime import date, timedelta

    today = date.today()
    d1 = (today - timedelta(days=5)).isoformat()
    d2 = (today - timedelta(days=3)).isoformat()
    d3 = (today - timedelta(days=1)).isoformat()

    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "GME", "name": "GameStop", "cik": "0001326380"}])
        company = repo.get_company_by_ticker("GME")
        repo.upsert_insider_transactions(
            company["id"],
            [
                {
                    "filing_date": d1,
                    "transaction_date": d1,
                    "owner_name": "Insider A",
                    "transaction_code": "P",
                    "shares": 1000,
                    "price_per_share": 20.0,
                    "transaction_value": 20000.0,
                    "security_title": "Common",
                    "form": "4",
                    "accession": "acc-1",
                },
                {
                    "filing_date": d2,
                    "transaction_date": d2,
                    "owner_name": "Insider B",
                    "transaction_code": "P",
                    "shares": 500,
                    "price_per_share": 21.0,
                    "transaction_value": 10500.0,
                    "security_title": "Common",
                    "form": "4",
                    "accession": "acc-2",
                },
                {
                    "filing_date": d3,
                    "transaction_date": d3,
                    "owner_name": "Insider C",
                    "transaction_code": "P",
                    "shares": 800,
                    "price_per_share": 22.0,
                    "transaction_value": 17600.0,
                    "security_title": "Common",
                    "form": "4",
                    "accession": "acc-3",
                },
            ],
        )
        from app.services.research import ResearchService
        from app.services.prices import PricesService

        ResearchService(repo, PricesService(repo)).refresh_insider_clusters(company["id"])

    detail = client.get("/api/research/insiders/GME")
    assert detail.status_code == 200
    payload = detail.get_json()
    assert payload["ticker"] == "GME"
    assert payload["summary"]["buyCount90d"] >= 3
    assert len(payload["clusters"]) >= 1

    clusters = client.get("/api/research/insiders/clusters?tickers=GME")
    assert clusters.status_code == 200
    cluster_payload = clusters.get_json()
    assert len(cluster_payload["clusters"]) >= 1
    assert cluster_payload["clusters"][0]["ticker"] == "GME"


def test_upsert_insider_cluster_analysis_replaces_stale_rows(app):
    from datetime import date, timedelta

    today = date.today()
    window_start = (today - timedelta(days=29)).isoformat()
    window_end = today.isoformat()

    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "CLST", "name": "Cluster Test", "cik": "0000000001"}])
        company = repo.get_company_by_ticker("CLST")
        repo.upsert_insider_cluster_analysis(
            company["id"],
            [
                {
                    "window_start": window_start,
                    "window_end": window_end,
                    "buy_count": 3,
                    "sell_count": 0,
                    "unique_buyers": 3,
                    "total_buy_value": 0.0,
                    "total_sell_value": 0.0,
                    "avg_buy_price": None,
                    "intensity_score": 0.0,
                },
            ],
        )
        repo.upsert_insider_cluster_analysis(
            company["id"],
            [
                {
                    "window_start": window_start,
                    "window_end": window_end,
                    "buy_count": 3,
                    "sell_count": 0,
                    "unique_buyers": 3,
                    "total_buy_value": 150000.0,
                    "total_sell_value": 0.0,
                    "avg_buy_price": 10.0,
                    "intensity_score": 0.8,
                },
            ],
        )
        rows = repo.fetch_insider_cluster_rankings(["CLST"], limit=10)
        assert len(rows) == 1
        assert rows[0]["totalBuyValue"] == 150000.0
