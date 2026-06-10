from __future__ import annotations

from app.db import get_db
from app.repositories import Repository
from app.services.fundamentals import FundamentalsService
from app.services.safe_overwrite import (
    partition_fundamentals_records,
    should_skip_fundamental_overwrite,
)


def _fundamental_record(
    *,
    company_id: int,
    metric: str = "revenue",
    period_end: str = "2024-12-31",
    filing_date: str = "2025-01-20",
    source_updated_at: str | None = None,
) -> dict:
    return {
        "company_id": company_id,
        "metric": metric,
        "value": 1000.0,
        "unit": "USD",
        "period_end": period_end,
        "period_type": "annual",
        "dimension": "ARY",
        "fiscal_year": 2024,
        "fiscal_quarter": "FY",
        "filing_date": filing_date,
        "form": "10-K",
        "accession": "acc-1",
        "source": "sec_companyfacts",
        "taxonomy": "us-gaap",
        "xbrl_concept": "Revenues",
        "source_updated_at": source_updated_at or filing_date,
    }


def test_should_skip_fundamental_overwrite_rules():
    assert should_skip_fundamental_overwrite("2025-01-20", "2025-01-21") is True
    assert should_skip_fundamental_overwrite("2025-01-22", "2025-01-21") is False
    assert should_skip_fundamental_overwrite("2025-01-21", "2025-01-21") is True
    assert should_skip_fundamental_overwrite("2025-01-20", "2025-01-21", force_refresh=True) is False
    assert should_skip_fundamental_overwrite(None, "2025-01-21") is False


def test_partition_fundamentals_records(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "SAFE", "name": "Safe Co", "cik": "0000000123"}])
        company = repo.get_company_by_ticker("SAFE")
        stored = {
            ("revenue", "2024-12-31", "ARY", "2025-01-20", "Revenues"): "2025-02-01",
        }
        incoming = [
            _fundamental_record(company_id=company["id"], filing_date="2025-01-20"),
            _fundamental_record(
                company_id=company["id"],
                metric="netinc",
                filing_date="2025-02-15",
                source_updated_at="2025-02-15",
            ),
        ]
        upsert, skipped = partition_fundamentals_records(incoming, stored)
        assert len(upsert) == 1
        assert upsert[0]["metric"] == "netinc"
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "older_or_equal_source"


def test_refresh_fundamentals_dry_run_does_not_mutate(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "DRY", "name": "Dry Co", "cik": "0000000456"}])
        company = repo.get_company_by_ticker("DRY")
        repo.upsert_fundamentals([_fundamental_record(company_id=company["id"])])
        before = repo.conn.execute("SELECT COUNT(*) FROM fundamentals WHERE company_id = ?", (company["id"],)).fetchone()[0]

        class StubSec:
            def fetch_company_facts(self, cik: str) -> dict:
                return {
                    "facts": {
                        "us-gaap": {
                            "Revenues": {
                                "units": {
                                    "USD": [
                                        {
                                            "val": 2000.0,
                                            "end": "2024-12-31",
                                            "filed": "2025-01-20",
                                            "form": "10-K",
                                            "accn": "acc-1",
                                            "fy": 2024,
                                            "fp": "FY",
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }

            def fetch_submissions(self, cik: str) -> dict:
                return {"filings": {"recent": {}}}

        service = FundamentalsService(repo, StubSec())
        payload = service.refresh_fundamentals(["DRY"], dry_run=True)
        after = repo.conn.execute("SELECT COUNT(*) FROM fundamentals WHERE company_id = ?", (company["id"],)).fetchone()[0]

        assert payload["dryRun"] is True
        assert payload["plannedUpserts"] is not None
        assert before == after == 1


def test_refresh_fundamentals_preserves_manual_newer_timestamp(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "MANU", "name": "Manual Co", "cik": "0000000789"}])
        company = repo.get_company_by_ticker("MANU")
        repo.upsert_fundamentals([_fundamental_record(company_id=company["id"])])
        repo.conn.execute(
            """
            UPDATE fundamentals
            SET source_updated_at = ?, value = 9999.0
            WHERE company_id = ? AND metric = 'revenue'
            """,
            ("2099-01-01", company["id"]),
        )
        repo.conn.commit()

        class StubSec:
            def fetch_company_facts(self, cik: str) -> dict:
                return {
                    "facts": {
                        "us-gaap": {
                            "Revenues": {
                                "units": {
                                    "USD": [
                                        {
                                            "val": 1.0,
                                            "end": "2024-12-31",
                                            "filed": "2025-01-20",
                                            "form": "10-K",
                                            "accn": "acc-1",
                                            "fy": 2024,
                                            "fp": "FY",
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }

            def fetch_submissions(self, cik: str) -> dict:
                return {"filings": {"recent": {}}}

        service = FundamentalsService(repo, StubSec())
        payload = service.refresh_fundamentals(["MANU"])
        row = repo.conn.execute(
            "SELECT value, source_updated_at FROM fundamentals WHERE company_id = ? AND metric = 'revenue'",
            (company["id"],),
        ).fetchone()

        assert payload["skippedOverwrites"] >= 1
        assert row["value"] == 9999.0
        assert row["source_updated_at"] == "2099-01-01"


def test_refresh_fundamentals_force_refresh_overwrites_manual(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "FORC", "name": "Force Co", "cik": "0000000321"}])
        company = repo.get_company_by_ticker("FORC")
        repo.upsert_fundamentals([_fundamental_record(company_id=company["id"])])
        repo.conn.execute(
            """
            UPDATE fundamentals
            SET source_updated_at = ?, value = 9999.0
            WHERE company_id = ? AND metric = 'revenue'
            """,
            ("2099-01-01", company["id"]),
        )
        repo.conn.commit()

        class StubSec:
            def fetch_company_facts(self, cik: str) -> dict:
                return {
                    "facts": {
                        "us-gaap": {
                            "Revenues": {
                                "units": {
                                    "USD": [
                                        {
                                            "val": 42.0,
                                            "end": "2024-12-31",
                                            "filed": "2025-01-20",
                                            "form": "10-K",
                                            "accn": "acc-1",
                                            "fy": 2024,
                                            "fp": "FY",
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }

            def fetch_submissions(self, cik: str) -> dict:
                return {"filings": {"recent": {}}}

        service = FundamentalsService(repo, StubSec())
        payload = service.refresh_fundamentals(["FORC"], force_refresh=True)
        row = repo.conn.execute(
            "SELECT value FROM fundamentals WHERE company_id = ? AND metric = 'revenue'",
            (company["id"],),
        ).fetchone()

        assert payload["forceRefresh"] is True
        assert row["value"] == 42.0


def test_refresh_fundamentals_dry_run_route(app, client, monkeypatch):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "API", "name": "API Co", "cik": "0000000111"}])
        company = repo.get_company_by_ticker("API")
        repo.upsert_fundamentals([_fundamental_record(company_id=company["id"])])
        before = repo.conn.execute("SELECT COUNT(*) FROM fundamentals WHERE company_id = ?", (company["id"],)).fetchone()[0]

    monkeypatch.setattr(
        "app.clients.sec.SecClient.fetch_company_facts",
        lambda self, cik: {"facts": {"us-gaap": {}}},
    )
    monkeypatch.setattr(
        "app.clients.sec.SecClient.fetch_submissions",
        lambda self, cik: {"filings": {"recent": {}}},
    )

    response = client.post(
        "/api/admin/refresh-fundamentals?tickers=API&dryRun=true&mode=force_refresh",
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dryRun"] is True
    assert payload["recordsWritten"] == 0

    with app.app_context():
        repo = Repository(get_db())
        company = repo.get_company_by_ticker("API")
        after = repo.conn.execute("SELECT COUNT(*) FROM fundamentals WHERE company_id = ?", (company["id"],)).fetchone()[0]
        assert before == after
