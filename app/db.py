from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from flask import Flask, current_app, g

logger = logging.getLogger("stock_tracker.db")

T = TypeVar("T")

SQLITE_LOCK_ERRORS = frozenset({"database is locked", "database is busy"})


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

CREATE TABLE IF NOT EXISTS company_rank_snapshots (
    ticker TEXT NOT NULL,
    composite TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    composite_score REAL,
    rank_in_universe INTEGER,
    factor_json TEXT,
    PRIMARY KEY (ticker, composite, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_rank_snapshots_composite_date
    ON company_rank_snapshots(composite, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_rank_snapshots_ticker_composite
    ON company_rank_snapshots(ticker, composite, snapshot_date DESC);

CREATE TABLE IF NOT EXISTS company_narrative_snapshots (
    ticker TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    states_json TEXT,
    divergence_score REAL,
    divergence_signal TEXT,
    emerging_situations_json TEXT,
    computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_narrative_snapshots_ticker_date
    ON company_narrative_snapshots(ticker, snapshot_date DESC);

CREATE TABLE IF NOT EXISTS research_queue (
    id INTEGER PRIMARY KEY,
    ticker TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_date TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    details_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT,
    dismissed INTEGER NOT NULL DEFAULT 0,
    UNIQUE (ticker, event_type, event_date)
);

CREATE INDEX IF NOT EXISTS idx_research_queue_priority
    ON research_queue(dismissed, priority, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_queue_ticker
    ON research_queue(ticker, created_at DESC);

CREATE TABLE IF NOT EXISTS saved_screens (
    id TEXT NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    spec_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_saved_screens_updated
    ON saved_screens(updated_at DESC);

CREATE TABLE IF NOT EXISTS company_edgar_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    form_type TEXT NOT NULL,
    item_number TEXT,
    filed_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    summary TEXT,
    accession TEXT,
    source TEXT NOT NULL DEFAULT 'sec_edgar',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    UNIQUE (company_id, accession, item_number, event_type)
);

CREATE INDEX IF NOT EXISTS idx_edgar_events_company_date
    ON company_edgar_events(company_id, filed_date DESC);

CREATE TABLE IF NOT EXISTS company_edgar_flags (
    company_id INTEGER NOT NULL,
    flag_type TEXT NOT NULL,
    filed_date TEXT,
    accession TEXT,
    details TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (company_id, flag_type),
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_insider_ownership (
    company_id INTEGER NOT NULL,
    as_of_date TEXT NOT NULL,
    ownership_pct REAL,
    shares_held REAL,
    shares_outstanding REAL,
    source TEXT NOT NULL DEFAULT 'sec_edgar',
    computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (company_id, as_of_date),
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_activist_filings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    filed_date TEXT NOT NULL,
    form_type TEXT NOT NULL,
    accession TEXT NOT NULL,
    filer_name TEXT,
    ownership_pct REAL,
    summary TEXT,
    source TEXT NOT NULL DEFAULT 'sec_edgar',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    UNIQUE (company_id, accession)
);

CREATE INDEX IF NOT EXISTS idx_activist_filings_company_date
    ON company_activist_filings(company_id, filed_date DESC);

CREATE TABLE IF NOT EXISTS company_debt_maturities (
    company_id INTEGER NOT NULL,
    period_end TEXT NOT NULL,
    maturity_year TEXT NOT NULL,
    amount REAL,
    source TEXT NOT NULL DEFAULT 'sec_edgar',
    computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (company_id, period_end, maturity_year),
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_segments (
    company_id INTEGER NOT NULL,
    period_end TEXT NOT NULL,
    segment_name TEXT NOT NULL,
    revenue REAL,
    operating_income REAL,
    margin REAL,
    source TEXT NOT NULL DEFAULT 'sec_edgar',
    computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (company_id, period_end, segment_name),
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_market_data (
    ticker TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, as_of_date, metric)
);

CREATE INDEX IF NOT EXISTS idx_company_market_data_ticker_metric
    ON company_market_data(ticker, metric, as_of_date DESC);
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


def is_sqlite_lock_error(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and str(exc).lower() in SQLITE_LOCK_ERRORS


def retry_on_sqlite_lock(
    fn: Callable[[], T],
    *,
    max_attempts: int = 8,
    base_delay_seconds: float = 0.25,
    operation: str = "sqlite_write",
) -> T:
    """Retry transient SQLite writer contention; re-raises non-lock errors immediately."""
    delay = base_delay_seconds
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if not is_sqlite_lock_error(exc) or attempt >= max_attempts:
                raise
            logger.warning(
                "%s lock contention attempt=%s/%s retry_in=%.2fs",
                operation,
                attempt,
                max_attempts,
                delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, 5.0)
    raise RuntimeError("retry_on_sqlite_lock exhausted without raising")


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
        "event_cluster_id": "INTEGER",
        "news_importance_score": "REAL",
        "divergence_context": "TEXT",
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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_news_importance_score ON articles(news_importance_score DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_event_cluster_id ON articles(event_cluster_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS article_event_clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            headline TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            article_count INTEGER NOT NULL DEFAULT 1,
            source_count INTEGER NOT NULL DEFAULT 1,
            source_domains_json TEXT,
            consensus_sentiment REAL,
            centroid_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_article_event_clusters_type_seen
        ON article_event_clusters(event_type, last_seen_at DESC)
        """
    )

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
        "source_weight": "REAL DEFAULT 0.55",
        "enabled_by_default": "INTEGER NOT NULL DEFAULT 1",
        "pack_tags": "TEXT",
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
    if "survivability_bucket" not in company_scores_cols:
        conn.execute("ALTER TABLE company_scores ADD COLUMN survivability_bucket TEXT")
    if "beneish_components" not in company_scores_cols:
        conn.execute("ALTER TABLE company_scores ADD COLUMN beneish_components TEXT")
    if "thesis_version" not in company_scores_cols:
        conn.execute(
            "ALTER TABLE company_scores ADD COLUMN thesis_version INTEGER NOT NULL DEFAULT 0"
        )
    if "pillar_version" not in company_scores_cols:
        conn.execute(
            "ALTER TABLE company_scores ADD COLUMN pillar_version INTEGER NOT NULL DEFAULT 0"
        )

    if "enrichment_version" not in article_cols:
        conn.execute(
            "ALTER TABLE articles ADD COLUMN enrichment_version INTEGER NOT NULL DEFAULT 0"
        )

    embedding_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(article_embedding_vectors)").fetchall()
    }
    if embedding_cols and "updated_at" not in embedding_cols:
        # SQLite ALTER ADD cannot use CURRENT_TIMESTAMP as column default.
        conn.execute("ALTER TABLE article_embedding_vectors ADD COLUMN updated_at TEXT")
        conn.execute(
            """
            UPDATE article_embedding_vectors
            SET updated_at = COALESCE(NULLIF(created_at, ''), datetime('now'))
            WHERE updated_at IS NULL OR updated_at = ''
            """
        )

    prices_cols = {row[1] for row in conn.execute("PRAGMA table_info(prices)").fetchall()}
    if prices_cols and "fetched_at" not in prices_cols:
        conn.execute("ALTER TABLE prices ADD COLUMN fetched_at TEXT")
        conn.execute(
            """
            UPDATE prices
            SET fetched_at = COALESCE(NULLIF(created_at, ''), datetime('now'))
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

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company_rank_snapshots (
            ticker TEXT NOT NULL,
            composite TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            composite_score REAL,
            rank_in_universe INTEGER,
            factor_json TEXT,
            PRIMARY KEY (ticker, composite, snapshot_date)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rank_snapshots_composite_date
        ON company_rank_snapshots(composite, snapshot_date DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rank_snapshots_ticker_composite
        ON company_rank_snapshots(ticker, composite, snapshot_date DESC)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company_narrative_snapshots (
            ticker TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            states_json TEXT,
            divergence_score REAL,
            divergence_signal TEXT,
            emerging_situations_json TEXT,
            computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker, snapshot_date)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_narrative_snapshots_ticker_date
        ON company_narrative_snapshots(ticker, snapshot_date DESC)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company_thesis_snapshots (
            id INTEGER PRIMARY KEY,
            company_id INTEGER NOT NULL REFERENCES companies(id),
            ticker TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            thesis_version INTEGER NOT NULL DEFAULT 1,
            pillar_version INTEGER NOT NULL DEFAULT 1,
            scoring_version INTEGER NOT NULL DEFAULT 1,
            gates_json TEXT,
            pillars_json TEXT,
            thesis_json TEXT,
            disqualified INTEGER NOT NULL DEFAULT 0,
            composite_score REAL,
            computed_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (company_id, snapshot_date, thesis_version)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_thesis_snapshots_ticker_date
        ON company_thesis_snapshots(ticker, snapshot_date DESC)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_queue (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_date TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 50,
            details_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT,
            dismissed INTEGER NOT NULL DEFAULT 0,
            UNIQUE (ticker, event_type, event_date)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_research_queue_priority
        ON research_queue(dismissed, priority, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_research_queue_ticker
        ON research_queue(ticker, created_at DESC)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_screens (
            id TEXT NOT NULL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            spec_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_saved_screens_updated
        ON saved_screens(updated_at DESC)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS catalyst_calendar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            event_type TEXT NOT NULL DEFAULT 'earnings',
            event_date TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'derived',
            confidence REAL,
            details_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (ticker, event_type, event_date)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_catalyst_calendar_date
        ON catalyst_calendar(event_date)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS short_interest_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            settlement_date TEXT NOT NULL,
            short_interest REAL,
            avg_daily_volume REAL,
            days_to_cover REAL,
            source TEXT NOT NULL DEFAULT 'finra',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (ticker, settlement_date, source)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_short_interest_ticker_date
        ON short_interest_snapshots(ticker, settlement_date DESC)
        """
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
