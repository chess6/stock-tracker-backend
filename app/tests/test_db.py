from __future__ import annotations

import sqlite3

from app.db import connect_db


def test_sqlite_schema_and_wal_mode(app):
    conn = connect_db(app.config["DATABASE_PATH"])
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"companies", "articles", "fundamentals", "ingestion_jobs"} <= tables
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.lower() == "wal"
    finally:
        conn.close()
