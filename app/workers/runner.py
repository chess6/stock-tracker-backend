from __future__ import annotations

import logging
import sqlite3
import time
from typing import Callable

from ..db import is_sqlite_lock_error
from ..repositories import Repository
from .handlers import JobContext, build_handlers

logger = logging.getLogger("stock_tracker.pipeline.worker")

_SUMMARY_KEYS = (
    "processed",
    "recomputed",
    "skipped_unchanged",
    "skipped",
    "upserted",
    "written",
    "totalTickers",
    "chunks",
)


def _format_job_summary(job_type: str, result: dict | None, elapsed: float) -> str:
    parts = [f"job_complete type={job_type} elapsed={elapsed:.2f}s"]
    payload = result or {}
    for key in _SUMMARY_KEYS:
        if key in payload:
            parts.append(f"{key}={payload[key]}")
    tickers = payload.get("tickers")
    if isinstance(tickers, list):
        parts.append(f"tickers={len(tickers)}")
    elif isinstance(tickers, int):
        parts.append(f"tickers={tickers}")
    return " ".join(parts)


class WorkerRunner:
    def __init__(self, ctx: JobContext, poll_interval_seconds: int = 5) -> None:
        self.ctx = ctx
        self.poll_interval_seconds = poll_interval_seconds
        self.handlers = build_handlers(ctx)

    def process_once(self) -> bool:
        job = self.ctx.repo.claim_next_job()
        if not job:
            return False
        handler = self.handlers.get(job["job_type"])
        if handler is None:
            logger.warning("Unknown job type=%s id=%s", job["job_type"], job["id"])
            self.ctx.repo.fail_job(job["id"], f"Unknown job type: {job['job_type']}")
            return True
        logger.info("Job start id=%s type=%s payload_keys=%s",
                    job["id"], job["job_type"], list((job.get("payload") or {}).keys()))
        t0 = time.monotonic()
        try:
            result = handler(job["payload"])
            elapsed = time.monotonic() - t0
            self.ctx.repo.complete_job(job["id"], status="done")
            logger.info(_format_job_summary(job["job_type"], result, elapsed))
            logger.debug(
                "Job done id=%s type=%s result_keys=%s",
                job["id"],
                job["job_type"],
                list((result or {}).keys()),
            )
        except Exception as exc:
            elapsed = time.monotonic() - t0
            logger.exception("Job failed id=%s type=%s elapsed=%.1fs", job["id"], job["job_type"], elapsed)
            self.ctx.repo.fail_job(job["id"], str(exc))
            self._emit_fetch_failed(job, exc)
        return True

    def _emit_fetch_failed(self, job: dict, exc: Exception) -> None:
        try:
            from orchestration.services.bridge import emit_fetch_failed

            payload = job.get("payload") or {}
            emit_fetch_failed(
                failure_type=job.get("job_type", "unknown"),
                source="ingestion_worker",
                error=str(exc),
                job_type=job.get("job_type"),
                tickers=payload.get("tickers", []),
                exc=exc,
            )
        except Exception:
            logger.debug("Orchestration bridge unavailable; skipping fetch_failed event")

    def run_forever(self) -> None:
        logger.info("Worker started")
        while True:
            try:
                processed = self.process_once()
            except sqlite3.OperationalError as exc:
                if is_sqlite_lock_error(exc):
                    logger.warning(
                        "SQLite lock while polling job queue; backing off %ss: %s",
                        self.poll_interval_seconds,
                        exc,
                    )
                    time.sleep(self.poll_interval_seconds)
                    continue
                raise
            if not processed:
                time.sleep(self.poll_interval_seconds)
