"""Centralized logging with 7-day rotating file retention.

Produces two log files under ``logs/``:
- ``api.log``      — HTTP request/response lines from Flask
- ``pipeline.log`` — ingest, ETL, and worker activity

Both use ``TimedRotatingFileHandler`` set to rotate at midnight and keep 7
backups (``backupCount=7``).  Console output mirrors ``pipeline.log`` so
``start.sh`` / ``worker.sh`` stdout remains useful.
"""

from __future__ import annotations

import logging
import os
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_LOGS_DIR: Path | None = None

LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

API_LOGGER_NAME = "stock_tracker.api"
PIPELINE_LOGGER_NAME = "stock_tracker.pipeline"


def _logs_dir() -> Path:
    global _LOGS_DIR
    if _LOGS_DIR is None:
        base = Path(os.getenv("STOCK_TRACKER_LOG_DIR", str(Path(__file__).resolve().parents[1] / "logs")))
        base.mkdir(parents=True, exist_ok=True)
        _LOGS_DIR = base
    return _LOGS_DIR


def _make_file_handler(filename: str, level: int = logging.DEBUG) -> TimedRotatingFileHandler:
    handler = TimedRotatingFileHandler(
        str(_logs_dir() / filename),
        when="midnight",
        backupCount=7,
        utc=True,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    return handler


def _make_console_handler(level: int = logging.INFO) -> logging.StreamHandler:
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    return handler


def setup_logging(*, console_level: int = logging.INFO) -> None:
    """Configure root + named loggers.  Safe to call multiple times."""
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.DEBUG)

    console = _make_console_handler(console_level)
    root.addHandler(console)

    pipeline_file = _make_file_handler("pipeline.log")
    root.addHandler(pipeline_file)

    api_logger = logging.getLogger(API_LOGGER_NAME)
    api_logger.propagate = False
    api_logger.setLevel(logging.DEBUG)
    api_logger.addHandler(_make_file_handler("api.log"))
    api_logger.addHandler(console)


def get_api_logger() -> logging.Logger:
    return logging.getLogger(API_LOGGER_NAME)


def get_pipeline_logger(name: str | None = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"{PIPELINE_LOGGER_NAME}.{name}")
    return logging.getLogger(PIPELINE_LOGGER_NAME)
