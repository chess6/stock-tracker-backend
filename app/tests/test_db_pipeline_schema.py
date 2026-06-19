from __future__ import annotations

import math
from datetime import date, timedelta

from app.db import connect_db, get_db, init_db
from app.repositories import Repository
from app.services.metric_primitives import gross_margin


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
        assert "enrichment_version" in article_cols
        fundamentals_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(fundamentals)").fetchall()
        }
        assert "source_updated_at" in fundamentals_cols
        scores_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(company_scores)").fetchall()
        }
        assert "scoring_version" in scores_cols
        assert "thesis_version" in scores_cols
        assert "pillar_version" in scores_cols
        assert "company_thesis_snapshots" in tables
        prices_cols = {row[1] for row in conn.execute("PRAGMA table_info(prices)").fetchall()}
        assert "fetched_at" in prices_cols
        assert "company_tags" in tables
        assert "research_queue" in tables
        queue_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(research_queue)").fetchall()
        }
        assert "event_type" in queue_cols
        assert "dismissed" in queue_cols
    finally:
        conn.close()


def test_zero_revenue_gross_margin_returns_none_not_inf():
    row = {"revenue": 0.0, "cor": 50_000.0, "gp": None}
    margin = gross_margin(row)
    assert margin is None
    if margin is not None:
        assert math.isfinite(margin)


def test_no_orphan_join_rows_after_init(tmp_path):
    db_path = tmp_path / "joins.sqlite3"
    init_db(str(db_path))
    conn = connect_db(str(db_path))
    try:
        orphan_article_company = conn.execute(
            """
            SELECT COUNT(*) FROM article_company ac
            LEFT JOIN articles a ON a.id = ac.article_id
            WHERE a.id IS NULL
            """
        ).fetchone()[0]
        orphan_insider = conn.execute(
            """
            SELECT COUNT(*) FROM insider_transactions it
            LEFT JOIN companies c ON c.id = it.company_id
            WHERE c.id IS NULL
            """
        ).fetchone()[0]
        assert orphan_article_company == 0
        assert orphan_insider == 0
    finally:
        conn.close()


def test_research_queue_upsert_is_idempotent(app):
    with app.app_context():
        repo = Repository(get_db())
        records = [
            {
                "ticker": "IDEMQ",
                "event_type": "rank_up",
                "event_date": date.today().isoformat(),
                "priority": 20,
                "details": {"delta": 6},
            }
        ]
        first = repo.upsert_research_queue_items(records)
        second = repo.upsert_research_queue_items(records)
        count = repo.conn.execute(
            "SELECT COUNT(*) FROM research_queue WHERE ticker = 'IDEMQ'"
        ).fetchone()[0]
        assert first >= 1
        assert second >= 1
        assert count == 1
