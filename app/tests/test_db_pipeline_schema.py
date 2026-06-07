from __future__ import annotations

from app.db import connect_db, init_db


def test_pipeline_tables_migrate(tmp_path):
    db_path = tmp_path / "migrate.sqlite3"
    init_db(str(db_path))
    conn = connect_db(str(db_path))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "article_event_classifications" in tables
        assert "article_market_reactions" in tables
        assert "article_embedding_vectors" in tables
        assert "domain_fetch_state" in tables
        article_cols = {row[1] for row in conn.execute("PRAGMA table_info(articles)").fetchall()}
        assert "pipeline_status" in article_cols
        assert "vader_compound" in article_cols
        assert "rank_score" in article_cols
    finally:
        conn.close()
