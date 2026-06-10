"""Tests for sector percentile stats."""

from app.db import get_db
from app.repositories import Repository
from app.services.sector_stats import percentile_breakpoints, sector_stats_for_tickers


def _seed_sector_peers(repo: Repository) -> None:
    repo.upsert_companies([
        {"ticker": "AAA", "name": "Alpha Corp", "cik": "0000000001", "sector": "Technology"},
        {"ticker": "BBB", "name": "Beta Corp", "cik": "0000000002", "sector": "Technology"},
    ])
    for ticker, gp, revenue in [("AAA", 400.0, 1000.0), ("BBB", 600.0, 2000.0)]:
        company = repo.get_company_by_ticker(ticker)
        for metric, value in [("revenue", revenue), ("gp", gp), ("assets", 2000.0), ("equity", 800.0),
                              ("liabilities", 1200.0), ("sharesbas", 100.0), ("netinc", 80.0)]:
            repo.upsert_fundamentals([
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
                },
            ])


def test_percentile_breakpoints():
    stats = percentile_breakpoints([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert stats["count"] == 10
    assert stats["min"] == 1
    assert stats["max"] == 10
    assert stats["p20"] <= stats["p40"] <= stats["p60"] <= stats["p80"]


def test_percentile_breakpoints_empty():
    assert percentile_breakpoints([])["count"] == 0


def test_sector_stats_for_tickers(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_sector_peers(repo)
        payload = sector_stats_for_tickers(repo, ["AAA", "BBB"], metric_api_keys=["grossMargin"])
        assert "Technology" in payload["bySector"]
        assert "grossMargin" in payload["bySector"]["Technology"]
        assert payload["bySector"]["Technology"]["grossMargin"]["count"] >= 1


def test_sector_stats_route(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _seed_sector_peers(repo)

    response = client.get("/api/research/metrics/sector-stats?tickers=AAA,BBB&metrics=grossMargin")
    assert response.status_code == 200
    body = response.get_json()
    assert "bySector" in body
    assert "meta" in body
    assert "Technology" in body["bySector"]
