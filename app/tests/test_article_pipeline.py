from __future__ import annotations

import hashlib

from app.db import get_db
from app.repositories import Repository
from app.services.article_ranking import RankInputs, compute_rank_score
from app.services.event_classification import classify_events_rules
from app.services.market_reaction import compute_market_reactions
from app.services.sentiment_analysis import analyze_sentiment


def _insert_article(repo: Repository, **overrides) -> int:
    title = overrides.pop("title", "Apple beats earnings estimates")
    url = overrides.pop("url", "https://example.com/aapl-earnings")
    url_hash = overrides.pop("url_hash", hashlib.sha256(url.encode()).hexdigest())
    published_at = overrides.pop("published_at", "2025-01-20T14:00:00Z")
    body_text = overrides.pop("body_text", "Apple reported quarterly earnings that beat analyst estimates.")
    return repo.upsert_article(
        {
            "canonical_url": url,
            "url_hash": url_hash,
            "title": title,
            "summary": overrides.pop("summary", "Earnings beat"),
            "body_text": body_text,
            "source_domain": overrides.pop("source_domain", "reuters.com"),
            "published_at": published_at,
            "fetched_at": published_at,
            "raw_source": "test",
        },
        skip_dedup=True,
    )


def test_vader_sentiment_positive_headline():
    result = analyze_sentiment("Apple beats earnings and raises guidance", body="Strong quarter for iPhone sales.")
    assert result.label in {"positive", "neutral"}
    assert result.vader_compound is not None


def test_event_classification_earnings_beat():
    events = classify_events_rules("Company beats earnings estimates and topped analyst expectations")
    types = {event.event_type for event in events}
    assert "earnings_beat" in types


def test_market_reaction_abnormal_return(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "AAPL", "name": "Apple"}, {"ticker": "SPY", "name": "SPY"}])
        article_id = _insert_article(repo)
        company = repo.get_company_by_ticker("AAPL")
        repo.link_article_company(article_id, company["id"], "ticker", 0.95)
        repo.upsert_prices(
            "AAPL",
            [
                {"date": "2025-01-20", "close": 100.0},
                {"date": "2025-01-21", "close": 110.0},
            ],
            source="test",
        )
        repo.upsert_prices(
            "SPY",
            [
                {"date": "2025-01-20", "close": 400.0},
                {"date": "2025-01-21", "close": 402.0},
            ],
            source="test",
        )
        reactions = compute_market_reactions(
            repo,
            article_id=article_id,
            tickers=["AAPL"],
            published_at="2025-01-20T14:00:00Z",
            sentiment_score=0.8,
            primary_event="earnings_beat",
        )
        assert len(reactions) == 1
        assert reactions[0].return_1d == 0.1
        assert round(reactions[0].abnormal_return_1d or 0, 4) == round(0.1 - 0.005, 4)


def test_backfill_market_reactions_updates_stale_rows(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "AAPL", "name": "Apple"}, {"ticker": "SPY", "name": "SPY"}])
        article_id = _insert_article(repo, published_at="2025-01-20T14:00:00Z")
        company = repo.get_company_by_ticker("AAPL")
        repo.link_article_company(article_id, company["id"], "cashtag", 0.95)
        repo.upsert_prices(
            "AAPL",
            [{"date": "2025-01-20", "close": 100.0}, {"date": "2025-01-21", "close": 110.0}],
            source="test",
        )
        repo.upsert_prices(
            "SPY",
            [{"date": "2025-01-20", "close": 400.0}, {"date": "2025-01-21", "close": 402.0}],
            source="test",
        )

        class Reaction:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

        repo.replace_article_market_reactions(
            article_id,
            [
                Reaction(
                    ticker="AAPL",
                    published_at="2025-01-20T14:00:00Z",
                    sentiment_score=0.5,
                    primary_event=None,
                    price_at_publish=100.0,
                    return_1d=0.0,
                    return_1w=0.0,
                    benchmark_return_1d=None,
                    abnormal_return_1d=None,
                )
            ],
        )

        from app.services.market_reaction import backfill_market_reactions

        payload = backfill_market_reactions(repo, ticker="AAPL", limit=10)
        assert payload["articlesUpdated"] == 1
        row = repo.conn.execute(
            "SELECT benchmark_return_1d, abnormal_return_1d FROM article_market_reactions WHERE article_id = ? AND ticker = 'AAPL'",
            (article_id,),
        ).fetchone()
        assert row["benchmark_return_1d"] is not None
        assert row["abnormal_return_1d"] is not None


def test_rank_score_prefers_strong_sentiment_and_quality_source():
    high = compute_rank_score(
        RankInputs(sentiment_score=0.9, vader_compound=0.8, source_domain="reuters.com", novelty_score=0.9)
    )
    low = compute_rank_score(
        RankInputs(sentiment_score=0.1, vader_compound=0.05, source_domain="unknown.blog", novelty_score=0.3)
    )
    assert high > low


def test_recover_stuck_pipeline_articles(app):
    with app.app_context():
        repo = Repository(get_db())
        article_id = _insert_article(repo)
        repo.set_article_pipeline_status(article_id, "processing")
        recovered = repo.recover_stuck_pipeline_articles()
        assert recovered == 1
        row = repo.get_article_by_id(article_id)
        assert row["pipeline_status"] == "pending"
        pending = repo.list_articles_pending_pipeline(limit=10)
        assert article_id in pending


def test_pipeline_process_article_without_network(app, monkeypatch):
    with app.app_context():
        repo = Repository(get_db())
        article_id = _insert_article(repo, body_text="")
        monkeypatch.setattr(
            "app.services.article_pipeline.DomainFetcher.fetch_and_extract",
            lambda self, url: ("Extracted body about earnings beat.", "hash123"),
        )
        from app.services.article_pipeline import ArticlePipeline

        pipeline = ArticlePipeline(repo, enable_embeddings=False, enable_finbert=False)
        result = pipeline.process_article(article_id)
        assert result["status"] == "complete"
        assert "earnings_beat" in result.get("events", [])
        row = repo.get_article_by_id(article_id)
        assert row["pipeline_status"] == "complete"
        assert row["body_text"]
        assert row["vader_compound"] is not None
