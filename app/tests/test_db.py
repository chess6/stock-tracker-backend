from __future__ import annotations

import sqlite3

from app.db import connect_db, init_db
from app.repositories import Repository


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


def test_migrate_legacy_unique_cik_schema(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    conn = connect_db(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                cik TEXT UNIQUE,
                exchange TEXT,
                sector TEXT,
                industry TEXT,
                sec_filings_url TEXT,
                company_site TEXT,
                source TEXT DEFAULT 'manual',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    init_db(str(db_path))
    conn = connect_db(str(db_path))
    try:
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='companies'"
        ).fetchone()[0].upper()
        assert "CIK TEXT UNIQUE" not in table_sql
    finally:
        conn.close()


def test_companies_allow_duplicate_cik(tmp_path):
    db_path = tmp_path / "companies.sqlite3"
    init_db(str(db_path))
    conn = connect_db(str(db_path))
    try:
        repo = Repository(conn)
        repo.upsert_companies(
            [
                {"ticker": "GOOGL", "name": "Alphabet Inc Class A", "cik": "0001652044"},
                {"ticker": "GOOG", "name": "Alphabet Inc Class C", "cik": "0001652044"},
            ]
        )
        rows = conn.execute(
            "SELECT ticker, cik FROM companies WHERE ticker IN ('GOOGL', 'GOOG') ORDER BY ticker"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["cik"] == "0001652044"
        assert rows[1]["cik"] == "0001652044"
    finally:
        conn.close()
