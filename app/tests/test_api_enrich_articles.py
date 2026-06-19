from __future__ import annotations

from app.db import get_db
from app.repositories import Repository


def test_enrich_requeue_only_does_not_process_batch(app, client):
    with app.app_context():
        repo = Repository(get_db())
        article_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/requeue-only",
                "url_hash": "hash-requeue-only",
                "title": "Completed for requeue test",
                "summary": "body",
                "published_at": "2026-06-18T12:00:00Z",
                "fetched_at": "2026-06-18T12:05:00Z",
                "raw_source": "test",
            },
            skip_dedup=True,
        )
        assert article_id
        repo.set_article_pipeline_status(article_id, "complete")

    response = client.post(
        "/api/admin/enrich-articles",
        json={
            "requeue_completed": True,
            "requeue_only": True,
            "enable_embeddings": False,
            "enable_finbert": False,
            "limit": 25,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["mode"] == "requeue"
    assert payload["processed"] == 0
    assert payload["requeued"] == 1

    with app.app_context():
        repo = Repository(get_db())
        row = repo.get_article_by_id(article_id)
        assert row["pipeline_status"] == "pending"


def test_enrich_force_does_not_requeue_each_batch(app, client, monkeypatch):
    with app.app_context():
        repo = Repository(get_db())
        article_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/no-repeat-requeue",
                "url_hash": "hash-no-repeat-requeue",
                "title": "Pending article",
                "summary": "body",
                "published_at": "2026-06-18T13:00:00Z",
                "fetched_at": "2026-06-18T13:05:00Z",
                "raw_source": "test",
            },
            skip_dedup=True,
        )
        assert article_id

    requeue_calls = {"count": 0}
    original = Repository.requeue_completed_articles

    def counting_requeue(self, *, limit: int = 500) -> int:
        requeue_calls["count"] += 1
        return original(self, limit=limit)

    monkeypatch.setattr(Repository, "requeue_completed_articles", counting_requeue)
    monkeypatch.setattr(
        "app.services.article_pipeline.ArticlePipeline.process_batch",
        lambda self, *, limit=25: {
            "processed": 1,
            "results": [{"article_id": article_id, "status": "complete"}],
            "pipeline": Repository(get_db()).get_pipeline_status_counts(),
        },
    )

    response = client.post(
        "/api/admin/enrich-articles",
        json={"limit": 25, "enable_embeddings": False, "enable_finbert": False},
    )
    assert response.status_code == 200
    assert requeue_calls["count"] == 0
