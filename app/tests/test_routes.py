from __future__ import annotations

from app.db import get_db
from app.repositories import Repository


def test_search_and_financial_routes_use_sqlite_cache(app, client):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "AAPL", "name": "Apple Inc", "cik": "0000320193"}])
        company = repo.get_company_by_ticker("AAPL")
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
                    "xbrl_concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
                },
                {
                    "company_id": company["id"],
                    "metric": "eps",
                    "value": 5.0,
                    "unit": "USD/shares",
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
                    "xbrl_concept": "EarningsPerShareDiluted",
                },
                {
                    "company_id": company["id"],
                    "metric": "sharesbas",
                    "value": 10.0,
                    "unit": "shares",
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                    "form": "10-K",
                    "accession": "1",
                    "source": "sec_companyfacts",
                    "taxonomy": "dei",
                    "xbrl_concept": "EntityCommonStockSharesOutstanding",
                },
            ]
        )
        repo.upsert_prices(
            "AAPL",
            [{"date": "2025-01-20", "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "volume": 100}],
            source="test",
        )

    search_response = client.get("/api/search?q=AAPL")
    assert search_response.status_code == 200
    assert search_response.get_json()[0]["ticker"] == "AAPL"

    financial_response = client.get("/api/ticker/financials?ticker=AAPL&mostRecent=true")
    assert financial_response.status_code == 200
    payload = financial_response.get_json()
    assert payload["metrics"]["AAPL"]["marketCap"] == 50.0
    assert payload["raw"]["datatable"]["data"][0][0] == "AAPL"
