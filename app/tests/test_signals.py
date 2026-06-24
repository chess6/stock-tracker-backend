"""Tests for unified Signal read-layer and /api/signals route."""

from __future__ import annotations

from datetime import date, timedelta

from app.db import get_db
from app.repositories import Repository
from app.services.signals import build_dedup_key, get_signals


def _enable_signals_flag(repo: Repository) -> None:
    repo.set_config("experimental_signals", True)


def _seed_company(repo: Repository, ticker: str) -> dict:
    repo.upsert_companies([{"ticker": ticker, "name": f"{ticker} Co", "cik": f"000000{ticker[-4:]}"}])
    return repo.get_company_by_ticker(ticker)


def test_build_dedup_key_normalizes_ticker_and_type():
    key = build_dedup_key("aapl", "Insider_Cluster_Buy", "2026-06-01")
    assert key == "AAPL:insider_cluster_buy:2026-06-01"


def test_get_signals_merges_queue_and_edgar(app):
    with app.app_context():
        repo = Repository(get_db())
        company = _seed_company(repo, "SIG1")
        event_date = date.today().isoformat()
        repo.upsert_research_queue_items(
            [
                {
                    "ticker": "SIG1",
                    "event_type": "rank_up",
                    "event_date": event_date,
                    "priority": 30,
                    "details": {"rankDelta": 10, "composite": "deep_value"},
                }
            ]
        )
        repo.upsert_company_edgar_events(
            company["id"],
            [
                {
                    "form_type": "8-K",
                    "item_number": "4.02",
                    "filed_date": event_date,
                    "event_type": "restatement",
                    "summary": "Financial restatement",
                    "accession": "0001234567-26-000001",
                }
            ],
        )

        payload = get_signals(repo, limit=20)
        tickers = {item["ticker"] for item in payload["items"]}
        types = {item["signalType"] for item in payload["items"]}
        assert "SIG1" in tickers
        assert "rank_up" in types
        assert "restatement" in types
        assert all("researchImportance" in item for item in payload["items"])
        assert all("whyItMatters" in item for item in payload["items"])
        assert all("dedupKey" in item for item in payload["items"])


def test_get_signals_dedupes_same_ticker_type_date(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_company(repo, "DEDP")
        event_date = date.today().isoformat()
        repo.upsert_research_queue_items(
            [
                {
                    "ticker": "DEDP",
                    "event_type": "new_insider_cluster",
                    "event_date": event_date,
                    "priority": 25,
                    "details": {"intensityScore": 0.7, "uniqueBuyers": 3},
                }
            ]
        )
        company = repo.get_company_by_ticker("DEDP")
        repo.upsert_insider_cluster_analysis(
            company["id"],
            [
                {
                    "window_start": (date.today() - timedelta(days=14)).isoformat(),
                    "window_end": event_date,
                    "buy_count": 4,
                    "sell_count": 0,
                    "unique_buyers": 3,
                    "total_buy_value": 500000.0,
                    "total_sell_value": 0.0,
                    "avg_buy_price": 10.0,
                    "intensity_score": 0.7,
                }
            ],
        )

        payload = get_signals(repo, limit=20)
        insider_items = [
            item
            for item in payload["items"]
            if item["ticker"] == "DEDP" and item["signalType"] == "insider_cluster_buy"
        ]
        assert len(insider_items) == 1


def test_get_signals_sorted_by_research_importance(app):
    with app.app_context():
        repo = Repository(get_db())
        company = _seed_company(repo, "SORT")
        event_date = date.today().isoformat()
        repo.upsert_research_queue_items(
            [
                {
                    "ticker": "SORT",
                    "event_type": "rank_up",
                    "event_date": event_date,
                    "priority": 40,
                    "details": {"rankDelta": 3},
                }
            ]
        )
        repo.upsert_company_edgar_events(
            company["id"],
            [
                {
                    "form_type": "8-K",
                    "item_number": "1.03",
                    "filed_date": event_date,
                    "event_type": "bankruptcy",
                    "summary": "Bankruptcy filing",
                    "accession": "0001234567-26-000099",
                }
            ],
        )

        payload = get_signals(repo, limit=10)
        scores = [item["researchImportance"] for item in payload["items"]]
        assert scores == sorted(scores, reverse=True)


def test_get_signals_caps_rank_move_flood(app):
    with app.app_context():
        repo = Repository(get_db())
        event_date = date.today().isoformat()
        rows = []
        for idx in range(30):
            ticker = f"RK{idx:02d}"
            _seed_company(repo, ticker)
            rows.append(
                {
                    "ticker": ticker,
                    "event_type": "rank_up",
                    "event_date": event_date,
                    "priority": 20 + idx,
                    "details": {"rankDelta": 5 + (idx % 3), "composite": "deep_value"},
                }
            )
        repo.upsert_research_queue_items(rows)
        payload = get_signals(repo, limit=50)
        rank_items = [item for item in payload["items"] if item["signalType"] == "rank_up"]
        assert len(rank_items) <= 20


def test_signals_route_requires_feature_flag(app, client):
    response = client.get("/api/signals")
    assert response.status_code == 404
    assert response.get_json()["flag"] == "experimental_signals"


def test_signals_route_returns_unified_payload(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _enable_signals_flag(repo)
        event_date = date.today().isoformat()
        repo.upsert_research_queue_items(
            [
                {
                    "ticker": "API1",
                    "event_type": "narrative_divergence",
                    "event_date": event_date,
                    "priority": 35,
                    "details": {"divergenceSignal": "rerating_candidate", "divergenceScore": 0.8},
                }
            ]
        )

    response = client.get("/api/signals?limit=10")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["returned"] >= 1
    assert "meta" in payload
    assert any(item["ticker"] == "API1" for item in payload["items"])
