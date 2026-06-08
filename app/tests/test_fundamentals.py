from __future__ import annotations

from app.services.fundamentals import pivot_fundamentals_rows, resolve_financial_dimension
from app.services.sec import normalize_company_facts


def test_resolve_financial_dimension_maps_sharadar_codes():
    assert resolve_financial_dimension("MRY", False)["storage_dimension"] == "ARY"
    assert resolve_financial_dimension("MRY", False)["most_recent"] is True
    assert resolve_financial_dimension("MRQ", False)["storage_dimension"] == "ARQ"
    assert resolve_financial_dimension("ART", False)["ttm_only"] is True
    assert resolve_financial_dimension("MRT", False)["most_recent"] is True
    assert resolve_financial_dimension("MRT", False)["ttm_only"] is True


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

    payload = FundamentalsService(FakeRepo(), None).get_financials_payload(["JPM"], None, None, True)
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
