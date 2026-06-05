from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Flask, current_app, g


SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
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

CREATE TABLE IF NOT EXISTS feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    feed_url TEXT NOT NULL UNIQUE,
    domain TEXT,
    category TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    etag TEXT,
    last_modified TEXT,
    last_polled_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_url TEXT,
    url_hash TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    summary TEXT,
    body_text TEXT,
    source_domain TEXT,
    published_at TEXT,
    fetched_at TEXT,
    content_hash TEXT,
    language TEXT,
    duplicate_of_article_id INTEGER,
    sentiment_label TEXT,
    sentiment_score REAL,
    topic_cluster_id TEXT,
    raw_source TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (duplicate_of_article_id) REFERENCES articles(id)
);

CREATE TABLE IF NOT EXISTS article_company (
    article_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    match_type TEXT,
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (article_id, company_id),
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fundamentals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    metric TEXT NOT NULL,
    value REAL,
    unit TEXT,
    period_end TEXT NOT NULL,
    period_type TEXT NOT NULL,
    dimension TEXT NOT NULL,
    fiscal_year INTEGER,
    fiscal_quarter TEXT,
    filing_date TEXT,
    form TEXT,
    accession TEXT,
    source TEXT NOT NULL,
    taxonomy TEXT,
    xbrl_concept TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    UNIQUE (company_id, metric, period_end, dimension, filing_date, xbrl_concept)
);

CREATE TABLE IF NOT EXISTS embeddings_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    content_hash TEXT,
    storage_key TEXT,
    vector_dimensions INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
    UNIQUE (article_id, model)
);

CREATE TABLE IF NOT EXISTS http_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    status_code INTEGER,
    etag TEXT,
    last_modified TEXT,
    response_body TEXT,
    fetched_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 100,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    locked_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    FOREIGN KEY (job_id) REFERENCES ingestion_jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(name);
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_source_domain_published_at ON articles(source_domain, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_duplicate_of ON articles(duplicate_of_article_id);
CREATE INDEX IF NOT EXISTS idx_article_company_company_article ON article_company(company_id, article_id);
CREATE INDEX IF NOT EXISTS idx_article_company_article_company ON article_company(article_id, company_id);
CREATE INDEX IF NOT EXISTS idx_fundamentals_company_metric_period ON fundamentals(company_id, metric, period_end, period_type);
CREATE INDEX IF NOT EXISTS idx_fundamentals_company_filing_date ON fundamentals(company_id, filing_date DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status_available ON ingestion_jobs(status, available_at, priority);
"""


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def configure_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    return conn


def connect_db(path: str) -> sqlite3.Connection:
    return configure_connection(sqlite3.connect(path))


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect_db(current_app.config["DATABASE_PATH"])
    return g.db


def init_db(path: str) -> None:
    conn = connect_db(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def close_db(_: Exception | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_app(app: Flask) -> None:
    init_db(app.config["DATABASE_PATH"])
    app.teardown_appcontext(close_db)
