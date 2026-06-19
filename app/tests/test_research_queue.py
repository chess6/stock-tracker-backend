"""Research queue — detectors, API routes, and worker integration."""

from __future__ import annotations

from datetime import date, timedelta

from app.db import get_db
from app.repositories import Repository
from app.services.feature_flags import is_enabled
from app.services.research_queue import (
    _detect_new_catalysts,
    _detect_new_insider_clusters,
    _detect_narrative_divergence,
    _detect_score_improvements,
    build_research_queue,
)


def _enable_queue_flag(repo: Repository) -> None:
    repo.set_config("experimental_research_queue", True)


def _seed_company(repo: Repository, ticker: str) -> dict:
    repo.upsert_companies(
        [{"ticker": ticker, "name": f"{ticker} Co", "cik": f"000000{ticker[-4:]}"}]
    )
    return repo.get_company_by_ticker(ticker)


def test_research_queue_table_exists(app):
    with app.app_context():
        repo = Repository(get_db())
        row = repo.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='research_queue'"
        ).fetchone()
        assert row is not None


def test_rank_change_detector_creates_queue_item(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_company(repo, "RQUP")
        today = date.today().isoformat()
        prior = (date.today() - timedelta(days=7)).isoformat()
        repo.upsert_company_rank_snapshots(
            [
                {
                    "ticker": "RQUP",
                    "composite": "deep_value",
                    "snapshot_date": prior,
                    "composite_score": 0.5,
                    "rank_in_universe": 30,
                },
                {
                    "ticker": "RQUP",
                    "composite": "deep_value",
                    "snapshot_date": today,
                    "composite_score": 0.7,
                    "rank_in_universe": 20,
                },
            ]
        )
        result = build_research_queue(repo, limit=20)
        assert result["detected"] >= 1
        items = [item for item in result["items"] if item["ticker"] == "RQUP"]
        assert any(item["eventType"] == "rank_up" for item in items)


def test_insider_cluster_detector(app):
    with app.app_context():
        repo = Repository(get_db())
        company = _seed_company(repo, "INSQ")
        today = date.today()
        repo.upsert_insider_cluster_analysis(
            company["id"],
            [
                {
                    "window_start": (today - timedelta(days=14)).isoformat(),
                    "window_end": today.isoformat(),
                    "buy_count": 3,
                    "sell_count": 0,
                    "unique_buyers": 2,
                    "total_buy_value": 120000.0,
                    "total_sell_value": 0.0,
                    "avg_buy_price": 50.0,
                    "intensity_score": 0.65,
                }
            ],
        )
        events = _detect_new_insider_clusters(repo)
        assert any(
            event["ticker"] == "INSQ" and event["event_type"] == "new_insider_cluster"
            for event in events
        )


def test_narrative_divergence_detector(app):
    with app.app_context():
        repo = Repository(get_db())
        snapshot_date = date.today().isoformat()
        repo.upsert_company_narrative_snapshots(
            [
                {
                    "ticker": "NARD",
                    "snapshot_date": snapshot_date,
                    "divergence_signal": "rerating_candidate",
                    "divergence_score": 0.82,
                    "states": [],
                    "emerging_situations": [],
                }
            ]
        )
        events = _detect_narrative_divergence(repo)
        assert any(
            event["ticker"] == "NARD" and event["event_type"] == "narrative_divergence"
            for event in events
        )


def test_score_improvement_detector(app):
    with app.app_context():
        repo = Repository(get_db())
        company = _seed_company(repo, "SCRQ")
        repo.upsert_company_scores(
            company["id"],
            [
                {"period_end": "2023-12-31", "dimension": "ARY", "piotroski_f": 4},
                {"period_end": "2024-12-31", "dimension": "ARY", "piotroski_f": 7},
            ],
        )
        events = _detect_score_improvements(repo)
        match = next(event for event in events if event["ticker"] == "SCRQ")
        assert match["event_type"] == "score_improvement"
        assert match["details"]["improvement"] == 3


def test_new_catalyst_detector(app):
    with app.app_context():
        repo = Repository(get_db())
        company = _seed_company(repo, "CATQ")
        today = date.today().isoformat()
        article_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/catq-earnings",
                "url_hash": "hash-catq-earnings",
                "title": "CATQ reports earnings beat",
                "summary": "Quarterly earnings",
                "published_at": f"{today}T12:00:00Z",
                "fetched_at": f"{today}T13:00:00Z",
                "raw_source": "test",
            },
            skip_dedup=True,
        )
        repo.link_article_company(article_id, company["id"], "cashtag", 0.9)

        class Event:
            def __init__(self, event_type: str, confidence: float, method: str) -> None:
                self.event_type = event_type
                self.confidence = confidence
                self.method = method

        repo.replace_article_events(article_id, [Event("earnings_beat", 0.92, "rules")])
        events = _detect_new_catalysts(repo)
        assert any(
            event["ticker"] == "CATQ" and event["event_type"] == "new_catalyst"
            for event in events
        )


def test_build_research_queue_is_idempotent(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_company(repo, "IDEM")
        today = date.today().isoformat()
        prior = (date.today() - timedelta(days=5)).isoformat()
        repo.upsert_company_rank_snapshots(
            [
                {
                    "ticker": "IDEM",
                    "composite": "deep_value",
                    "snapshot_date": prior,
                    "composite_score": 0.4,
                    "rank_in_universe": 40,
                },
                {
                    "ticker": "IDEM",
                    "composite": "deep_value",
                    "snapshot_date": today,
                    "composite_score": 0.6,
                    "rank_in_universe": 25,
                },
            ]
        )

        build_research_queue(repo, limit=50)
        count_after_first = repo.conn.execute(
            "SELECT COUNT(*) FROM research_queue WHERE ticker = 'IDEM'"
        ).fetchone()[0]
        build_research_queue(repo, limit=50)
        count_after_second = repo.conn.execute(
            "SELECT COUNT(*) FROM research_queue WHERE ticker = 'IDEM'"
        ).fetchone()[0]

        assert count_after_first > 0
        assert count_after_second == count_after_first


def test_queue_route_requires_feature_flag(app, client):
    response = client.get("/api/research/queue")
    assert response.status_code == 404
    assert response.get_json()["flag"] == "experimental_research_queue"


def test_queue_route_returns_items_when_enabled(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _enable_queue_flag(repo)
        repo.upsert_research_queue_items(
            [
                {
                    "ticker": "AAPL",
                    "event_type": "new_catalyst",
                    "event_date": date.today().isoformat(),
                    "priority": 40,
                    "details": {"catalystType": "earnings"},
                }
            ]
        )

    response = client.get("/api/research/queue?limit=10")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["returned"] >= 1
    assert any(item["ticker"] == "AAPL" for item in payload["items"])


def test_queue_route_filters_event_types(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _enable_queue_flag(repo)
        event_date = date.today().isoformat()
        repo.upsert_research_queue_items(
            [
                {
                    "ticker": "FLT1",
                    "event_type": "rank_up",
                    "event_date": event_date,
                    "priority": 30,
                    "details": {"rankDelta": 8},
                },
                {
                    "ticker": "FLT2",
                    "event_type": "new_catalyst",
                    "event_date": event_date,
                    "priority": 40,
                    "details": {"catalystType": "merger"},
                },
            ]
        )

    response = client.get("/api/research/queue?limit=10&event_types=new_catalyst")
    assert response.status_code == 200
    items = response.get_json()["items"]
    assert items
    assert all(item["eventType"] == "new_catalyst" for item in items)
    assert any(item["ticker"] == "FLT2" for item in items)
    assert all(item["ticker"] != "FLT1" for item in items)


def test_queue_dismiss_route(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _enable_queue_flag(repo)
        event_date = date.today().isoformat()
        repo.upsert_research_queue_items(
            [
                {
                    "ticker": "MSFT",
                    "event_type": "score_improvement",
                    "event_date": event_date,
                    "priority": 45,
                    "details": {"improvement": 3},
                }
            ]
        )

    response = client.post(
        "/api/research/queue/MSFT/dismiss",
        json={"event_type": "score_improvement", "event_date": event_date},
    )
    assert response.status_code == 200
    assert response.get_json()["dismissed"] == 1

    hidden = client.get("/api/research/queue?limit=10")
    assert all(item["ticker"] != "MSFT" for item in hidden.get_json()["items"])


def test_build_research_queue_worker_skips_when_flag_off(app):
    with app.app_context():
        from app.workers.handlers import build_context, build_handlers

        conn = get_db()
        ctx = build_context(
            {
                "conn": conn,
                "sec_user_agent": "TestApp test@example.com",
                "sec_base_url": "https://data.sec.gov",
                "sec_company_tickers_url": "https://www.sec.gov/files/company_tickers.json",
                "request_timeout": 20,
                "news_http_ttl_seconds": 3600,
                "default_tickers": ["AAPL"],
            }
        )
        handlers = build_handlers(ctx)
        result = handlers["build_research_queue"]({})
        assert result == {"skipped": True, "reason": "feature_flag_disabled"}


def test_feature_flag_default_off():
    assert is_enabled("experimental_research_queue") is False
