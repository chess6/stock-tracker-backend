from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Iterable


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def counts(self) -> dict:
        tables = ["companies", "feeds", "articles", "fundamentals", "ingestion_jobs"]
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
        }
        return {"counts": counts, "freshness": freshness}

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

    def upsert_article(self, article: dict) -> int:
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
                article.get("duplicate_of_article_id"),
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
