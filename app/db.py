from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Flask, current_app, g


SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    cik TEXT,
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

CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (ticker, date, source)
);

CREATE TABLE IF NOT EXISTS insider_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    filing_date TEXT,
    transaction_date TEXT,
    owner_name TEXT,
    transaction_code TEXT,
    shares REAL,
    price_per_share REAL,
    transaction_value REAL,
    security_title TEXT,
    form TEXT,
    accession TEXT,
    source TEXT NOT NULL DEFAULT 'sec_edgar',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    UNIQUE (company_id, accession, owner_name, transaction_date, transaction_code, shares)
);

CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(name);
CREATE INDEX IF NOT EXISTS idx_companies_cik ON companies(cik);
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_source_domain_published_at ON articles(source_domain, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_duplicate_of ON articles(duplicate_of_article_id);
CREATE INDEX IF NOT EXISTS idx_article_company_company_article ON article_company(company_id, article_id);
CREATE INDEX IF NOT EXISTS idx_article_company_article_company ON article_company(article_id, company_id);
CREATE INDEX IF NOT EXISTS idx_fundamentals_company_metric_period ON fundamentals(company_id, metric, period_end, period_type);
CREATE INDEX IF NOT EXISTS idx_fundamentals_company_filing_date ON fundamentals(company_id, filing_date DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status_available ON ingestion_jobs(status, available_at, priority);
CREATE INDEX IF NOT EXISTS idx_prices_ticker_date ON prices(ticker, date DESC);
CREATE INDEX IF NOT EXISTS idx_insider_company_filing ON insider_transactions(company_id, filing_date DESC);

CREATE TABLE IF NOT EXISTS watchlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS watchlist_tickers (
    watchlist_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (watchlist_id, ticker),
    FOREIGN KEY (watchlist_id) REFERENCES watchlists(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_tags (
    ticker TEXT NOT NULL,
    tag TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, tag)
);

CREATE INDEX IF NOT EXISTS idx_company_tags_tag ON company_tags(tag);

CREATE TABLE IF NOT EXISTS user_preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    theme TEXT NOT NULL DEFAULT 'dark',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS article_event_classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    method TEXT NOT NULL DEFAULT 'rules',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
    UNIQUE (article_id, event_type)
);

CREATE TABLE IF NOT EXISTS article_market_reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    published_at TEXT,
    sentiment_score REAL,
    primary_event TEXT,
    price_at_publish REAL,
    return_1d REAL,
    return_1w REAL,
    benchmark_return_1d REAL,
    abnormal_return_1d REAL,
    computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
    UNIQUE (article_id, ticker)
);

CREATE TABLE IF NOT EXISTS article_embedding_vectors (
    article_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector_json TEXT NOT NULL,
    content_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (article_id, model),
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS domain_fetch_state (
    domain TEXT PRIMARY KEY,
    last_fetched_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    backoff_until TEXT
);

CREATE INDEX IF NOT EXISTS idx_article_events_article ON article_event_classifications(article_id);
CREATE INDEX IF NOT EXISTS idx_article_market_reactions_ticker ON article_market_reactions(ticker, published_at DESC);

CREATE TABLE IF NOT EXISTS company_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL DEFAULT 'name',
    normalized_alias TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    UNIQUE (company_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_company_aliases_normalized ON company_aliases(normalized_alias);
CREATE INDEX IF NOT EXISTS idx_company_aliases_company ON company_aliases(company_id);

CREATE TABLE IF NOT EXISTS company_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    period_end TEXT NOT NULL,
    dimension TEXT NOT NULL DEFAULT 'ARY',
    piotroski_f INTEGER,
    altman_z REAL,
    beneish_m REAL,
    survivability REAL,
    piotroski_components TEXT,
    altman_components TEXT,
    computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    UNIQUE (company_id, period_end, dimension)
);

CREATE INDEX IF NOT EXISTS idx_company_scores_company_period ON company_scores(company_id, period_end DESC);

CREATE TABLE IF NOT EXISTS insider_cluster_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    buy_count INTEGER NOT NULL DEFAULT 0,
    sell_count INTEGER NOT NULL DEFAULT 0,
    unique_buyers INTEGER NOT NULL DEFAULT 0,
    total_buy_value REAL,
    total_sell_value REAL,
    avg_buy_price REAL,
    intensity_score REAL,
    computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    UNIQUE (company_id, window_start, window_end)
);

CREATE INDEX IF NOT EXISTS idx_insider_cluster_company ON insider_cluster_analysis(company_id, window_start DESC);
CREATE INDEX IF NOT EXISTS idx_insider_cluster_intensity ON insider_cluster_analysis(intensity_score DESC);
"""


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def configure_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    return conn


def connect_db(path: str) -> sqlite3.Connection:
    return configure_connection(sqlite3.connect(path))


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect_db(current_app.config["DATABASE_PATH"])
    return g.db


def _companies_table_sql(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='companies'"
    ).fetchone()
    return row[0] if row else None


def migrate_schema(conn: sqlite3.Connection) -> None:
    companies_sql = _companies_table_sql(conn)
    if companies_sql and "CIK TEXT UNIQUE" in companies_sql.upper().replace("\n", " "):
        conn.executescript(
            """
            CREATE TABLE companies__new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                cik TEXT,
                exchange TEXT,
                sector TEXT,
                industry TEXT,
                sec_filings_url TEXT,
                company_site TEXT,
                source TEXT DEFAULT 'manual',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO companies__new (
                id, ticker, name, cik, exchange, sector, industry,
                sec_filings_url, company_site, source, created_at, updated_at
            )
            SELECT
                id, ticker, name, cik, exchange, sector, industry,
                sec_filings_url, company_site, source, created_at, updated_at
            FROM companies;
            DROP TABLE companies;
            ALTER TABLE companies__new RENAME TO companies;
            CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(name);
            CREATE INDEX IF NOT EXISTS idx_companies_cik ON companies(cik);
            """
        )
        conn.commit()

    article_cols = {row[1] for row in conn.execute("PRAGMA table_info(articles)").fetchall()}
    article_migrations = {
        "simhash_fingerprint": "TEXT",
        "pipeline_status": "TEXT DEFAULT 'pending'",
        "vader_compound": "REAL",
        "vader_pos": "REAL",
        "vader_neu": "REAL",
        "vader_neg": "REAL",
        "finbert_label": "TEXT",
        "finbert_pos": "REAL",
        "finbert_neu": "REAL",
        "finbert_neg": "REAL",
        "rank_score": "REAL",
        "engagement_score": "REAL",
        "novelty_score": "REAL",
        "extraction_status": "TEXT",
    }
    for column, col_type in article_migrations.items():
        if column not in article_cols:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {column} {col_type}")
    if "simhash_fingerprint" not in article_cols:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_articles_simhash ON articles(simhash_fingerprint)"
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_pipeline_status ON articles(pipeline_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_rank_score ON articles(rank_score DESC)")

    article_company_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(article_company)").fetchall()
    }
    article_company_migrations = {
        "match_strategy": "TEXT",
        "extraction_stage": "TEXT",
        "evidence_text": "TEXT",
        "embedding_similarity": "REAL",
        "updated_at": "TEXT",
    }
    for column, col_type in article_company_migrations.items():
        if column not in article_company_cols:
            conn.execute(f"ALTER TABLE article_company ADD COLUMN {column} {col_type}")
    conn.execute(
        """
        UPDATE article_company
        SET match_strategy = COALESCE(match_strategy, match_type, 'ticker_symbol'),
            extraction_stage = COALESCE(extraction_stage, 'ingest'),
            updated_at = COALESCE(updated_at, created_at)
        WHERE match_strategy IS NULL OR extraction_stage IS NULL OR updated_at IS NULL
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_article_company_confidence ON article_company(article_id, confidence DESC)"
    )

    feed_cols = {row[1] for row in conn.execute("PRAGMA table_info(feeds)").fetchall()}
    feed_migrations = {
        "last_success_at": "TEXT",
        "last_error_at": "TEXT",
        "last_error_message": "TEXT",
        "consecutive_failures": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, col_type in feed_migrations.items():
        if column not in feed_cols:
            conn.execute(f"ALTER TABLE feeds ADD COLUMN {column} {col_type}")
    pref_cols = {row[1] for row in conn.execute("PRAGMA table_info(user_preferences)").fetchall()}
    if "ui_prefs_json" not in pref_cols:
        conn.execute(
            "ALTER TABLE user_preferences ADD COLUMN ui_prefs_json TEXT NOT NULL DEFAULT '{}'",
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_config (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    fundamentals_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(fundamentals)").fetchall()
    }
    if "source_updated_at" not in fundamentals_cols:
        conn.execute("ALTER TABLE fundamentals ADD COLUMN source_updated_at TEXT")

    company_scores_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(company_scores)").fetchall()
    }
    if "scoring_version" not in company_scores_cols:
        conn.execute(
            "ALTER TABLE company_scores ADD COLUMN scoring_version INTEGER NOT NULL DEFAULT 1"
        )

    if "enrichment_version" not in article_cols:
        conn.execute(
            "ALTER TABLE articles ADD COLUMN enrichment_version INTEGER NOT NULL DEFAULT 0"
        )

    embedding_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(article_embedding_vectors)").fetchall()
    }
    if embedding_cols and "updated_at" not in embedding_cols:
        conn.execute(
            "ALTER TABLE article_embedding_vectors ADD COLUMN updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        )
        conn.execute(
            """
            UPDATE article_embedding_vectors
            SET updated_at = created_at
            WHERE updated_at IS NULL OR updated_at = ''
            """
        )

    prices_cols = {row[1] for row in conn.execute("PRAGMA table_info(prices)").fetchall()}
    if prices_cols and "fetched_at" not in prices_cols:
        conn.execute(
            "ALTER TABLE prices ADD COLUMN fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        )
        conn.execute(
            """
            UPDATE prices
            SET fetched_at = created_at
            WHERE fetched_at IS NULL OR fetched_at = ''
            """
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company_tags (
            ticker TEXT NOT NULL,
            tag TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker, tag)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_company_tags_tag ON company_tags(tag)"
    )

    conn.commit()


def _seed_default_watchlist(conn: sqlite3.Connection) -> None:
    exists = conn.execute(
        "SELECT 1 FROM watchlists WHERE name = ?", ("Portfolio",)
    ).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO watchlists (name, description) VALUES (?, ?)",
            ("Portfolio", "Default portfolio watchlist"),
        )
        conn.commit()


def _seed_default_preferences(conn: sqlite3.Connection) -> None:
    exists = conn.execute("SELECT 1 FROM user_preferences WHERE id = 1").fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO user_preferences (id, theme) VALUES (1, 'dark')",
        )
        conn.commit()


def init_db(path: str) -> None:
    conn = connect_db(path)
    try:
        conn.executescript(SCHEMA)
        migrate_schema(conn)
        _seed_default_watchlist(conn)
        _seed_default_preferences(conn)
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
