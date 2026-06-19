from __future__ import annotations

from app.services.fundamentals import (
    CANONICAL_ANNUAL_MIN_STATEMENT_METRICS,
    collapse_narrow_fundamentals_rows,
    filter_canonical_annual_wide_rows,
    pivot_fundamentals_rows,
    resolve_financial_dimension,
)
from app.services.sec import normalize_company_facts


def test_resolve_financial_dimension_maps_legacy_codes():
    assert resolve_financial_dimension("MRY", False)["storage_dimension"] == "MRY"
    assert resolve_financial_dimension("MRY", False)["legacy_storage_dimension"] == "ARY"
    assert resolve_financial_dimension("MRQ", False)["storage_dimension"] == "MRQ"
    assert resolve_financial_dimension("ART", False)["ttm_only"] is True
    assert resolve_financial_dimension("MRT", False)["storage_dimension"] == "MRT"
    assert resolve_financial_dimension("MRT", False)["legacy_ttm_only"] is True


def test_normalize_company_facts_maps_us_gaap_shares_outstanding():
    payload = {
        "facts": {
            "us-gaap": {
                "CommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "val": 12_116_000_000,
                                "fy": 2026,
                                "fp": "Q1",
                                "form": "10-Q",
                                "filed": "2026-04-30",
                                "end": "2026-03-31",
                                "accn": "1",
                            },
                        ]
                    }
                }
            }
        }
    }

    rows = normalize_company_facts(7489, payload)
    shares = [row for row in rows if row["metric"] == "sharesbas"]
    assert len(shares) == 1
    assert shares[0]["value"] == 12_116_000_000
    assert shares[0]["xbrl_concept"] == "CommonStockSharesOutstanding"


def test_normalize_company_facts_maps_core_metrics():
    payload = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {"val": 1000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-20", "end": "2024-12-31", "accn": "1"},
                            {"val": 220, "fy": 2025, "fp": "Q1", "form": "10-Q", "filed": "2025-04-20", "end": "2025-03-31", "accn": "2"},
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            {"val": 200, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-20", "end": "2024-12-31", "accn": "1"},
                        ]
                    }
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            {"val": 50, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-20", "end": "2024-12-31", "accn": "1"},
                        ]
                    }
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"val": 10, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-20", "end": "2024-12-31", "accn": "1"},
                        ]
                    }
                }
            },
        }
    }

    rows = normalize_company_facts(7, payload)
    metrics = {(row["metric"], row["period_end"], row["dimension"]): row for row in rows}
    assert ("revenue", "2024-12-31", "ARY") in metrics
    assert ("revenue", "2025-03-31", "ARQ") in metrics
    assert ("sharesbas", "2024-12-31", "ARY") in metrics
    assert ("fcf", "2024-12-31", "ARY") in metrics
    assert metrics[("fcf", "2024-12-31", "ARY")]["value"] == 150.0


def test_normalize_company_facts_derives_opinc_from_gp_and_opex():
    payload = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {"val": 1000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-20", "end": "2024-12-31", "accn": "1"},
                        ]
                    }
                },
                "CostOfRevenue": {
                    "units": {
                        "USD": [
                            {"val": 400, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-20", "end": "2024-12-31", "accn": "1"},
                        ]
                    }
                },
                "OperatingExpenses": {
                    "units": {
                        "USD": [
                            {"val": 200, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-20", "end": "2024-12-31", "accn": "1"},
                        ]
                    }
                },
            }
        }
    }

    rows = normalize_company_facts(11, payload)
    metrics = {(row["metric"], row["period_end"]): row["value"] for row in rows}
    assert metrics[("gp", "2024-12-31")] == 600.0
    assert metrics[("opinc", "2024-12-31")] == 400.0


def test_normalize_company_facts_derives_gp_and_ebitda():
    payload = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {"val": 1000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-20", "end": "2024-12-31", "accn": "1"},
                        ]
                    }
                },
                "CostOfRevenue": {
                    "units": {
                        "USD": [
                            {"val": 400, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-20", "end": "2024-12-31", "accn": "1"},
                        ]
                    }
                },
                "OperatingIncomeLoss": {
                    "units": {
                        "USD": [
                            {"val": 300, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-20", "end": "2024-12-31", "accn": "1"},
                        ]
                    }
                },
                "DepreciationDepletionAndAmortization": {
                    "units": {
                        "USD": [
                            {"val": 50, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-20", "end": "2024-12-31", "accn": "1"},
                        ]
                    }
                },
            }
        }
    }

    rows = normalize_company_facts(9, payload)
    metrics = {(row["metric"], row["period_end"]): row["value"] for row in rows}
    assert metrics[("gp", "2024-12-31")] == 600.0
    assert metrics[("ebit", "2024-12-31")] == 300.0
    assert metrics[("ebitda", "2024-12-31")] == 350.0


def test_materialize_snapshot_dimensions_persists_mry_mrq_mrt(app):
    from app.db import get_db
    from app.repositories import Repository
    from app.services.fundamentals import FundamentalsService

    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies(
            [{"ticker": "SNAP", "name": "Snap Co", "cik": "0000000099", "sector": "Tech", "industry": "Software"}]
        )
        company = repo.get_company_by_ticker("SNAP")
        records = []
        for period_end, dimension, revenue in [
            ("2024-12-31", "ARY", 1000.0),
            ("2024-09-30", "ARQ", 260.0),
            ("2024-06-30", "ARQ", 250.0),
            ("2024-03-31", "ARQ", 240.0),
            ("2023-12-31", "ARQ", 230.0),
        ]:
            records.append(
                {
                    "company_id": company["id"],
                    "metric": "revenue",
                    "value": revenue,
                    "unit": "USD",
                    "period_end": period_end,
                    "period_type": "annual" if dimension == "ARY" else "quarterly",
                    "dimension": dimension,
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY" if dimension == "ARY" else "Q3",
                    "filing_date": "2025-01-20",
                    "form": "10-K" if dimension == "ARY" else "10-Q",
                    "accession": "test",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "Revenues",
                }
            )
        repo.upsert_fundamentals(records)

        class StubSec:
            def fetch_submissions(self, cik):
                return {"filings": {"recent": {}}}

        service = FundamentalsService(repo, StubSec())
        written = service._materialize_snapshot_dimensions(company["id"], "SNAP")
        assert written > 0

        mry_rows = repo.fetch_fundamentals_rows(["SNAP"], dimension="MRY")
        mrq_rows = repo.fetch_fundamentals_rows(["SNAP"], dimension="MRQ")
        mrt_rows = repo.fetch_fundamentals_rows(["SNAP"], dimension="MRT")
        assert mry_rows
        assert mrq_rows
        assert mrt_rows
        assert any(row["metric"] == "revenue" for row in mry_rows)
        assert any(row["metric"] == "revenue" for row in mrt_rows)


def test_build_company_metrics_computes_ebitda_ev():
    from app.services.fundamentals import build_company_metrics

    metrics = build_company_metrics(
        {
            "sharesbas": 10.0,
            "revenue": 1000.0,
            "equity": 500.0,
            "ncfo": 200.0,
            "fcf": 150.0,
            "eps": 5.0,
            "ebitda": 120.0,
            "debt": 80.0,
            "cashneq": 20.0,
        },
        price=10.0,
    )
    assert metrics["marketCap"] == 100.0
    assert metrics["ebitdaEv"] == 120.0 / (100.0 + 80.0 - 20.0)


def test_get_financials_most_recent_prefers_annual_rows():
    from app.services.fundamentals import FundamentalsService

    class FakeRepo:
        def fetch_fundamentals_rows(self, tickers, gte=None, dimension=None):
            return [
                {
                    "ticker": "JPM",
                    "company_name": "JPM",
                    "metric": "netinc",
                    "value": 1.0,
                    "period_end": "2026-03-31",
                    "period_type": "quarterly",
                    "dimension": "ARQ",
                    "fiscal_year": 2026,
                    "fiscal_quarter": "Q1",
                    "filing_date": "2026-05-01",
                },
                {
                    "ticker": "JPM",
                    "company_name": "JPM",
                    "metric": "revenue",
                    "value": 100.0,
                    "period_end": "2025-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2025,
                    "fiscal_quarter": "FY",
                    "filing_date": "2026-02-01",
                },
                {
                    "ticker": "JPM",
                    "company_name": "JPM",
                    "metric": "netinc",
                    "value": 50.0,
                    "period_end": "2025-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2025,
                    "fiscal_quarter": "FY",
                    "filing_date": "2026-02-01",
                },
            ]

        def fetch_prices(self, ticker, limit=None):
            return []

        def fetch_prices_by_period_ends(self, ticker, period_ends):
            return {period_end: None for period_end in period_ends}

    payload = FundamentalsService(FakeRepo(), None).get_financials_payload(["JPM"], None, None, True)
    assert payload["meta"]["source"] == "sqlite"
    assert payload["meta"]["computedAt"]
    row = payload["raw"]["datatable"]["data"][0]
    cols = [c["name"] for c in payload["raw"]["datatable"]["columns"]]
    wide = dict(zip(cols, row))
    assert wide["calendardate"] == "2025-12-31"
    assert wide["dimension"] == "ARY"
    assert wide["revenue"] == 100.0


def test_refresh_fundamentals_continues_after_sec_404():
    import requests
    from app.services.fundamentals import FundamentalsService

    class FakeRepo:
        def __init__(self) -> None:
            self.fundamentals: list[dict] = []

        def get_company_by_ticker(self, ticker: str):
            companies = {
                "GOOD": {"id": 1, "ticker": "GOOD", "cik": "0000000001"},
                "BAD": {"id": 2, "ticker": "BAD", "cik": "0000000002"},
            }
            return companies.get(ticker.upper())

        def upsert_fundamentals(self, records):
            self.fundamentals.extend(records)
            return len(records)

        def fetch_fundamentals_overwrite_state(self, company_id):
            return {}

        def fetch_fundamentals_rows(self, tickers, gte=None, dimension=None):
            return []

        def delete_fundamentals_snapshots(self, company_id, dimensions):
            return 0

        def upsert_company_scores(self, company_id, records):
            return 0

        def fetch_prices_by_period_ends(self, ticker, period_ends):
            return {period[:10]: None for period in period_ends if period}

        def update_company_metadata(self, ticker, meta):
            return None

    class FakeSecClient:
        def fetch_company_facts(self, cik: str) -> dict:
            if cik.endswith("0002"):
                response = requests.Response()
                response.status_code = 404
                raise requests.HTTPError("404", response=response)
            return {"facts": {"us-gaap": {}, "dei": {}}}

        def fetch_submissions(self, cik: str) -> dict:
            return {"filings": {"recent": {}}}

    payload = FundamentalsService(FakeRepo(), FakeSecClient()).refresh_fundamentals(["GOOD", "BAD"])
    assert payload["tickers"] == ["GOOD"]
    assert len(payload["errors"]) == 0
    assert len(payload["skipped"]) == 1
    assert payload["skipped"][0]["ticker"] == "BAD"
    assert payload["skipped"][0]["reason"] == "no_sec_companyfacts"


def test_refresh_fundamentals_skips_etf_before_sec_call():
    from app.services.fundamentals import FundamentalsService

    class FakeRepo:
        def get_company_by_ticker(self, ticker: str):
            return {
                "id": 9,
                "ticker": "QQQ",
                "name": "INVESCO QQQ TRUST, SERIES 1",
                "cik": "0001067839",
            }

        def upsert_fundamentals(self, records):
            raise AssertionError("SEC should not be called for ETF")

        def fetch_fundamentals_rows(self, tickers, gte=None, dimension=None):
            return []

        def delete_fundamentals_snapshots(self, company_id, dimensions):
            return 0

        def upsert_company_scores(self, company_id, records):
            return 0

        def fetch_prices_by_period_ends(self, ticker, period_ends):
            return {period[:10]: None for period in period_ends if period}

        def update_company_metadata(self, ticker, meta):
            return None

    class FailingSecClient:
        def fetch_company_facts(self, cik: str) -> dict:
            raise AssertionError("SEC should not be called for ETF")

    payload = FundamentalsService(FakeRepo(), FailingSecClient()).refresh_fundamentals(["QQQ"])
    assert payload["tickers"] == []
    assert payload["skipped"] == [{"ticker": "QQQ", "reason": "index_etf"}]
    assert payload["errors"] == []


def test_pivot_fundamentals_rows_aligns_shares_to_statement_period():
    rows = [
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "metric": "revenue",
            "value": 416_000_000_000,
            "period_end": "2025-09-27",
            "period_type": "annual",
            "dimension": "ARY",
            "fiscal_year": 2025,
            "fiscal_quarter": "FY",
            "filing_date": "2025-10-31",
        },
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "metric": "netinc",
            "value": 112_000_000_000,
            "period_end": "2025-09-27",
            "period_type": "annual",
            "dimension": "ARY",
            "fiscal_year": 2025,
            "fiscal_quarter": "FY",
            "filing_date": "2025-10-31",
        },
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "metric": "sharesbas",
            "value": 14_776_353_000,
            "period_end": "2025-10-17",
            "period_type": "annual",
            "dimension": "ARY",
            "fiscal_year": 2025,
            "fiscal_quarter": "FY",
            "filing_date": "2025-10-31",
        },
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "metric": "revenue",
            "value": 391_000_000_000,
            "period_end": "2024-09-28",
            "period_type": "annual",
            "dimension": "ARY",
            "fiscal_year": 2024,
            "fiscal_quarter": "FY",
            "filing_date": "2024-11-01",
        },
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "metric": "revenue",
            "value": 391_000_000_000,
            "period_end": "2024-09-28",
            "period_type": "annual",
            "dimension": "ARY",
            "fiscal_year": 2025,
            "fiscal_quarter": "FY",
            "filing_date": "2025-10-31",
        },
    ]

    wide_rows = pivot_fundamentals_rows(rows)
    by_date = {row["calendardate"]: row for row in wide_rows}

    assert len(by_date) == 2
    latest = by_date["2025-09-27"]
    assert latest["revenue"] == 416_000_000_000
    assert latest["netinc"] == 112_000_000_000
    assert latest["sharesbas"] == 14_776_353_000
    assert "2025-10-17" not in by_date


def test_collapse_narrow_annual_prefers_full_year_revenue_snapshot():
    rows = [
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "metric": "revenue",
            "value": 42_000_000_000,
            "period_end": "2016-06-25",
            "period_type": "annual",
            "dimension": "ARY",
            "fiscal_year": 2016,
            "fiscal_quarter": "Q3",
            "filing_date": "2016-10-26",
        },
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "metric": "revenue",
            "value": 215_000_000_000,
            "period_end": "2016-09-24",
            "period_type": "annual",
            "dimension": "ARY",
            "fiscal_year": 2016,
            "fiscal_quarter": "FY",
            "filing_date": "2016-10-26",
        },
    ]

    collapsed = collapse_narrow_fundamentals_rows(rows, annual=True)
    assert len(collapsed) == 1
    assert collapsed[0]["period_end"] == "2016-09-24"
    assert collapsed[0]["value"] == 215_000_000_000


def _aapl_fy_row(period_end: str, fiscal_year: int, revenue: float, **metrics: float) -> list[dict]:
    base = {
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "period_end": period_end,
        "period_type": "annual",
        "dimension": "ARY",
        "fiscal_year": fiscal_year,
        "fiscal_quarter": "FY",
        "filing_date": f"{fiscal_year}-10-31",
    }
    rows = [
        {**base, "metric": "revenue", "value": revenue},
        {**base, "metric": "netinc", "value": revenue * 0.25},
        {**base, "metric": "assets", "value": revenue * 2},
        {**base, "metric": "liabilities", "value": revenue},
        {**base, "metric": "ncfo", "value": revenue * 0.3},
        {**base, "metric": "eps", "value": 12.3},
        {**base, "metric": "gp", "value": revenue * 0.4},
        {**base, "metric": "opinc", "value": revenue * 0.3},
        {**base, "metric": "cashneq", "value": revenue * 0.2},
    ]
    for metric, value in metrics.items():
        rows.append({**base, "metric": metric, "value": value})
    return rows


def test_filter_canonical_annual_wide_rows_drops_interim_orphans():
    collapsed = collapse_narrow_fundamentals_rows(
        _aapl_fy_row("2012-09-29", 2012, 156_000_000_000, debt=0.0)
        + [
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "metric": "eps",
                "value": 12.3,
                "period_end": "2012-03-31",
                "period_type": "annual",
                "dimension": "ARY",
                "fiscal_year": 2012,
                "fiscal_quarter": "Q2",
                "filing_date": "2012-05-01",
            },
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "metric": "ncfdiv",
                "value": 2_500_000_000,
                "period_end": "2011-12-31",
                "period_type": "annual",
                "dimension": "ARY",
                "fiscal_year": 2011,
                "fiscal_quarter": "Q1",
                "filing_date": "2012-02-01",
            },
        ],
        annual=True,
    )
    wide_rows = pivot_fundamentals_rows(collapsed)
    dates_before = {row["calendardate"] for row in wide_rows}
    assert "2012-09-29" in dates_before
    assert "2011-12-31" in dates_before
    assert len(dates_before) > 1

    canonical = filter_canonical_annual_wide_rows(wide_rows)
    dates = {row["calendardate"] for row in canonical}
    assert dates == {"2012-09-29"}
    assert canonical[0]["revenue"] == 156_000_000_000


def test_pivot_canonical_annual_drops_interim_orphan_columns():
    collapsed = collapse_narrow_fundamentals_rows(
        _aapl_fy_row("2012-09-29", 2012, 156_000_000_000)
        + _aapl_fy_row("2011-09-24", 2011, 108_000_000_000)
        + [
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "metric": "eps",
                "value": 12.3,
                "period_end": "2012-03-31",
                "period_type": "annual",
                "dimension": "ARY",
                "fiscal_year": 2012,
                "fiscal_quarter": "Q2",
                "filing_date": "2012-05-01",
            },
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "metric": "ncfdiv",
                "value": 2_500_000_000,
                "period_end": "2011-12-31",
                "period_type": "annual",
                "dimension": "ARY",
                "fiscal_year": 2011,
                "fiscal_quarter": "Q1",
                "filing_date": "2012-02-01",
            },
        ],
        annual=True,
    )
    wide_rows = pivot_fundamentals_rows(collapsed, canonical_annual=True)
    dates = [row["calendardate"] for row in wide_rows]
    assert dates == ["2012-09-29", "2011-09-24"]
    assert all(row.get("revenue") is not None for row in wide_rows)
    assert all(
        sum(1 for metric in ("revenue", "netinc", "assets", "liabilities", "ncfo", "gp", "opinc", "cashneq")
            if row.get(metric) is not None) >= CANONICAL_ANNUAL_MIN_STATEMENT_METRICS
        for row in wide_rows
    )


def test_filter_canonical_annual_keeps_distinct_periods_when_fiscal_year_restatement_tags_overlap():
    """SEC restatements can tag multiple period_end dates with the same fiscal_year."""
    wide_rows = [
        {
            "ticker": "AAPL",
            "calendardate": "2025-09-27",
            "dimension": "ARY",
            "_fiscal_year": 2025,
            "revenue": 416_000_000_000,
            "netinc": 100_000_000_000,
            "assets": 800_000_000_000,
            "liabilities": 400_000_000_000,
            "ncfo": 120_000_000_000,
            "gp": 180_000_000_000,
            "opinc": 130_000_000_000,
            "cashneq": 50_000_000_000,
            "eps": 2.5,
        },
        {
            "ticker": "AAPL",
            "calendardate": "2024-09-28",
            "dimension": "ARY",
            "_fiscal_year": 2025,
            "revenue": 391_000_000_000,
            "netinc": 95_000_000_000,
            "assets": 750_000_000_000,
            "liabilities": 380_000_000_000,
            "ncfo": 110_000_000_000,
            "gp": 170_000_000_000,
            "opinc": 120_000_000_000,
            "cashneq": 45_000_000_000,
            "eps": 2.3,
        },
    ]
    canonical = filter_canonical_annual_wide_rows(wide_rows)
    assert {row["calendardate"] for row in canonical} == {"2025-09-27", "2024-09-28"}


def test_research_ticker_detail_annual_periods_are_canonical(client):
    from app.db import get_db
    from app.repositories import Repository

    with client.application.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "AAPL", "name": "Apple Inc", "cik": "0000320193"}])
        company = repo.get_company_by_ticker("AAPL")
        assert company is not None
        records = []
        for narrow in collapse_narrow_fundamentals_rows(
            _aapl_fy_row("2012-09-29", 2012, 156_000_000_000)
            + _aapl_fy_row("2011-09-24", 2011, 108_000_000_000)
            + [
                {
                    "ticker": "AAPL",
                    "company_name": "Apple Inc.",
                    "metric": "eps",
                    "value": 12.3,
                    "period_end": "2012-03-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2012,
                    "fiscal_quarter": "Q2",
                    "filing_date": "2012-05-01",
                },
            ],
            annual=True,
        ):
            records.append(
                {
                    "company_id": company["id"],
                    "metric": narrow["metric"],
                    "value": narrow["value"],
                    "unit": None,
                    "period_end": narrow["period_end"],
                    "period_type": narrow["period_type"],
                    "dimension": narrow["dimension"],
                    "fiscal_year": narrow.get("fiscal_year"),
                    "fiscal_quarter": narrow.get("fiscal_quarter"),
                    "filing_date": narrow.get("filing_date"),
                    "form": "10-K",
                    "accession": "test",
                    "source": "test",
                }
            )
        repo.upsert_fundamentals(records)

    response = client.get("/api/research/ticker/AAPL?dimension=ARY")
    assert response.status_code == 200
    payload = response.get_json()
    period_ends = [period["periodEnd"] for period in payload["periods"]]
    assert period_ends == ["2012-09-29", "2011-09-24"]
    assert "2012-03-31" not in period_ends


def test_get_financials_payload_ary_collapses_interim_periods():
    from app.services.fundamentals import FundamentalsService

    narrow_rows = (
        _aapl_fy_row("2012-09-29", 2012, 156_000_000_000)
        + _aapl_fy_row("2011-09-24", 2011, 108_000_000_000)
        + [
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "metric": "eps",
                "value": 12.3,
                "period_end": "2012-03-31",
                "period_type": "annual",
                "dimension": "ARY",
                "fiscal_year": 2012,
                "fiscal_quarter": "Q2",
                "filing_date": "2012-05-01",
            },
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "metric": "ncfdiv",
                "value": 2_500_000_000,
                "period_end": "2011-12-31",
                "period_type": "annual",
                "dimension": "ARY",
                "fiscal_year": 2011,
                "fiscal_quarter": "Q1",
                "filing_date": "2012-02-01",
            },
        ]
    )

    class FakeRepo:
        def fetch_fundamentals_rows(self, tickers, gte=None, dimension=None):
            if dimension != "ARY":
                return []
            rows = []
            for item in narrow_rows:
                if gte and (item.get("period_end") or "") < gte:
                    continue
                rows.append({**item, "company_name": "Apple Inc."})
            return rows

        def fetch_prices(self, ticker, limit=None):
            return []

        def fetch_prices_by_period_ends(self, ticker, period_ends):
            return {period_end: None for period_end in period_ends}

    payload = FundamentalsService(FakeRepo(), None).get_financials_payload(
        ["AAPL"],
        gte=None,
        dimension="ARY",
        most_recent=False,
    )
    period_ends = [item["periodEnd"] for item in payload["periodSeries"]["AAPL"]]
    assert period_ends == ["2012-09-29", "2011-09-24"]
    assert "2012-03-31" not in period_ends
    assert "2011-12-31" not in period_ends


def test_history_dimension_for_fetch_maps_mr_to_ar():
    from app.services.fundamentals import history_dimension_for_fetch

    assert history_dimension_for_fetch("MRY") == "ARY"
    assert history_dimension_for_fetch("MRQ") == "ARQ"
    assert history_dimension_for_fetch("ARY") == "ARY"


def test_research_ticker_mry_uses_financials_history(client):
    from app.db import get_db
    from app.repositories import Repository

    with client.application.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "AAPL", "name": "Apple Inc", "cik": "0000320193"}])
        company = repo.get_company_by_ticker("AAPL")
        assert company is not None
        records = []
        for narrow in collapse_narrow_fundamentals_rows(
            _aapl_fy_row("2012-09-29", 2012, 156_000_000_000)
            + _aapl_fy_row("2011-09-24", 2011, 108_000_000_000),
            annual=True,
        ):
            records.append(
                {
                    "company_id": company["id"],
                    "metric": narrow["metric"],
                    "value": narrow["value"],
                    "unit": None,
                    "period_end": narrow["period_end"],
                    "period_type": narrow["period_type"],
                    "dimension": narrow["dimension"],
                    "fiscal_year": narrow.get("fiscal_year"),
                    "fiscal_quarter": narrow.get("fiscal_quarter"),
                    "filing_date": narrow.get("filing_date"),
                    "form": "10-K",
                    "accession": "test",
                    "source": "test",
                }
            )
        repo.upsert_fundamentals(records)

    research = client.get("/api/research/ticker/AAPL?dimension=MRY")
    assert research.status_code == 200
    research_ends = [period["periodEnd"] for period in research.get_json()["periods"]]
    assert research_ends == ["2012-09-29", "2011-09-24"]

    with client.application.app_context():
        from app.db import get_db
        from app.repositories import Repository
        from app.services.fundamentals import get_financials_period_series

        repo = Repository(get_db())
        shared_ends = [
            period["periodEnd"]
            for period in get_financials_period_series(repo, ["AAPL"], dimension="MRY", gte=None).get("AAPL", [])
        ]
        assert shared_ends == research_ends
