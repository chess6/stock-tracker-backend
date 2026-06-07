"""Flask middleware for structured API request/response logging."""

from __future__ import annotations

import time

from flask import Flask, g, request

from .logging_config import get_api_logger


def register_request_logging(app: Flask) -> None:
    """Attach before/after hooks that log every request to the API log."""
    logger = get_api_logger()

    @app.before_request
    def _start_timer() -> None:
        g._request_start = time.monotonic()

    @app.after_request
    def _log_request(response):
        duration_ms = (time.monotonic() - getattr(g, "_request_start", time.monotonic())) * 1000
        path = request.path
        qs = request.query_string.decode("utf-8", errors="replace")
        full_path = f"{path}?{qs}" if qs else path

        status = response.status_code
        method = request.method
        content_length = response.content_length or 0

        level = "WARNING" if status >= 400 else "INFO"
        logger.log(
            getattr(__import__("logging"), level),
            '%s %s %d %.0fms %dB client=%s',
            method,
            full_path,
            status,
            duration_ms,
            content_length,
            request.remote_addr or "-",
        )
        return response
