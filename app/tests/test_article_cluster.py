from __future__ import annotations

from app.db import get_db
from app.repositories import Repository
from app.services.article_cluster import assign_article_to_event_cluster


def test_event_cluster_groups_similar_articles(app):
    with app.app_context():
        repo = Repository(get_db())
        vector = [1.0, 0.0, 0.0]
        near_vector = [0.99, 0.01, 0.0]

        first_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/fed-cut-1",
                "url_hash": "hash-fed-1",
                "title": "Fed cuts rates by 25bps",
                "summary": "Federal Reserve lowers benchmark rate",
                "published_at": "2026-06-16T12:00:00Z",
                "fetched_at": "2026-06-16T12:05:00Z",
                "source_domain": "reuters.com",
                "sentiment_score": 0.2,
                "raw_source": "test",
            },
            skip_dedup=True,
        )
        second_id = repo.upsert_article(
            {
                "canonical_url": "https://publisher.example/fed-cut-2",
                "url_hash": "hash-fed-2",
                "title": "Federal Reserve cuts interest rates",
                "summary": "Central bank moves to ease policy",
                "published_at": "2026-06-16T13:00:00Z",
                "fetched_at": "2026-06-16T13:05:00Z",
                "source_domain": "cnbc.com",
                "sentiment_score": 0.1,
                "raw_source": "test",
            },
            skip_dedup=True,
        )

        cluster_one = assign_article_to_event_cluster(
            repo,
            article_id=first_id,
            event_type="macroeconomic",
            headline="Fed cuts rates by 25bps",
            published_at="2026-06-16T12:00:00Z",
            source_domain="reuters.com",
            sentiment_score=0.2,
            vector=vector,
        )
        cluster_two = assign_article_to_event_cluster(
            repo,
            article_id=second_id,
            event_type="macroeconomic",
            headline="Federal Reserve cuts interest rates",
            published_at="2026-06-16T13:00:00Z",
            source_domain="cnbc.com",
            sentiment_score=0.1,
            vector=near_vector,
        )

        assert cluster_one is not None
        assert cluster_two == cluster_one

        cluster = repo.conn.execute(
            "SELECT article_count, source_count FROM article_event_clusters WHERE id = ?",
            (cluster_one,),
        ).fetchone()
        assert cluster["article_count"] == 2
        assert cluster["source_count"] == 2
