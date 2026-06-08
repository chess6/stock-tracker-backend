"""Gunicorn configuration for the Stock Tracker Flask API."""

from __future__ import annotations

import multiprocessing
import os

# Bind address. Use 127.0.0.1:5000 behind a reverse proxy, or 0.0.0.0:5000 standalone.
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:5000")

# SQLite is write-serialized; gthread spreads concurrent reads across threads in one process.
worker_class = os.getenv("GUNICORN_WORKER_CLASS", "gthread")
workers = int(os.getenv("GUNICORN_WORKERS", "1"))
threads = int(os.getenv("GUNICORN_THREADS", str(max(4, multiprocessing.cpu_count()))))

# Admin refresh/bootstrap can run long SEC batches.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "300"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))

accesslog = os.getenv("GUNICORN_ACCESS_LOG", "-")
errorlog = os.getenv("GUNICORN_ERROR_LOG", "-")
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
capture_output = True

proc_name = "stock-tracker-api"
wsgi_app = "wsgi:app"
