from __future__ import annotations

from app.db import connect_db, init_db
from app.repositories import Repository


def test_job_queue_claim_and_complete(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    init_db(str(db_path))
    conn = connect_db(str(db_path))
    try:
        repo = Repository(conn)
        job_id = repo.enqueue_job("refresh_prices", {"tickers": ["AAPL"]}, priority=10)
        claimed = repo.claim_next_job()
        assert claimed is not None
        assert claimed["id"] == job_id
        assert claimed["job_type"] == "refresh_prices"
        repo.complete_job(job_id, status="done")
        row = conn.execute("SELECT status FROM ingestion_jobs WHERE id = ?", (job_id,)).fetchone()
        assert row["status"] == "done"
        runs = repo.list_job_runs(limit=1)
        assert runs[0]["finished_at"].endswith("Z")
    finally:
        conn.close()


def test_build_research_queue_job_enqueued_on_schedule(tmp_path):
    db_path = tmp_path / "queue_job.sqlite3"
    init_db(str(db_path))
    conn = connect_db(str(db_path))
    try:
        repo = Repository(conn)
        repo.enqueue_job("build_research_queue", {"limit": 10}, priority=35)
        claimed = repo.claim_next_job()
        assert claimed is not None
        assert claimed["job_type"] == "build_research_queue"
    finally:
        conn.close()
