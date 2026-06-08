from __future__ import annotations

from datetime import date, timedelta

from app.db import get_db
from app.repositories import Repository
from app.services.narrative import build_narrative_analysis, clear_narrative_cache


def _seed_narrative_fixture(repo: Repository, symbol: str = "AAPL") -> None:
    repo.upsert_companies([{"ticker": symbol, "name": "Apple Inc", "cik": "0000320193"}])
    company = repo.get_company_by_ticker(symbol)
    today = date.today()

    articles = []
    for idx in range(6):
        pub_day = (today - timedelta(days=idx * 15)).isoformat()
        article_id = repo.upsert_article(
            {
                "canonical_url": f"https://example.com/{symbol.lower()}-story-{idx}",
                "url_hash": f"hash-{symbol.lower()}-{idx}",
                "title": f"{symbol} story {idx}",
                "summary": f"News about {symbol}",
                "published_at": f"{pub_day}T12:00:00Z",
                "fetched_at": f"{pub_day}T13:00:00Z",
                "content_hash": f"content-{idx}",
                "raw_source": "test",
                "sentiment_label": "positive" if idx % 2 == 0 else "negative",
                "sentiment_score": 0.4 - (idx * 0.05),
            }
        )
        repo.link_article_company(article_id, company["id"], "cashtag", 0.95)
        class Event:
            def __init__(self, event_type, confidence, method):
                self.event_type = event_type
                self.confidence = confidence
                self.method = method

        repo.replace_article_events(
            article_id,
            [Event("earnings" if idx % 2 == 0 else "product", 0.9, "rules")],
        )

        class Reaction:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

        repo.replace_article_market_reactions(
            article_id,
            [
                Reaction(
                    ticker=symbol,
                    published_at=f"{pub_day}T12:00:00Z",
                    sentiment_score=0.4 - (idx * 0.05),
                    primary_event="earnings" if idx % 2 == 0 else "product",
                    price_at_publish=100.0 + idx,
                    return_1d=0.01 * idx,
                    return_1w=0.02 * idx,
                    benchmark_return_1d=0.005,
                    abnormal_return_1d=0.015 * idx,
                )
            ],
        )
        articles.append(article_id)

    repo.upsert_prices(
        symbol,
        [
            {
                "date": (today - timedelta(days=offset)).isoformat(),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0 - offset * 0.1,
                "volume": 1000,
            }
            for offset in range(120, -1, -1)
        ],
        source="test",
    )


def test_build_narrative_analysis_returns_expected_sections(app):
    with app.app_context():
        repo = Repository(get_db())
        clear_narrative_cache()
        _seed_narrative_fixture(repo, "AAPL")
        payload = build_narrative_analysis(repo, "AAPL", use_cache=False)

    assert payload["ticker"] == "AAPL"
    assert payload["articleCount"] >= 1
    assert "movingAverages" in payload["sentimentTrend"]
    assert payload["sentimentTrend"]["movingAverages"]["30d"] is not None
    assert isinstance(payload["priceOverlay"], list)
    assert len(payload["priceOverlay"]) >= 1
    assert isinstance(payload["eventTimeline"], list)
    assert isinstance(payload["topEvents"], list)
    assert len(payload["recentArticles"]) >= 1


def test_build_narrative_analysis_handles_naive_published_at(app):
    from datetime import date, timedelta

    pub_day = (date.today() - timedelta(days=3)).isoformat()
    with app.app_context():
        repo = Repository(get_db())
        clear_narrative_cache()
        repo.upsert_companies([{"ticker": "NAIV", "name": "Naive Date Co", "cik": "0000000001"}])
        company = repo.get_company_by_ticker("NAIV")
        article_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/naive-story",
                "url_hash": "hash-naive",
                "title": "NAIV headline",
                "summary": "Story",
                "published_at": f"{pub_day}T12:00:00",
                "fetched_at": f"{pub_day}T13:00:00",
                "content_hash": "content-naive",
                "raw_source": "test",
                "sentiment_score": 0.25,
            }
        )
        repo.link_article_company(article_id, company["id"], "cashtag", 0.95)
        payload = build_narrative_analysis(repo, "NAIV", use_cache=False)

    assert payload["articleCount"] == 1
    assert payload["sentimentTrend"]["movingAverages"]["30d"] == 0.25


def test_research_narrative_route(app, client):
    with app.app_context():
        repo = Repository(get_db())
        clear_narrative_cache()
        _seed_narrative_fixture(repo, "MSFT")

    response = client.get("/api/research/narrative/MSFT")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ticker"] == "MSFT"
    assert payload["sentimentTrend"]["movingAverages"]["90d"] is not None


def test_research_narrative_route_not_found(app, client):
    response = client.get("/api/research/narrative/ZZZZ")
    assert response.status_code == 404
    assert response.get_json()["error"] == "not_found"
