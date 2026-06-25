"""Tests for portfolio watch digest."""

from __future__ import annotations

from datetime import date

from app.db import get_db
from app.repositories import Repository
from app.services.watch_digest import build_portfolio_watch_digest


def test_watch_digest_summarizes_portfolio_signals(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "MU", "name": "Micron", "cik": "0000000001"}])
        today = date.today().isoformat()
        signals = [
            {
                "ticker": "MU",
                "signalType": "earnings_today",
                "eventDate": today,
                "researchImportance": 0.8,
                "whyItMatters": "Earnings today",
            },
            {
                "ticker": "MU",
                "signalType": "going_concern_8k",
                "eventDate": today,
                "researchImportance": 0.9,
                "whyItMatters": "Going concern",
            },
            {
                "ticker": "AAPL",
                "signalType": "rank_up",
                "eventDate": today,
                "researchImportance": 0.6,
                "whyItMatters": "Rank up",
            },
        ]
        digest = build_portfolio_watch_digest(repo, signals, portfolio_tickers=["MU", "MSFT"])
        assert digest["portfolioCount"] == 2
        assert digest["signalCount"] == 3
        assert digest["tickersWithSignals"] == 1
        assert "MSFT" in digest["tickersQuiet"]
        assert any(item["ticker"] == "MU" for item in digest["earningsImminent"])
        assert any(item["signalType"] == "going_concern_8k" for item in digest["alerts"])
        assert digest["summaryLines"]
