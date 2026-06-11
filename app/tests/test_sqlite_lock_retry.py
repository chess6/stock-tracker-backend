"""SQLite lock contention helpers."""

from __future__ import annotations

import sqlite3

import pytest

from app.db import connect_db, init_db, is_sqlite_lock_error, retry_on_sqlite_lock
from app.repositories import Repository


def test_is_sqlite_lock_error_matches_operational_error():
    assert is_sqlite_lock_error(sqlite3.OperationalError("database is locked"))
    assert is_sqlite_lock_error(sqlite3.OperationalError("database is busy"))
    assert not is_sqlite_lock_error(sqlite3.OperationalError("no such table: x"))
    assert not is_sqlite_lock_error(ValueError("database is locked"))


def test_retry_on_sqlite_lock_recovers(tmp_path):
    calls = {"count": 0}

    def flaky() -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert retry_on_sqlite_lock(flaky, max_attempts=5, base_delay_seconds=0.01) == "ok"
    assert calls["count"] == 3


def test_worker_runner_survives_sqlite_lock(monkeypatch):
    from app.workers.runner import WorkerRunner

    calls: list[int] = []

    class _Ctx:
        repo = object()

    runner = WorkerRunner(_Ctx(), poll_interval_seconds=0)

    def fake_process_once() -> bool:
        calls.append(len(calls))
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "process_once", fake_process_once)
    monkeypatch.setattr("app.workers.runner.time.sleep", lambda _seconds: None)

    with pytest.raises(KeyboardInterrupt):
        runner.run_forever()
    assert len(calls) == 2


def test_claim_next_job_still_claims_after_rollback(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    init_db(str(db_path))
    conn = connect_db(str(db_path))
    try:
        repo = Repository(conn)
        job_id = repo.enqueue_job("refresh_prices", {"tickers": ["AAPL"]}, priority=10)
        conn.execute("SELECT 1")
        claimed = repo.claim_next_job()
        assert claimed is not None
        assert claimed["id"] == job_id
        repo.complete_job(job_id, status="done")
    finally:
        conn.close()
