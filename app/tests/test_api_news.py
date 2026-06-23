from __future__ import annotations

from datetime import date

from app.db import get_db
from app.repositories import Repository


def test_news_feed_sorted_by_importance_score(app, client):
    with app.app_context():
        repo = Repository(get_db())
        older_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/old",
                "url_hash": "hash-old",
                "title": "Older high importance",
                "summary": "Old story",
                "published_at": "2026-06-10T12:00:00Z",
                "fetched_at": "2026-06-10T12:05:00Z",
                "rank_score": 0.95,
                "news_importance_score": 0.95,
                "raw_source": "test",
            },
            skip_dedup=True,
        )
        newer_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/new",
                "url_hash": "hash-new",
                "title": "Newer low importance",
                "summary": "New story",
                "published_at": "2026-06-16T12:00:00Z",
                "fetched_at": "2026-06-16T12:05:00Z",
                "rank_score": 0.20,
                "news_importance_score": 0.20,
                "raw_source": "test",
            },
            skip_dedup=True,
        )
        assert older_id and newer_id
        repo.conn.execute(
            "UPDATE articles SET rank_score = ?, news_importance_score = ? WHERE id = ?",
            (0.95, 0.95, older_id),
        )
        repo.conn.execute(
            "UPDATE articles SET rank_score = ?, news_importance_score = ? WHERE id = ?",
            (0.20, 0.20, newer_id),
        )
        repo.conn.commit()

    response = client.get("/api/news?limit=10")
    assert response.status_code == 200
    articles = response.get_json()["articles"]
    assert articles[0]["title"] == "Older high importance"


def test_news_feed_sorted_by_latest(app, client):
    with app.app_context():
        repo = Repository(get_db())
        older_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/old-latest",
                "url_hash": "hash-old-latest",
                "title": "Older high importance latest sort",
                "summary": "Old story",
                "published_at": "2026-06-10T12:00:00Z",
                "fetched_at": "2026-06-10T12:05:00Z",
                "rank_score": 0.95,
                "news_importance_score": 0.95,
                "raw_source": "test",
            },
            skip_dedup=True,
        )
        newer_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/new-latest",
                "url_hash": "hash-new-latest",
                "title": "Newer low importance latest sort",
                "summary": "New story",
                "published_at": "2026-06-16T12:00:00Z",
                "fetched_at": "2026-06-16T12:05:00Z",
                "rank_score": 0.20,
                "news_importance_score": 0.20,
                "raw_source": "test",
            },
            skip_dedup=True,
        )
        assert older_id and newer_id
        repo.conn.execute(
            "UPDATE articles SET rank_score = ?, news_importance_score = ? WHERE id = ?",
            (0.95, 0.95, older_id),
        )
        repo.conn.execute(
            "UPDATE articles SET rank_score = ?, news_importance_score = ? WHERE id = ?",
            (0.20, 0.20, newer_id),
        )
        repo.conn.commit()

    response = client.get("/api/news?limit=10&sort=latest")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["sort"] == "latest"
    titles = [item["title"] for item in payload["articles"]]
    assert titles.index("Newer low importance latest sort") < titles.index("Older high importance latest sort")


def test_news_source_domains_route(app, client):
    with app.app_context():
        repo = Repository(get_db())
        for idx, domain in enumerate(["reuters.com", "cnbc.com", "reuters.com"]):
            repo.upsert_article(
                {
                    "canonical_url": f"https://{domain}/story-{idx}",
                    "url_hash": f"hash-domain-{idx}",
                    "title": f"Story from {domain}",
                    "summary": "Domain filter option",
                    "published_at": "2026-06-16T12:00:00Z",
                    "fetched_at": "2026-06-16T12:05:00Z",
                    "source_domain": domain,
                    "raw_source": "test",
                },
                skip_dedup=True,
            )

    response = client.get("/api/news/source-domains?limit=10")
    assert response.status_code == 200
    domains = response.get_json()["domains"]
    assert "reuters.com" in domains
    assert "cnbc.com" in domains
    assert domains.index("reuters.com") < domains.index("cnbc.com")


def test_news_clusters_route(app, client):
    with app.app_context():
        repo = Repository(get_db())
        cluster_id = repo.create_event_cluster(
            {
                "event_type": "macroeconomic",
                "headline": "Fed cuts rates",
                "first_seen_at": f"{date.today().isoformat()}T12:00:00Z",
                "last_seen_at": f"{date.today().isoformat()}T13:00:00Z",
                "source_domains": ["reuters.com", "cnbc.com"],
                "consensus_sentiment": 0.15,
                "centroid": [1.0, 0.0],
            }
        )
        assert cluster_id > 0

    response = client.get("/api/news/clusters?limit=5")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["returned"] >= 1
    assert payload["total"] >= 1
    assert payload["offset"] == 0
    match = next(item for item in payload["clusters"] if item.get("headline") == "Fed cuts rates")
    assert match["sourceCount"] == 2
