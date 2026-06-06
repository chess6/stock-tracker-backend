from __future__ import annotations

import logging
import time
from typing import Callable

from ..repositories import Repository
from .handlers import JobContext, build_handlers

logger = logging.getLogger(__name__)


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
            self.ctx.repo.fail_job(job["id"], f"Unknown job type: {job['job_type']}")
            return True
        try:
            result = handler(job["payload"])
            self.ctx.repo.complete_job(job["id"], status="done")
            logger.info("Job %s (%s) completed: %s", job["id"], job["job_type"], result)
        except Exception as exc:
            logger.exception("Job %s (%s) failed", job["id"], job["job_type"])
            self.ctx.repo.fail_job(job["id"], str(exc))
        return True

    def run_forever(self) -> None:
        logger.info("Worker started")
        while True:
            processed = self.process_once()
            if not processed:
                time.sleep(self.poll_interval_seconds)
