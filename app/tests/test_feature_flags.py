from __future__ import annotations

from app.repositories import Repository
from app.services.feature_flags import (
    FLAG_DEFAULTS,
    embeddings_default_enabled,
    is_enabled,
    resolve_flags,
)


def test_feature_flags_default_off():
    assert FLAG_DEFAULTS["experimental_composite_rank"] is True
    assert FLAG_DEFAULTS["embedding_heavy_retag"] is False
    assert FLAG_DEFAULTS["experimental_signal_ranking"] is False
    assert is_enabled("experimental_composite_rank") is True
    assert embeddings_default_enabled() is False


def test_feature_flags_env_override(monkeypatch):
    monkeypatch.setenv("STOCK_TRACKER_FF_EMBEDDING_HEAVY_RETAG", "true")
    assert is_enabled("embedding_heavy_retag") is True
    assert embeddings_default_enabled() is True


def test_flag_sources_marks_env_and_sqlite(app, monkeypatch):
    monkeypatch.setenv("STOCK_TRACKER_FF_EXPERIMENTAL_SIGNALS", "true")
    with app.app_context():
        from app.db import get_db
        from app.services.feature_flags import flag_sources

        repo = Repository(get_db())
        repo.set_config("experimental_research_queue", True)
        sources = flag_sources(repo)
        assert sources["experimental_signals"] == "env"
        assert sources["experimental_research_queue"] == "sqlite"


def test_feature_flags_sqlite_override(app):
    with app.app_context():
        from app.db import get_db

        repo = Repository(get_db())
        repo.set_config("experimental_signal_ranking", True)
        assert is_enabled("experimental_signal_ranking", repo) is True
        flags = resolve_flags(repo)
        assert flags["experimental_signal_ranking"] is True
        assert flags["experimental_composite_rank"] is True


def test_pipeline_skips_rank_when_composite_flag_off(app, monkeypatch):
    with app.app_context():
        from app.db import get_db
        from app.services.article_pipeline import ArticlePipeline
        from app.tests.test_article_pipeline import _insert_article

        repo = Repository(get_db())
        repo.set_config("experimental_composite_rank", False)
        article_id = _insert_article(repo, body_text="")
        monkeypatch.setattr(
            "app.services.article_pipeline.DomainFetcher.fetch_and_extract",
            lambda self, url: ("Extracted body about earnings beat.", "hash123"),
        )
        pipeline = ArticlePipeline(repo, enable_embeddings=False, enable_finbert=False)
        pipeline.process_article(article_id)
        row = repo.get_article_by_id(article_id)
        assert row["pipeline_status"] == "complete"
        assert row["rank_score"] is None


def test_pipeline_writes_rank_when_composite_flag_on(app, monkeypatch):
    with app.app_context():
        from app.db import get_db
        from app.services.article_pipeline import ArticlePipeline
        from app.tests.test_article_pipeline import _insert_article

        repo = Repository(get_db())
        repo.set_config("experimental_composite_rank", True)
        article_id = _insert_article(repo, body_text="")
        monkeypatch.setattr(
            "app.services.article_pipeline.DomainFetcher.fetch_and_extract",
            lambda self, url: ("Extracted body about earnings beat.", "hash123"),
        )
        pipeline = ArticlePipeline(repo, enable_embeddings=False, enable_finbert=False)
        pipeline.process_article(article_id)
        row = repo.get_article_by_id(article_id)
        assert row["rank_score"] is not None


def test_admin_config_routes(app, client):
    get_resp = client.get("/api/admin/config")
    assert get_resp.status_code == 200
    payload = get_resp.get_json()
    assert "flags" in payload
    assert "sources" in payload
    assert payload["flags"]["experimental_composite_rank"] is True

    post_resp = client.post(
        "/api/admin/config",
        json={"experimental_composite_rank": True, "unknown_flag": True},
    )
    assert post_resp.status_code == 200
    post_payload = post_resp.get_json()
    assert post_payload["updated"] == ["experimental_composite_rank"]
    assert post_payload["flags"]["experimental_composite_rank"] is True
    assert "sources" in post_payload
