"""Tests for derived catalyst calendar."""

from __future__ import annotations

from datetime import date, timedelta

from app.db import get_db
from app.repositories import Repository
from app.services.catalyst_calendar import (
    _project_next_earnings,
    derive_earnings_dates_from_fundamentals,
    fetch_upcoming_catalysts,
    refresh_derived_catalyst_calendar,
    upsert_catalyst_calendar,
)


def _seed_fundamental_period(repo: Repository, ticker: str, period_end: str) -> None:
    repo.upsert_companies([{"ticker": ticker, "name": f"{ticker} Co", "cik": "0000000001"}])
    company = repo.get_company_by_ticker(ticker)
    repo.upsert_fundamentals(
        [
            {
                "company_id": company["id"],
                "metric": "revenue",
                "value": 1_000_000.0,
                "unit": "USD",
                "period_end": period_end,
                "period_type": "annual",
                "dimension": "ARY",
                "fiscal_year": 2024,
                "fiscal_quarter": "FY",
                "filing_date": period_end,
                "form": "10-K",
                "accession": "1",
                "source": "sec_companyfacts",
                "taxonomy": "us-gaap",
                "xbrl_concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
            }
        ]
    )


def test_project_next_earnings_rolls_past_stale_estimate():
    last = date(2026, 3, 11)
    nxt = _project_next_earnings(last, anchor=date(2026, 6, 24))
    assert nxt >= date(2026, 6, 21)


def test_fetch_upcoming_includes_recent_past_dates(app):
    with app.app_context():
        repo = Repository(get_db())
        past = (date.today() - timedelta(days=2)).isoformat()
        upsert_catalyst_calendar(
            repo,
            [{
                "ticker": "PAST1",
                "event_type": "earnings",
                "event_date": past,
                "source": "test",
                "confidence": 0.7,
            }],
        )
        rows = fetch_upcoming_catalysts(repo, limit=10, horizon_days=30, include_past_days=3)
        assert any(row["ticker"] == "PAST1" for row in rows)


def test_derive_earnings_dates_from_fundamentals(app):
    with app.app_context():
        repo = Repository(get_db())
        period_end = (date.today() - timedelta(days=80)).isoformat()
        _seed_fundamental_period(repo, "CAT1", period_end)
        records = derive_earnings_dates_from_fundamentals(repo, limit=10)
        tickers = {row["ticker"] for row in records}
        assert "CAT1" in tickers


def test_refresh_derived_catalyst_calendar_idempotent(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_fundamental_period(repo, "CAT2", date.today().isoformat())
        first = refresh_derived_catalyst_calendar(repo, limit=20)
        second = refresh_derived_catalyst_calendar(repo, limit=20)
        assert first["upserted"] >= 1
        assert second["upserted"] >= 1
        upcoming = fetch_upcoming_catalysts(repo, limit=10, horizon_days=120)
        assert any(item["ticker"] == "CAT2" for item in upcoming)


def test_upsert_catalyst_calendar(app):
    with app.app_context():
        repo = Repository(get_db())
        event_date = (date.today() + timedelta(days=10)).isoformat()
        written = upsert_catalyst_calendar(
            repo,
            [
                {
                    "ticker": "UPS1",
                    "event_type": "earnings",
                    "event_date": event_date,
                    "source": "test",
                    "confidence": 0.6,
                    "details": {"method": "test"},
                }
            ],
        )
        assert written == 1
        rows = fetch_upcoming_catalysts(repo, limit=5, horizon_days=30)
        assert any(row["ticker"] == "UPS1" for row in rows)
