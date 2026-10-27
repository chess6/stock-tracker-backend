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


def test_scheduler_registers_jobs_and_enqueues(tmp_path):
    from scheduler import build_scheduler, enqueue_feed_poll, enqueue_scheduled_jobs

    db_path = str(tmp_path / "sched.sqlite3")
    init_db(db_path)

    scheduler = build_scheduler(db_path, ["AAPL"])
    assert {job.id for job in scheduler.get_jobs()} == {"nightly_etl", "rss_poll"}

    enqueue_scheduled_jobs(db_path, ["AAPL"])
    enqueue_feed_poll(db_path)
    conn = connect_db(db_path)
    try:
        job_types = [row["job_type"] for row in conn.execute("SELECT job_type FROM ingestion_jobs")]
    finally:
        conn.close()
    assert "refresh_fundamentals" in job_types
    assert job_types.count("ingest_default_feeds") == 2


def test_worker_does_not_start_scheduler():
    import worker

    assert not hasattr(worker, "BackgroundScheduler")
