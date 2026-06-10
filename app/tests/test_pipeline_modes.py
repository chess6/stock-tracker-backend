from __future__ import annotations

import pytest

from app.db import get_db
from app.repositories import Repository
from app.services.pipeline_modes import (
    UnknownPipelineModeError,
    normalize_mode,
    resolve_fundamentals_tickers,
    resolve_prices_tickers,
)
from app.services.pipeline_refresh import PipelineRefreshService


def test_normalize_mode_accepts_aliases():
    assert normalize_mode("force-refresh") == "force_refresh"
    assert normalize_mode(None) == "force_refresh"


def test_normalize_mode_rejects_unknown():
    with pytest.raises(UnknownPipelineModeError):
        normalize_mode("not_a_mode")


def test_resolve_missing_and_stale_tickers(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies(
            [
                {"ticker": "NEW1", "name": "New One", "cik": "0000000001"},
                {"ticker": "OLD1", "name": "Old One", "cik": "0000000002"},
            ]
        )
        old = repo.get_company_by_ticker("OLD1")
        repo.upsert_fundamentals(
            [
                {
                    "company_id": old["id"],
                    "metric": "revenue",
                    "value": 100.0,
                    "unit": "USD",
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                    "form": "10-K",
                    "accession": "1",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "Revenues",
                }
            ]
        )
        repo.conn.execute(
            "UPDATE fundamentals SET updated_at = ? WHERE company_id = ?",
            ("2020-01-01T00:00:00Z", old["id"]),
        )
        repo.conn.commit()

        missing = resolve_fundamentals_tickers(repo, "refresh_missing_only", ["NEW1", "OLD1"])
        assert missing == ["NEW1"]

        stale = resolve_fundamentals_tickers(repo, "refresh_stale_only", ["NEW1", "OLD1"])
        assert stale == ["OLD1"]

        missing_prices = resolve_prices_tickers(repo, "refresh_missing_only", ["NEW1", "OLD1"])
        assert missing_prices == ["NEW1", "OLD1"]


def test_pipeline_refresh_recompute_scores_only(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "SCOR", "name": "Score Co", "cik": "0000000099"}])
        company = repo.get_company_by_ticker("SCOR")
        seed_rows = [
            ("2024-12-31", "2025-01-20", "test", 2024, "revenue", 1000.0, "Revenues"),
            ("2024-12-31", "2025-01-20", "test", 2024, "netinc", 120.0, "NetIncomeLoss"),
            ("2024-12-31", "2025-01-20", "test", 2024, "assets", 2000.0, "Assets"),
            ("2023-12-31", "2024-01-20", "test2", 2023, "revenue", 900.0, "Revenues"),
            ("2023-12-31", "2024-01-20", "test2", 2023, "netinc", 100.0, "NetIncomeLoss"),
            ("2023-12-31", "2024-01-20", "test2", 2023, "assets", 1800.0, "Assets"),
            ("2024-12-31", "2025-01-20", "test", 2024, "ncfo", 150.0, "NetCashProvidedByUsedInOperatingActivities"),
            ("2024-12-31", "2025-01-20", "test", 2024, "liabilities", 800.0, "Liabilities"),
            ("2024-12-31", "2025-01-20", "test", 2024, "equity", 1200.0, "Equity"),
            ("2024-12-31", "2025-01-20", "test", 2024, "ebit", 200.0, "Ebit"),
            ("2024-12-31", "2025-01-20", "test", 2024, "cashneq", 300.0, "Cash"),
        ]
        for period_end, filing_date, accession, fiscal_year, metric, value, concept in seed_rows:
            repo.upsert_fundamentals(
                [
                    {
                        "company_id": company["id"],
                        "metric": metric,
                        "value": value,
                        "unit": "USD",
                        "period_end": period_end,
                        "period_type": "annual",
                        "dimension": "ARY",
                        "fiscal_year": fiscal_year,
                        "fiscal_quarter": "FY",
                        "filing_date": filing_date,
                        "form": "10-K",
                        "accession": accession,
                        "source": "test",
                        "taxonomy": "us-gaap",
                        "xbrl_concept": concept,
                    }
                ]
            )
        repo.upsert_prices(
            "SCOR",
            [
                {"date": "2024-12-31", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1},
                {"date": "2023-12-31", "open": 8.0, "high": 8.0, "low": 8.0, "close": 8.0, "volume": 1},
            ],
            source="test",
        )

        class StubSec:
            def fetch_company_facts(self, cik):
                raise AssertionError("SEC should not be called in recompute_scores_only")

            def fetch_submissions(self, cik):
                raise AssertionError("SEC should not be called in recompute_scores_only")

        from app.services.fundamentals import FundamentalsService
        from app.services.prices import PricesService

        fundamentals = FundamentalsService(repo, StubSec())
        service = PipelineRefreshService(
            repo,
            fundamentals,
            PricesService(repo),
            news=_NoopNews(),
        )

        first = service.run("recompute_scores_only", tickers=["SCOR"])
        assert first["mode"] == "recompute_scores_only"
        assert first["stages"]["scores"]["recomputed"] >= 1

        second = service.run("recompute_scores_only", tickers=["SCOR"])
        assert second["stages"]["scores"]["skipped_unchanged"] == 1


class _NoopNews:
    def ingest_default_feeds(self, **kwargs):
        return {"feeds": 0}


def test_pipeline_refresh_route(app, client):
    response = client.post(
        "/api/admin/pipeline-refresh?mode=refresh_stale_only&tickers=NONE",
        json={"tickers": ["NONE"]},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["mode"] == "refresh_stale_only"
    assert payload["selection"]["fundamentalsTickers"] == 0


def test_pipeline_refresh_route_requires_tickers_for_force_refresh(app, client):
    response = client.post("/api/admin/pipeline-refresh?mode=force_refresh", json={})
    assert response.status_code == 400
    assert "requires tickers" in response.get_json()["error"]


def test_refresh_fundamentals_stale_mode_returns_empty_without_sec(app, client):
    response = client.post(
        "/api/admin/refresh-fundamentals?mode=refresh_stale_only&tickers=ZZZZ",
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["mode"] == "refresh_stale_only"
    assert payload["tickers"] == []
