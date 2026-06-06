from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Iterable

try:
    from rapidfuzz import fuzz  # type: ignore
except ImportError:  # pragma: no cover
    fuzz = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def counts(self) -> dict:
        tables = ["companies", "feeds", "articles", "fundamentals", "prices", "insider_transactions", "ingestion_jobs"]
        output = {}
        for table in tables:
            output[table] = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return output

    def status_snapshot(self) -> dict:
        counts = self.counts()
        freshness = {
            "companiesUpdatedAt": self.conn.execute("SELECT MAX(updated_at) FROM companies").fetchone()[0],
            "feedsLastPolledAt": self.conn.execute("SELECT MAX(last_polled_at) FROM feeds").fetchone()[0],
            "fundamentalsUpdatedAt": self.conn.execute("SELECT MAX(updated_at) FROM fundamentals").fetchone()[0],
            "latestArticlePublishedAt": self.conn.execute("SELECT MAX(published_at) FROM articles").fetchone()[0],
            "latestArticleFetchedAt": self.conn.execute("SELECT MAX(fetched_at) FROM articles").fetchone()[0],
            "pricesUpdatedAt": self.conn.execute("SELECT MAX(created_at) FROM prices").fetchone()[0],
            "insidersUpdatedAt": self.conn.execute("SELECT MAX(created_at) FROM insider_transactions").fetchone()[0],
        }
        jobs = {
            "queued": self.conn.execute("SELECT COUNT(*) FROM ingestion_jobs WHERE status='queued'").fetchone()[0],
            "running": self.conn.execute("SELECT COUNT(*) FROM ingestion_jobs WHERE status='running'").fetchone()[0],
            "failed": self.conn.execute("SELECT COUNT(*) FROM ingestion_jobs WHERE status='failed'").fetchone()[0],
        }
        feed_rows = self.conn.execute(
            """
            SELECT name, feed_url, last_polled_at, category
            FROM feeds
            ORDER BY last_polled_at IS NULL, last_polled_at DESC, name
            LIMIT 25
            """
        ).fetchall()
        return {
            "counts": counts,
            "freshness": freshness,
            "jobs": jobs,
            "feeds": [dict(row) for row in feed_rows],
        }

    def upsert_companies(self, companies: Iterable[dict]) -> int:
        rows = [
            (
                company["ticker"].upper(),
                company["name"],
                company.get("cik"),
                company.get("exchange"),
                company.get("sector"),
                company.get("industry"),
                company.get("sec_filings_url"),
                company.get("company_site"),
                company.get("source", "sec"),
            )
            for company in companies
            if company.get("ticker") and company.get("name")
        ]
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO companies (
                ticker, name, cik, exchange, sector, industry, sec_filings_url, company_site, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                name=excluded.name,
                cik=COALESCE(excluded.cik, companies.cik),
                exchange=COALESCE(excluded.exchange, companies.exchange),
                sector=COALESCE(excluded.sector, companies.sector),
                industry=COALESCE(excluded.industry, companies.industry),
                sec_filings_url=COALESCE(excluded.sec_filings_url, companies.sec_filings_url),
                company_site=COALESCE(excluded.company_site, companies.company_site),
                source=excluded.source,
                updated_at=CURRENT_TIMESTAMP
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def search_companies(self, query: str, limit: int = 10) -> list[dict]:
        like = f"%{query.upper()}%"
        rows = self.conn.execute(
            """
            SELECT ticker, name, cik, exchange, sector, industry, sec_filings_url, company_site
            FROM companies
            WHERE UPPER(ticker) LIKE ? OR UPPER(name) LIKE ?
            ORDER BY CASE WHEN UPPER(ticker) = ? THEN 0 ELSE 1 END, ticker
            LIMIT ?
            """,
            (like, like, query.upper(), limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_company_by_ticker(self, ticker: str) -> dict | None:
        row = self.conn.execute(
            """
            SELECT id, ticker, name, cik, exchange, sector, industry, sec_filings_url, company_site
            FROM companies
            WHERE ticker = ?
            """,
            (ticker.upper(),),
        ).fetchone()
        return dict(row) if row else None

    def list_companies_for_matching(self) -> list[dict]:
        rows = self.conn.execute("SELECT id, ticker, name FROM companies").fetchall()
        return [dict(row) for row in rows]

    def upsert_fundamentals(self, records: Iterable[dict]) -> int:
        rows = [
            (
                record["company_id"],
                record["metric"],
                record.get("value"),
                record.get("unit"),
                record["period_end"],
                record["period_type"],
                record["dimension"],
                record.get("fiscal_year"),
                record.get("fiscal_quarter"),
                record.get("filing_date"),
                record.get("form"),
                record.get("accession"),
                record["source"],
                record.get("taxonomy"),
                record.get("xbrl_concept"),
            )
            for record in records
        ]
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO fundamentals (
                company_id, metric, value, unit, period_end, period_type, dimension,
                fiscal_year, fiscal_quarter, filing_date, form, accession, source, taxonomy, xbrl_concept
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, metric, period_end, dimension, filing_date, xbrl_concept) DO UPDATE SET
                value=excluded.value,
                unit=excluded.unit,
                period_type=excluded.period_type,
                fiscal_year=excluded.fiscal_year,
                fiscal_quarter=excluded.fiscal_quarter,
                form=excluded.form,
                accession=excluded.accession,
                source=excluded.source,
                taxonomy=excluded.taxonomy,
                updated_at=CURRENT_TIMESTAMP
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def fetch_fundamentals_rows(self, tickers: list[str], gte: str | None = None, dimension: str | None = None) -> list[dict]:
        if not tickers:
            return []
        params: list = [ticker.upper() for ticker in tickers]
        placeholders = ",".join("?" for _ in tickers)
        sql = f"""
            SELECT
                c.ticker,
                c.name AS company_name,
                f.metric,
                f.value,
                f.unit,
                f.period_end,
                f.period_type,
                f.dimension,
                f.fiscal_year,
                f.fiscal_quarter,
                f.filing_date,
                f.form,
                f.accession,
                f.source
            FROM fundamentals f
            JOIN companies c ON c.id = f.company_id
            WHERE c.ticker IN ({placeholders})
        """
        if gte:
            sql += " AND f.period_end >= ?"
            params.append(gte)
        if dimension:
            sql += " AND f.dimension = ?"
            params.append(dimension)
        sql += " ORDER BY c.ticker, f.period_end DESC, f.metric"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def upsert_feed(self, feed: dict) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO feeds (name, feed_url, domain, category, is_active, etag, last_modified, last_polled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(feed_url) DO UPDATE SET
                name=excluded.name,
                domain=excluded.domain,
                category=excluded.category,
                is_active=excluded.is_active,
                etag=COALESCE(excluded.etag, feeds.etag),
                last_modified=COALESCE(excluded.last_modified, feeds.last_modified),
                last_polled_at=excluded.last_polled_at,
                updated_at=CURRENT_TIMESTAMP
            RETURNING id
            """,
            (
                feed["name"],
                feed["feed_url"],
                feed.get("domain"),
                feed.get("category"),
                int(feed.get("is_active", True)),
                feed.get("etag"),
                feed.get("last_modified"),
                feed.get("last_polled_at", utc_now_iso()),
            ),
        )
        feed_id = cursor.fetchone()[0]
        self.conn.commit()
        return feed_id

    def find_duplicate_title(self, title: str, threshold: int = 90, lookback: int = 500) -> int | None:
        if fuzz is None:
            return None
        rows = self.conn.execute(
            "SELECT id, title FROM articles ORDER BY id DESC LIMIT ?",
            (lookback,),
        ).fetchall()
        normalized = title.lower().strip()
        for row in rows:
            candidate = (row["title"] or "").lower().strip()
            if candidate and fuzz.ratio(normalized, candidate) >= threshold:
                return row["id"]
        return None

    def upsert_article(self, article: dict) -> int:
        duplicate_id = article.get("duplicate_of_article_id")
        if duplicate_id is None:
            duplicate_id = self.find_duplicate_title(article["title"])
        cursor = self.conn.execute(
            """
            INSERT INTO articles (
                canonical_url, url_hash, title, summary, body_text, source_domain,
                published_at, fetched_at, content_hash, language, duplicate_of_article_id,
                sentiment_label, sentiment_score, topic_cluster_id, raw_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url_hash) DO UPDATE SET
                canonical_url=COALESCE(excluded.canonical_url, articles.canonical_url),
                title=excluded.title,
                summary=COALESCE(excluded.summary, articles.summary),
                body_text=COALESCE(excluded.body_text, articles.body_text),
                source_domain=COALESCE(excluded.source_domain, articles.source_domain),
                published_at=COALESCE(excluded.published_at, articles.published_at),
                fetched_at=COALESCE(excluded.fetched_at, articles.fetched_at),
                content_hash=COALESCE(excluded.content_hash, articles.content_hash),
                language=COALESCE(excluded.language, articles.language),
                duplicate_of_article_id=COALESCE(excluded.duplicate_of_article_id, articles.duplicate_of_article_id),
                raw_source=COALESCE(excluded.raw_source, articles.raw_source),
                updated_at=CURRENT_TIMESTAMP
            RETURNING id
            """,
            (
                article.get("canonical_url"),
                article["url_hash"],
                article["title"],
                article.get("summary"),
                article.get("body_text"),
                article.get("source_domain"),
                article.get("published_at"),
                article.get("fetched_at"),
                article.get("content_hash"),
                article.get("language"),
                duplicate_id,
                article.get("sentiment_label"),
                article.get("sentiment_score"),
                article.get("topic_cluster_id"),
                article.get("raw_source"),
            ),
        )
        article_id = cursor.fetchone()[0]
        self.conn.commit()
        return article_id

    def link_article_company(self, article_id: int, company_id: int, match_type: str, confidence: float) -> None:
        self.conn.execute(
            """
            INSERT INTO article_company (article_id, company_id, match_type, confidence)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(article_id, company_id) DO UPDATE SET
                match_type=excluded.match_type,
                confidence=excluded.confidence
            """,
            (article_id, company_id, match_type, confidence),
        )
        self.conn.commit()

    def get_company_news(self, ticker: str, limit: int = 25) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT a.id, a.title, a.summary, a.body_text, a.canonical_url, a.published_at, a.source_domain
            FROM articles a
            JOIN article_company ac ON ac.article_id = a.id
            JOIN companies c ON c.id = ac.company_id
            WHERE c.ticker = ?
            ORDER BY a.published_at DESC, a.id DESC
            LIMIT ?
            """,
            (ticker.upper(), limit),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "description": row["summary"] or row["body_text"],
                "url": row["canonical_url"],
                "publishedDate": row["published_at"],
                "sourceDomain": row["source_domain"],
            }
            for row in rows
        ]

    def get_cached_http_response(self, cache_key: str) -> dict | None:
        row = self.conn.execute(
            """
            SELECT cache_key, url, status_code, etag, last_modified, response_body, fetched_at, expires_at
            FROM http_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        ).fetchone()
        return dict(row) if row else None

    def put_cached_http_response(self, cache_key: str, url: str, status_code: int, response_body: str, expires_at: str | None) -> None:
        now = utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO http_cache (cache_key, url, status_code, response_body, fetched_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                status_code=excluded.status_code,
                response_body=excluded.response_body,
                fetched_at=excluded.fetched_at,
                expires_at=excluded.expires_at
            """,
            (cache_key, url, status_code, response_body, now, expires_at),
        )
        self.conn.commit()

    def upsert_prices(self, ticker: str, rows: Iterable[dict], source: str) -> int:
        payload = [
            (
                ticker.upper(),
                row["date"],
                row.get("open"),
                row.get("high"),
                row.get("low"),
                row.get("close"),
                row.get("volume"),
                row.get("source", source),
            )
            for row in rows
            if row.get("date") and row.get("close") is not None
        ]
        if not payload:
            return 0
        self.conn.executemany(
            """
            INSERT INTO prices (ticker, date, open, high, low, close, volume, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date, source) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume
            """,
            payload,
        )
        self.conn.commit()
        return len(payload)

    def fetch_prices(self, ticker: str, since: str | None = None, limit: int | None = None) -> list[dict]:
        sql = "SELECT ticker, date, open, high, low, close, volume, source FROM prices WHERE ticker = ?"
        params: list = [ticker.upper()]
        if since:
            sql += " AND date >= ?"
            params.append(since)
        sql += " ORDER BY date DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def upsert_insider_transactions(self, company_id: int, transactions: Iterable[dict]) -> int:
        rows = [
            (
                company_id,
                item.get("filing_date"),
                item.get("transaction_date"),
                item.get("owner_name"),
                item.get("transaction_code"),
                item.get("shares"),
                item.get("price_per_share"),
                item.get("transaction_value"),
                item.get("security_title"),
                item.get("form"),
                item.get("accession"),
                item.get("source", "sec_edgar"),
            )
            for item in transactions
        ]
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO insider_transactions (
                company_id, filing_date, transaction_date, owner_name, transaction_code,
                shares, price_per_share, transaction_value, security_title, form, accession, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, accession, owner_name, transaction_date, transaction_code, shares) DO UPDATE SET
                filing_date=excluded.filing_date,
                transaction_value=excluded.transaction_value,
                price_per_share=excluded.price_per_share,
                security_title=excluded.security_title
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def fetch_insider_transactions(self, ticker: str, limit: int = 500) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT
                c.ticker,
                c.name AS company_name,
                i.filing_date,
                i.transaction_date,
                i.owner_name,
                i.transaction_code,
                i.transaction_value,
                i.security_title,
                CASE WHEN i.transaction_code = 'S' THEN 'ND' ELSE 'NA' END AS security_ad_code
            FROM insider_transactions i
            JOIN companies c ON c.id = i.company_id
            WHERE c.ticker = ?
            ORDER BY i.filing_date DESC, i.transaction_date DESC
            LIMIT ?
            """,
            (ticker.upper(), limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def fetch_insider_buying_sums(self, tickers: list[str] | None = None) -> list[dict]:
        six_months_ago = (datetime.now(timezone.utc).date() - timedelta(days=183)).isoformat()
        three_months_ago = (datetime.now(timezone.utc).date() - timedelta(days=92)).isoformat()
        one_month_ago = (datetime.now(timezone.utc).date() - timedelta(days=31)).isoformat()
        ticker_filter = ""
        params: list = [six_months_ago, three_months_ago, one_month_ago, six_months_ago]
        if tickers:
            placeholders = ",".join("?" for _ in tickers)
            ticker_filter = f" AND c.ticker IN ({placeholders})"
            params.extend([ticker.upper() for ticker in tickers])
        rows = self.conn.execute(
            f"""
            SELECT
                c.ticker,
                c.name AS company,
                SUM(
                    CASE
                        WHEN i.transaction_date >= ? THEN
                            CASE WHEN i.transaction_code = 'S' THEN -ABS(COALESCE(i.transaction_value, 0))
                                 ELSE ABS(COALESCE(i.transaction_value, 0)) END
                        ELSE 0
                    END
                ) AS buy6m,
                SUM(
                    CASE
                        WHEN i.transaction_date >= ? THEN
                            CASE WHEN i.transaction_code = 'S' THEN -ABS(COALESCE(i.transaction_value, 0))
                                 ELSE ABS(COALESCE(i.transaction_value, 0)) END
                        ELSE 0
                    END
                ) AS buy3m,
                SUM(
                    CASE
                        WHEN i.transaction_date >= ? THEN
                            CASE WHEN i.transaction_code = 'S' THEN -ABS(COALESCE(i.transaction_value, 0))
                                 ELSE ABS(COALESCE(i.transaction_value, 0)) END
                        ELSE 0
                    END
                ) AS buy1m,
                COUNT(DISTINCT CASE WHEN i.transaction_code = 'P' AND i.transaction_date >= ? THEN i.owner_name END) AS owners6m
            FROM insider_transactions i
            JOIN companies c ON c.id = i.company_id
            WHERE i.transaction_code IN ('P', 'S')
              AND COALESCE(i.transaction_value, 0) >= 100000
              AND (i.security_title IS NULL OR i.security_title NOT LIKE '%Preferred%')
              {ticker_filter}
            GROUP BY c.ticker, c.name
            ORDER BY buy6m DESC
            """,
            params,
        ).fetchall()
        return [
            {
                "ticker": row["ticker"],
                "company": row["company"],
                "buy6m": row["buy6m"],
                "buy3m": row["buy3m"],
                "buy1m": row["buy1m"],
                "owners6m": row["owners6m"],
            }
            for row in rows
        ]

    def enqueue_job(self, job_type: str, payload: dict, priority: int = 100) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO ingestion_jobs (job_type, payload_json, priority)
            VALUES (?, ?, ?)
            RETURNING id
            """,
            (job_type, json.dumps(payload), priority),
        )
        job_id = cursor.fetchone()[0]
        self.conn.commit()
        return job_id

    def claim_next_job(self) -> dict | None:
        row = self.conn.execute(
            """
            SELECT id, job_type, payload_json, attempt_count
            FROM ingestion_jobs
            WHERE status = 'queued' AND available_at <= CURRENT_TIMESTAMP
            ORDER BY priority ASC, id ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        updated = self.conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = 'running', locked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'queued'
            """,
            (row["id"],),
        )
        if updated.rowcount == 0:
            return None
        self.conn.commit()
        return {
            "id": row["id"],
            "job_type": row["job_type"],
            "payload": json.loads(row["payload_json"]),
            "attempt_count": row["attempt_count"],
        }

    def complete_job(self, job_id: int, status: str = "done", error_message: str | None = None) -> None:
        self.conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = ?, updated_at = CURRENT_TIMESTAMP, locked_at = NULL
            WHERE id = ?
            """,
            (status, job_id),
        )
        self.conn.execute(
            """
            INSERT INTO job_runs (job_id, finished_at, status, error_message)
            VALUES (?, CURRENT_TIMESTAMP, ?, ?)
            """,
            (job_id, status, error_message),
        )
        self.conn.commit()

    def fail_job(self, job_id: int, error_message: str, retry_in_minutes: int = 15) -> None:
        row = self.conn.execute("SELECT attempt_count FROM ingestion_jobs WHERE id = ?", (job_id,)).fetchone()
        attempts = (row["attempt_count"] if row else 0) + 1
        status = "failed" if attempts >= 3 else "queued"
        self.conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = ?,
                attempt_count = ?,
                available_at = datetime('now', ?),
                locked_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, attempts, f"+{retry_in_minutes} minutes", job_id),
        )
        self.conn.execute(
            """
            INSERT INTO job_runs (job_id, finished_at, status, error_message)
            VALUES (?, CURRENT_TIMESTAMP, 'error', ?)
            """,
            (job_id, error_message),
        )
        self.conn.commit()
