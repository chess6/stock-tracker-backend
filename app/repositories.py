from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .db import retry_on_sqlite_lock

try:
    from rapidfuzz import fuzz  # type: ignore
except ImportError:  # pragma: no cover
    fuzz = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _news_display_match_clause(column_alias: str) -> tuple[str, list[str]]:
    from .services.entity_linking import NEWS_DISPLAY_MATCH_STRATEGIES

    strategies = sorted(NEWS_DISPLAY_MATCH_STRATEGIES)
    placeholders = ",".join("?" for _ in strategies)
    return (
        f"COALESCE({column_alias}.match_strategy, {column_alias}.match_type, '') IN ({placeholders})",
        strategies,
    )


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def commit(self) -> None:
        retry_on_sqlite_lock(lambda: self.conn.commit(), operation="repository_commit")

    def get_config(self, key: str) -> Any | None:
        row = self.conn.execute(
            "SELECT value_json FROM app_config WHERE key = ?",
            (key,),
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["value_json"])
        except json.JSONDecodeError:
            return None

    def set_config(self, key: str, value: Any) -> None:
        self.conn.execute(
            """
            INSERT INTO app_config (key, value_json, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, json.dumps(value)),
        )
        self.commit()

    def get_all_config(self) -> dict[str, Any]:
        rows = self.conn.execute("SELECT key, value_json FROM app_config").fetchall()
        output: dict[str, Any] = {}
        for row in rows:
            try:
                output[row["key"]] = json.loads(row["value_json"])
            except json.JSONDecodeError:
                continue
        return output

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
            "companyScoresUpdatedAt": self.conn.execute("SELECT MAX(computed_at) FROM company_scores").fetchone()[0],
            "latestArticlePublishedAt": self.conn.execute("SELECT MAX(published_at) FROM articles").fetchone()[0],
            "latestArticleFetchedAt": self.conn.execute("SELECT MAX(fetched_at) FROM articles").fetchone()[0],
            "pricesUpdatedAt": self.conn.execute("SELECT MAX(created_at) FROM prices").fetchone()[0],
            "insidersUpdatedAt": self.conn.execute("SELECT MAX(created_at) FROM insider_transactions").fetchone()[0],
            "insiderClustersUpdatedAt": self.conn.execute(
                "SELECT MAX(computed_at) FROM insider_cluster_analysis"
            ).fetchone()[0],
        }
        coverage = {
            "companiesMissingMetadata": self.count_companies_missing_metadata(),
            "articlesWithMarketReactions": self.conn.execute(
                "SELECT COUNT(DISTINCT article_id) FROM article_market_reactions"
            ).fetchone()[0],
            "linkedArticles": self.conn.execute(
                "SELECT COUNT(DISTINCT article_id) FROM article_company"
            ).fetchone()[0],
        }
        jobs = {
            "queued": self.conn.execute("SELECT COUNT(*) FROM ingestion_jobs WHERE status='queued'").fetchone()[0],
            "running": self.conn.execute("SELECT COUNT(*) FROM ingestion_jobs WHERE status='running'").fetchone()[0],
            "failed": self.conn.execute("SELECT COUNT(*) FROM ingestion_jobs WHERE status='failed'").fetchone()[0],
        }
        feed_rows = self.conn.execute(
            """
            SELECT
                name,
                feed_url,
                last_polled_at,
                last_success_at,
                last_error_at,
                last_error_message,
                consecutive_failures,
                category
            FROM feeds
            ORDER BY consecutive_failures DESC, last_polled_at IS NULL, last_polled_at DESC, name
            LIMIT 50
            """
        ).fetchall()
        recent_jobs = self.list_job_runs(limit=15)
        return {
            "counts": counts,
            "freshness": freshness,
            "coverage": coverage,
            "jobs": jobs,
            "feeds": [dict(row) for row in feed_rows],
            "recentJobRuns": recent_jobs,
        }

    def pipeline_status_snapshot(self) -> dict:
        from .services.freshness import (
            CURRENT_SCORING_VERSION,
            DEFAULT_STALE_FUNDAMENTALS_DAYS,
            DEFAULT_STALE_PRICES_DAYS,
        )

        articles = self.get_pipeline_status_counts()
        freshness = {
            "fundamentalsUpdatedAt": self.conn.execute(
                "SELECT MAX(updated_at) FROM fundamentals"
            ).fetchone()[0],
            "fundamentalsSourceUpdatedAt": self.conn.execute(
                "SELECT MAX(source_updated_at) FROM fundamentals"
            ).fetchone()[0],
            "pricesFetchedAt": self._max_prices_fetched_at(),
            "scoresComputedAt": self.conn.execute(
                "SELECT MAX(computed_at) FROM company_scores"
            ).fetchone()[0],
            "embeddingsUpdatedAt": self.conn.execute(
                "SELECT MAX(updated_at) FROM article_embedding_vectors"
            ).fetchone()[0],
            "articlesFetchedAt": self.conn.execute(
                "SELECT MAX(fetched_at) FROM articles"
            ).fetchone()[0],
            "articlesMaxEnrichmentVersion": self.conn.execute(
                "SELECT MAX(COALESCE(enrichment_version, 0)) FROM articles"
            ).fetchone()[0],
        }
        stale = {
            "fundamentalsTickers": len(
                self.fetch_stale_fundamentals_tickers(DEFAULT_STALE_FUNDAMENTALS_DAYS)
            ),
            "pricesTickers": len(self.fetch_stale_prices_tickers(DEFAULT_STALE_PRICES_DAYS)),
            "scoresNeedingRecompute": len(
                self.fetch_scores_needing_recompute(CURRENT_SCORING_VERSION)
            ),
            "staleAfterDays": {
                "fundamentals": DEFAULT_STALE_FUNDAMENTALS_DAYS,
                "prices": DEFAULT_STALE_PRICES_DAYS,
            },
        }
        recent_jobs = self.list_job_runs(limit=5)
        return {
            "articles": articles,
            "freshness": freshness,
            "stale": stale,
            "versions": {
                "scoring": CURRENT_SCORING_VERSION,
            },
            "lastJobRun": recent_jobs[0] if recent_jobs else None,
            "recentJobRuns": recent_jobs,
        }

    def _max_prices_fetched_at(self) -> str | None:
        row = self.conn.execute(
            """
            SELECT MAX(COALESCE(fetched_at, created_at)) AS latest
            FROM prices
            """
        ).fetchone()
        return row["latest"] if row else None

    def _stale_cutoff_iso(self, stale_after_days: int) -> str:
        cutoff = datetime.now(timezone.utc) - timedelta(days=stale_after_days)
        return cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def fetch_stale_fundamentals_tickers(self, stale_after_days: int) -> list[str]:
        cutoff = self._stale_cutoff_iso(stale_after_days)
        rows = self.conn.execute(
            """
            SELECT c.ticker
            FROM companies c
            JOIN fundamentals f ON f.company_id = c.id
            GROUP BY c.id, c.ticker
            HAVING MAX(f.updated_at) < ?
            ORDER BY c.ticker
            """,
            (cutoff,),
        ).fetchall()
        return [row["ticker"] for row in rows]

    def fetch_stale_prices_tickers(self, stale_after_days: int) -> list[str]:
        cutoff = self._stale_cutoff_iso(stale_after_days)
        rows = self.conn.execute(
            """
            SELECT ticker
            FROM prices
            GROUP BY ticker
            HAVING MAX(COALESCE(fetched_at, created_at)) < ?
            ORDER BY ticker
            """,
            (cutoff,),
        ).fetchall()
        return [row["ticker"] for row in rows]

    def fetch_scores_needing_recompute(self, scoring_version: int) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT c.ticker
            FROM companies c
            WHERE EXISTS (
                SELECT 1 FROM fundamentals f WHERE f.company_id = c.id
            )
            AND (
                NOT EXISTS (
                    SELECT 1 FROM company_scores cs WHERE cs.company_id = c.id
                )
                OR EXISTS (
                    SELECT 1
                    FROM company_scores cs
                    WHERE cs.company_id = c.id
                      AND COALESCE(cs.scoring_version, 1) < ?
                )
            )
            ORDER BY c.ticker
            """,
            (scoring_version,),
        ).fetchall()
        return [row["ticker"] for row in rows]

    def get_article_embedding_hash(self, article_id: int, *, model: str) -> str | None:
        row = self.conn.execute(
            """
            SELECT content_hash
            FROM article_embedding_vectors
            WHERE article_id = ? AND model = ?
            """,
            (article_id, model),
        ).fetchone()
        return row["content_hash"] if row else None

    def should_skip_score_recompute(
        self,
        company_id: int,
        period_ends: list[str],
        scoring_version: int,
        *,
        dimension: str = "ARY",
    ) -> bool:
        unique_periods = list(dict.fromkeys(end for end in period_ends if end))
        if not unique_periods:
            return True

        latest_fundamentals_at = self.conn.execute(
            """
            SELECT MAX(updated_at) AS latest
            FROM fundamentals
            WHERE company_id = ? AND dimension = ?
            """,
            (company_id, dimension),
        ).fetchone()["latest"]
        if not latest_fundamentals_at:
            return False

        placeholders = ",".join("?" for _ in unique_periods)
        rows = self.conn.execute(
            f"""
            SELECT period_end, COALESCE(scoring_version, 1) AS scoring_version, computed_at
            FROM company_scores
            WHERE company_id = ? AND dimension = ? AND period_end IN ({placeholders})
            """,
            [company_id, dimension, *unique_periods],
        ).fetchall()
        if len(rows) != len(unique_periods):
            return False

        for row in rows:
            if row["scoring_version"] < scoring_version:
                return False
            if row["computed_at"] < latest_fundamentals_at:
                return False
        return True

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
        self.commit()
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

    def fetch_sector_tickers(self, sector: str, limit: int = 150) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT ticker FROM companies
            WHERE sector = ? AND ticker IS NOT NULL AND TRIM(ticker) != ''
            ORDER BY ticker
            LIMIT ?
            """,
            (sector, limit),
        ).fetchall()
        return [row["ticker"] for row in rows]

    def update_company_metadata(self, ticker: str, metadata: dict) -> None:
        self.conn.execute(
            """
            UPDATE companies
            SET sector = COALESCE(?, sector),
                industry = COALESCE(?, industry),
                exchange = COALESCE(?, exchange)
            WHERE ticker = ?
            """,
            (
                metadata.get("sector"),
                metadata.get("industry"),
                metadata.get("exchange"),
                ticker.upper(),
            ),
        )
        self.commit()

    def list_industry_groups(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT sector, industry, COUNT(*) AS company_count
            FROM companies
            WHERE industry IS NOT NULL AND TRIM(industry) != ''
            GROUP BY sector, industry
            ORDER BY sector, industry
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def fetch_industry_peers(self, industry: str, sector: str | None = None, limit: int = 100) -> list[dict]:
        sql = """
            SELECT ticker, name, sector, industry
            FROM companies
            WHERE industry = ?
        """
        params: list = [industry]
        if sector:
            sql += " AND sector = ?"
            params.append(sector)
        sql += " ORDER BY ticker LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def fetch_tickers_with_recent_prices(self, limit: int = 400) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT ticker
            FROM prices
            GROUP BY ticker
            HAVING MAX(date) >= date('now', '-14 days')
            ORDER BY MAX(date) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [row["ticker"] for row in rows]

    def delete_fundamentals_snapshots(self, company_id: int, dimensions: Iterable[str]) -> int:
        dims = [item for item in dimensions if item]
        if not dims:
            return 0
        placeholders = ",".join("?" for _ in dims)
        cursor = self.conn.execute(
            f"""
            DELETE FROM fundamentals
            WHERE company_id = ? AND dimension IN ({placeholders})
            """,
            [company_id, *dims],
        )
        self.commit()
        return cursor.rowcount

    def count_companies_missing_metadata(self) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) FROM companies
            WHERE cik IS NOT NULL AND cik != ''
              AND (sector IS NULL OR TRIM(sector) = '' OR industry IS NULL OR TRIM(industry) = '')
            """
        ).fetchone()
        return int(row[0] if row else 0)

    def fetch_tickers_missing_metadata(self, limit: int = 500) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT ticker FROM companies
            WHERE cik IS NOT NULL AND cik != ''
              AND (sector IS NULL OR TRIM(sector) = '' OR industry IS NULL OR TRIM(industry) = '')
            ORDER BY ticker
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [row["ticker"] for row in rows]

    def fetch_tickers_with_fundamentals(self, dimension: str = "ARY", limit: int | None = None) -> list[str]:
        sql = """
            SELECT DISTINCT c.ticker
            FROM companies c
            INNER JOIN fundamentals f ON f.company_id = c.id
            WHERE f.dimension = ?
            ORDER BY c.ticker
        """
        params: list = [dimension]
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [row["ticker"] for row in rows]

    def fetch_all_tradable_tickers(self, limit: int | None = None) -> list[str]:
        sql = """
            SELECT ticker
            FROM companies
            WHERE cik IS NOT NULL AND TRIM(cik) != ''
            ORDER BY ticker
        """
        params: list = []
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [row["ticker"] for row in rows]

    def fetch_tickers_without_fundamentals(self, candidates: list[str] | None = None) -> list[str]:
        params: list = ["ARY"]
        candidate_clause = ""
        if candidates:
            upper = [item.upper() for item in candidates if item]
            if not upper:
                return []
            placeholders = ",".join("?" for _ in upper)
            candidate_clause = f"AND c.ticker IN ({placeholders})"
            params.extend(upper)
        rows = self.conn.execute(
            f"""
            SELECT c.ticker
            FROM companies c
            WHERE c.cik IS NOT NULL AND TRIM(c.cik) != ''
              {candidate_clause}
              AND NOT EXISTS (
                  SELECT 1
                  FROM fundamentals f
                  WHERE f.company_id = c.id AND f.dimension = ?
              )
            ORDER BY c.ticker
            """,
            params,
        ).fetchall()
        return [row["ticker"] for row in rows]

    def fetch_tickers_without_prices(self, candidates: list[str] | None = None) -> list[str]:
        candidate_clause = ""
        params: list = []
        if candidates:
            upper = [item.upper() for item in candidates if item]
            if not upper:
                return []
            placeholders = ",".join("?" for _ in upper)
            candidate_clause = f"AND c.ticker IN ({placeholders})"
            params.extend(upper)
        rows = self.conn.execute(
            f"""
            SELECT c.ticker
            FROM companies c
            WHERE c.cik IS NOT NULL AND TRIM(c.cik) != ''
              {candidate_clause}
              AND NOT EXISTS (
                  SELECT 1 FROM prices p WHERE p.ticker = c.ticker
              )
            ORDER BY c.ticker
            """,
            params,
        ).fetchall()
        return [row["ticker"] for row in rows]

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
                record.get("source_updated_at") or record.get("filing_date"),
            )
            for record in records
        ]
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO fundamentals (
                company_id, metric, value, unit, period_end, period_type, dimension,
                fiscal_year, fiscal_quarter, filing_date, form, accession, source, taxonomy,
                xbrl_concept, source_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                source_updated_at=COALESCE(excluded.source_updated_at, fundamentals.source_updated_at),
                updated_at=CURRENT_TIMESTAMP
            """,
            rows,
        )
        self.commit()
        return len(rows)

    def fetch_fundamentals_overwrite_state(self, company_id: int) -> dict[tuple, str | None]:
        rows = self.conn.execute(
            """
            SELECT metric, period_end, dimension, filing_date, xbrl_concept, source_updated_at
            FROM fundamentals
            WHERE company_id = ?
            """,
            (company_id,),
        ).fetchall()
        return {
            (
                row["metric"],
                row["period_end"],
                row["dimension"],
                row["filing_date"],
                row["xbrl_concept"],
            ): row["source_updated_at"] or row["filing_date"]
            for row in rows
        }

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

    def upsert_company_scores(self, company_id: int, records: Iterable[dict]) -> int:
        from .services.freshness import CURRENT_SCORING_VERSION

        rows = [
            (
                company_id,
                record["period_end"],
                record.get("dimension", "ARY"),
                record.get("piotroski_f"),
                record.get("altman_z"),
                record.get("beneish_m"),
                record.get("survivability"),
                record.get("survivability_bucket"),
                record.get("piotroski_components"),
                record.get("altman_components"),
                record.get("beneish_components"),
                record.get("scoring_version", CURRENT_SCORING_VERSION),
            )
            for record in records
        ]
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO company_scores (
                company_id, period_end, dimension,
                piotroski_f, altman_z, beneish_m, survivability,
                survivability_bucket,
                piotroski_components, altman_components, beneish_components,
                computed_at, scoring_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(company_id, period_end, dimension) DO UPDATE SET
                piotroski_f=excluded.piotroski_f,
                altman_z=excluded.altman_z,
                beneish_m=excluded.beneish_m,
                survivability=excluded.survivability,
                survivability_bucket=excluded.survivability_bucket,
                piotroski_components=excluded.piotroski_components,
                altman_components=excluded.altman_components,
                beneish_components=excluded.beneish_components,
                computed_at=CURRENT_TIMESTAMP,
                scoring_version=excluded.scoring_version
            """,
            rows,
        )
        self.commit()
        return len(rows)

    def fetch_company_scores(self, company_id: int, dimension: str = "ARY") -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT
                period_end,
                dimension,
                piotroski_f,
                altman_z,
                beneish_m,
                survivability,
                survivability_bucket,
                piotroski_components,
                altman_components,
                beneish_components,
                computed_at
            FROM company_scores
            WHERE company_id = ? AND dimension = ?
            ORDER BY period_end DESC
            """,
            (company_id, dimension),
        ).fetchall()
        return [self._format_score_row(row) for row in rows]

    def upsert_company_rank_snapshots(self, records: Iterable[dict]) -> int:
        import json

        rows = []
        for record in records:
            factors = record.get("factors")
            factor_json = json.dumps(factors) if factors is not None else None
            rows.append(
                (
                    str(record["ticker"]).strip().upper(),
                    str(record["composite"]).strip().lower(),
                    record["snapshot_date"],
                    record.get("composite_score"),
                    record.get("rank_in_universe"),
                    factor_json,
                )
            )
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO company_rank_snapshots (
                ticker, composite, snapshot_date,
                composite_score, rank_in_universe, factor_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, composite, snapshot_date) DO UPDATE SET
                composite_score=excluded.composite_score,
                rank_in_universe=excluded.rank_in_universe,
                factor_json=excluded.factor_json
            """,
            rows,
        )
        self.commit()
        return len(rows)

    def fetch_company_ranks_on_or_before_date(
        self,
        tickers: list[str],
        *,
        composite: str,
        as_of_date: str,
    ) -> dict[str, int]:
        """Most recent rank_in_universe per ticker on or before as_of_date."""
        if not tickers:
            return {}
        placeholders = ",".join("?" for _ in tickers)
        params = [t.strip().upper() for t in tickers] + [
            composite.strip().lower(),
            as_of_date,
            composite.strip().lower(),
        ]
        rows = self.conn.execute(
            f"""
            SELECT rs.ticker, rs.rank_in_universe
            FROM company_rank_snapshots rs
            JOIN (
                SELECT ticker, MAX(snapshot_date) AS snapshot_date
                FROM company_rank_snapshots
                WHERE ticker IN ({placeholders})
                  AND composite = ?
                  AND snapshot_date <= ?
                GROUP BY ticker
            ) prior ON prior.ticker = rs.ticker AND prior.snapshot_date = rs.snapshot_date
            WHERE rs.composite = ?
            """,
            params,
        ).fetchall()
        output: dict[str, int] = {}
        for row in rows:
            rank = row["rank_in_universe"]
            if rank is not None:
                output[row["ticker"]] = int(rank)
        return output

    def fetch_distinct_rank_snapshot_dates(
        self,
        *,
        composite: str,
        limit: int = 12,
    ) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT snapshot_date
            FROM company_rank_snapshots
            WHERE composite = ?
            ORDER BY snapshot_date DESC
            LIMIT ?
            """,
            (composite.strip().lower(), max(1, int(limit))),
        ).fetchall()
        return [row["snapshot_date"] for row in reversed(rows)]

    def fetch_rank_snapshot_rows(
        self,
        *,
        composite: str,
        snapshot_date: str,
    ) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT ticker, composite_score, rank_in_universe
            FROM company_rank_snapshots
            WHERE composite = ? AND snapshot_date = ?
            ORDER BY rank_in_universe ASC
            """,
            (composite.strip().lower(), snapshot_date),
        ).fetchall()
        return [dict(row) for row in rows]

    def fetch_company_rank_history(
        self,
        ticker: str,
        *,
        composite: str,
        limit: int = 90,
    ) -> list[dict]:
        import json

        rows = self.conn.execute(
            """
            SELECT snapshot_date, composite_score, rank_in_universe, factor_json
            FROM company_rank_snapshots
            WHERE ticker = ? AND composite = ?
            ORDER BY snapshot_date DESC
            LIMIT ?
            """,
            (ticker.strip().upper(), composite.strip().lower(), max(1, int(limit))),
        ).fetchall()
        history = []
        for row in rows:
            item = dict(row)
            raw_factors = item.pop("factor_json", None)
            if raw_factors:
                try:
                    item["factors"] = json.loads(raw_factors)
                except (TypeError, ValueError):
                    item["factors"] = None
            else:
                item["factors"] = None
            history.append(item)
        return list(reversed(history))

    def upsert_research_queue_items(self, records: Iterable[dict]) -> int:
        import json

        rows = []
        for record in records:
            details = record.get("details")
            rows.append(
                (
                    str(record["ticker"]).strip().upper(),
                    str(record["event_type"]).strip().lower(),
                    record["event_date"],
                    int(record.get("priority", 50)),
                    json.dumps(details) if details is not None else None,
                    record.get("expires_at"),
                )
            )
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO research_queue (
                ticker, event_type, event_date, priority, details_json, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, event_type, event_date) DO UPDATE SET
                priority=excluded.priority,
                details_json=excluded.details_json,
                expires_at=excluded.expires_at,
                dismissed=0
            """,
            rows,
        )
        self.commit()
        return len(rows)

    def fetch_research_queue(
        self,
        *,
        limit: int = 50,
        event_types: list[str] | None = None,
        dismissed: bool = False,
    ) -> list[dict]:
        import json

        clauses = ["dismissed = ?"]
        params: list[object] = [1 if dismissed else 0]
        if event_types:
            normalized = [item.strip().lower() for item in event_types if item and str(item).strip()]
            if normalized:
                placeholders = ",".join("?" for _ in normalized)
                clauses.append(f"event_type IN ({placeholders})")
                params.extend(normalized)
        clauses.append(
            "(expires_at IS NULL OR expires_at = '' OR expires_at > datetime('now'))"
        )
        where_sql = " AND ".join(clauses)
        rows = self.conn.execute(
            f"""
            SELECT
                id,
                ticker,
                event_type,
                event_date,
                priority,
                details_json,
                created_at,
                expires_at,
                dismissed
            FROM research_queue
            WHERE {where_sql}
            ORDER BY priority ASC, created_at DESC
            LIMIT ?
            """,
            [*params, max(1, int(limit))],
        ).fetchall()
        items = []
        for row in rows:
            item = {
                "id": row["id"],
                "ticker": row["ticker"],
                "eventType": row["event_type"],
                "eventDate": row["event_date"],
                "priority": row["priority"],
                "createdAt": row["created_at"],
                "expiresAt": row["expires_at"],
                "dismissed": bool(row["dismissed"]),
            }
            raw_details = row["details_json"]
            if raw_details:
                try:
                    item["details"] = json.loads(raw_details)
                except (TypeError, ValueError):
                    item["details"] = None
            else:
                item["details"] = None
            items.append(item)
        return items

    def dismiss_research_queue_items(
        self,
        ticker: str,
        *,
        event_type: str | None = None,
        event_date: str | None = None,
    ) -> int:
        symbol = (ticker or "").strip().upper()
        if not symbol:
            return 0
        clauses = ["ticker = ?", "dismissed = 0"]
        params: list[object] = [symbol]
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type.strip().lower())
        if event_date:
            clauses.append("event_date = ?")
            params.append(event_date)
        cursor = self.conn.execute(
            f"""
            UPDATE research_queue
            SET dismissed = 1
            WHERE {" AND ".join(clauses)}
            """,
            params,
        )
        self.commit()
        return cursor.rowcount

    def fetch_latest_thesis_snapshot(self, company_id: int) -> dict | None:
        row = self.conn.execute(
            """
            SELECT
                id,
                company_id,
                ticker,
                snapshot_date,
                thesis_version,
                pillar_version,
                scoring_version,
                gates_json,
                pillars_json,
                thesis_json,
                disqualified,
                composite_score,
                computed_at
            FROM company_thesis_snapshots
            WHERE company_id = ?
            ORDER BY computed_at DESC, snapshot_date DESC
            LIMIT 1
            """,
            (company_id,),
        ).fetchone()
        return dict(row) if row else None

    def fetch_thesis_drift_history(
        self,
        ticker: str,
        *,
        composite: str,
        limit: int = 90,
    ) -> list[dict]:
        import json

        rows = self.conn.execute(
            """
            SELECT
                ts.snapshot_date,
                ts.composite_score,
                ts.disqualified,
                ts.gates_json,
                ts.pillars_json,
                rs.rank_in_universe,
                rs.composite_score AS rank_composite_score
            FROM company_thesis_snapshots ts
            LEFT JOIN company_rank_snapshots rs
              ON rs.ticker = ts.ticker
             AND rs.snapshot_date = ts.snapshot_date
             AND rs.composite = ?
            WHERE ts.ticker = ?
            ORDER BY ts.snapshot_date DESC
            LIMIT ?
            """,
            (
                composite.strip().lower(),
                ticker.strip().upper(),
                max(1, int(limit)),
            ),
        ).fetchall()

        history: list[dict] = []
        for row in rows:
            item = dict(row)
            composite_score = item.get("composite_score")
            if composite_score is None:
                composite_score = item.pop("rank_composite_score", None)
            else:
                item.pop("rank_composite_score", None)

            gates: dict[str, str] = {}
            raw_gates = item.pop("gates_json", None)
            if raw_gates:
                try:
                    parsed = json.loads(raw_gates) if isinstance(raw_gates, str) else raw_gates
                    if isinstance(parsed, list):
                        for gate in parsed:
                            if isinstance(gate, dict) and gate.get("gate"):
                                gates[str(gate["gate"])] = str(gate.get("status") or "unknown")
                    elif isinstance(parsed, dict):
                        for key, value in parsed.items():
                            if isinstance(value, dict):
                                gates[str(key)] = str(value.get("status") or "unknown")
                            else:
                                gates[str(key)] = str(value)
                except (TypeError, ValueError):
                    gates = {}

            pillar_scores: dict[str, float | None] = {}
            raw_pillars = item.pop("pillars_json", None)
            if raw_pillars:
                try:
                    parsed = json.loads(raw_pillars) if isinstance(raw_pillars, str) else raw_pillars
                    if isinstance(parsed, list):
                        for pillar in parsed:
                            if isinstance(pillar, dict) and pillar.get("pillar") is not None:
                                score = pillar.get("score")
                                pillar_scores[str(pillar["pillar"])] = (
                                    float(score) if score is not None else None
                                )
                    elif isinstance(parsed, dict):
                        for key, value in parsed.items():
                            if isinstance(value, dict):
                                score = value.get("score")
                                pillar_scores[str(key)] = float(score) if score is not None else None
                            elif value is not None:
                                pillar_scores[str(key)] = float(value)
                except (TypeError, ValueError):
                    pillar_scores = {}

            history.append({
                "snapshot_date": item.get("snapshot_date"),
                "composite_score": composite_score,
                "rank_in_universe": item.get("rank_in_universe"),
                "disqualified": bool(item.get("disqualified")),
                "gates": gates,
                "pillar_scores": pillar_scores,
            })

        return list(reversed(history))

    def upsert_thesis_snapshot(self, record: dict) -> int:
        import json

        gates_json = record.get("gates_json")
        if gates_json is not None and not isinstance(gates_json, str):
            gates_json = json.dumps(gates_json)
        pillars_json = record.get("pillars_json")
        if pillars_json is not None and not isinstance(pillars_json, str):
            pillars_json = json.dumps(pillars_json)
        thesis_json = record.get("thesis_json")
        if thesis_json is not None and not isinstance(thesis_json, str):
            thesis_json = json.dumps(thesis_json)

        self.conn.execute(
            """
            INSERT INTO company_thesis_snapshots (
                company_id,
                ticker,
                snapshot_date,
                thesis_version,
                pillar_version,
                scoring_version,
                gates_json,
                pillars_json,
                thesis_json,
                disqualified,
                composite_score,
                computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))
            ON CONFLICT(company_id, snapshot_date, thesis_version) DO UPDATE SET
                ticker=excluded.ticker,
                pillar_version=excluded.pillar_version,
                scoring_version=excluded.scoring_version,
                gates_json=excluded.gates_json,
                pillars_json=excluded.pillars_json,
                thesis_json=excluded.thesis_json,
                disqualified=excluded.disqualified,
                composite_score=excluded.composite_score,
                computed_at=excluded.computed_at
            """,
            (
                record["company_id"],
                str(record["ticker"]).strip().upper(),
                record["snapshot_date"],
                int(record.get("thesis_version", 1)),
                int(record.get("pillar_version", 1)),
                int(record.get("scoring_version", 1)),
                gates_json,
                pillars_json,
                thesis_json,
                1 if record.get("disqualified") else 0,
                record.get("composite_score"),
                record.get("computed_at"),
            ),
        )
        self.commit()
        return 1

    def upsert_company_narrative_snapshots(self, records: Iterable[dict]) -> int:
        import json

        rows = []
        for record in records:
            rows.append(
                (
                    str(record["ticker"]).strip().upper(),
                    record["snapshot_date"],
                    json.dumps(record.get("states") or []),
                    record.get("divergence_score"),
                    record.get("divergence_signal"),
                    json.dumps(record.get("emerging_situations") or []),
                )
            )
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO company_narrative_snapshots (
                ticker, snapshot_date, states_json,
                divergence_score, divergence_signal, emerging_situations_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, snapshot_date) DO UPDATE SET
                states_json=excluded.states_json,
                divergence_score=excluded.divergence_score,
                divergence_signal=excluded.divergence_signal,
                emerging_situations_json=excluded.emerging_situations_json,
                computed_at=CURRENT_TIMESTAMP
            """,
            rows,
        )
        self.commit()
        return len(rows)

    def fetch_narrative_snapshot_history(
        self,
        ticker: str,
        *,
        limit: int = 24,
    ) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT snapshot_date, divergence_score, divergence_signal
            FROM company_narrative_snapshots
            WHERE ticker = ?
            ORDER BY snapshot_date DESC
            LIMIT ?
            """,
            (ticker.strip().upper(), max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def fetch_latest_narrative_snapshots(self, tickers: list[str]) -> dict[str, dict]:
        import json

        if not tickers:
            return {}
        placeholders = ",".join("?" for _ in tickers)
        rows = self.conn.execute(
            f"""
            SELECT ns.ticker, ns.snapshot_date, ns.states_json,
                   ns.divergence_score, ns.divergence_signal, ns.emerging_situations_json
            FROM company_narrative_snapshots ns
            JOIN (
                SELECT ticker, MAX(snapshot_date) AS snapshot_date
                FROM company_narrative_snapshots
                WHERE ticker IN ({placeholders})
                GROUP BY ticker
            ) latest ON latest.ticker = ns.ticker AND latest.snapshot_date = ns.snapshot_date
            """,
            [t.upper() for t in tickers],
        ).fetchall()
        output: dict[str, dict] = {}
        for row in rows:
            item = dict(row)
            for key in ("states_json", "emerging_situations_json"):
                raw = item.pop(key, None)
                parsed_key = "states" if key == "states_json" else "emergingSituations"
                if raw:
                    try:
                        item[parsed_key] = json.loads(raw)
                    except (TypeError, ValueError):
                        item[parsed_key] = []
                else:
                    item[parsed_key] = []
            output[row["ticker"]] = item
        return output

    def fetch_latest_company_scores(
        self,
        tickers: list[str],
        dimension: str = "ARY",
    ) -> dict[str, dict]:
        if not tickers:
            return {}
        placeholders = ",".join("?" for _ in tickers)
        rows = self.conn.execute(
            f"""
            SELECT
                c.ticker,
                cs.period_end,
                cs.dimension,
                cs.piotroski_f,
                cs.altman_z,
                cs.beneish_m,
                cs.survivability,
                cs.survivability_bucket,
                cs.piotroski_components,
                cs.altman_components,
                cs.beneish_components,
                cs.computed_at
            FROM company_scores cs
            JOIN companies c ON c.id = cs.company_id
            WHERE c.ticker IN ({placeholders})
              AND cs.dimension = ?
              AND cs.period_end = (
                  SELECT MAX(cs2.period_end)
                  FROM company_scores cs2
                  WHERE cs2.company_id = cs.company_id AND cs2.dimension = cs.dimension
              )
            """,
            [*[t.upper() for t in tickers], dimension],
        ).fetchall()
        return {row["ticker"]: self._format_score_row(row) for row in rows}

    def fetch_company_scores_on_or_before(
        self,
        ticker: str,
        as_of_date: str,
        *,
        dimension: str = "ARY",
    ) -> dict | None:
        """Latest company_scores row with period_end on or before as_of_date."""
        row = self.conn.execute(
            """
            SELECT
                cs.period_end,
                cs.dimension,
                cs.piotroski_f,
                cs.altman_z,
                cs.beneish_m,
                cs.survivability,
                cs.survivability_bucket,
                cs.piotroski_components,
                cs.altman_components,
                cs.beneish_components,
                cs.computed_at
            FROM company_scores cs
            JOIN companies c ON c.id = cs.company_id
            WHERE c.ticker = ?
              AND cs.dimension = ?
              AND cs.period_end <= ?
            ORDER BY cs.period_end DESC
            LIMIT 1
            """,
            (ticker.strip().upper(), dimension, as_of_date[:10]),
        ).fetchone()
        if not row:
            return None
        formatted = self._format_score_row(row)
        return {
            "piotroski_f": formatted.get("piotroskiF"),
            "altman_z": formatted.get("altmanZ"),
            "beneish_m": formatted.get("beneishM"),
            "survivability": formatted.get("survivability"),
            "survivability_bucket": formatted.get("survivabilityBucket"),
            "period_end": formatted.get("periodEnd"),
        }

    def fetch_fundamentals_wide_on_or_before(
        self,
        ticker: str,
        as_of_date: str,
        *,
        dimension: str = "MRY",
    ) -> dict | None:
        """Pivot fundamentals to a wide row for the latest period_end on or before as_of_date."""
        from .services.fundamentals import (
            collapse_narrow_fundamentals_rows,
            pivot_fundamentals_rows,
        )

        narrow = self.fetch_fundamentals_rows(
            [ticker.strip().upper()],
            dimension=dimension,
        )
        if not narrow:
            return None
        filtered = [row for row in narrow if (row.get("period_end") or "")[:10] <= as_of_date[:10]]
        if not filtered:
            return None
        annual = dimension.upper() in {"MRY", "ARY"}
        collapsed = collapse_narrow_fundamentals_rows(filtered, annual=annual)
        wide = pivot_fundamentals_rows(collapsed, canonical_annual=annual)
        if not wide:
            return None
        wide.sort(key=lambda item: item.get("calendardate") or "", reverse=True)
        return wide[0]

    @staticmethod
    def _format_score_row(row: sqlite3.Row) -> dict:
        import json

        output = {
            "periodEnd": row["period_end"],
            "dimension": row["dimension"],
            "piotroskiF": row["piotroski_f"],
            "altmanZ": row["altman_z"],
            "beneishM": row["beneish_m"],
            "survivability": row["survivability"],
            "survivabilityBucket": row["survivability_bucket"],
            "computedAt": row["computed_at"],
        }
        if row["piotroski_components"]:
            try:
                output["piotroskiComponents"] = json.loads(row["piotroski_components"])
            except json.JSONDecodeError:
                output["piotroskiComponents"] = None
        if row["altman_components"]:
            try:
                output["altmanComponents"] = json.loads(row["altman_components"])
            except json.JSONDecodeError:
                output["altmanComponents"] = None
        if row["beneish_components"]:
            try:
                output["beneishComponents"] = json.loads(row["beneish_components"])
            except json.JSONDecodeError:
                output["beneishComponents"] = None
        return output

    def fetch_price_history(self, ticker: str, *, through_date: str | None = None) -> list[dict]:
        """All price rows for a ticker up to through_date (inclusive)."""
        sql = """
            SELECT ticker, date, open, high, low, close, volume, source
            FROM prices
            WHERE ticker = ?
        """
        params: list = [ticker.upper()]
        if through_date:
            sql += " AND date <= ?"
            params.append(through_date[:10])
        sql += " ORDER BY date ASC"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def fetch_prices_by_period_ends(self, ticker: str, period_ends: list[str]) -> dict[str, float | None]:
        """Bulk lookup: one price-history query, then in-memory period mapping."""
        from .services.price_lookup import map_prices_by_period_end

        normalized = sorted({period[:10] for period in period_ends if period})
        if not normalized:
            return {}
        history = self.fetch_price_history(ticker, through_date=normalized[-1])
        return map_prices_by_period_end(normalized, history)

    def fetch_price_near_date(self, ticker: str, target_date: str) -> float | None:
        mapped = self.fetch_prices_by_period_ends(ticker, [target_date])
        return mapped.get(target_date[:10])

    def fetch_insider_summary_90d(self, tickers: list[str]) -> list[dict]:
        if not tickers:
            return []
        cutoff = (datetime.now(timezone.utc).date() - timedelta(days=90)).isoformat()
        placeholders = ",".join("?" for _ in tickers)
        rows = self.conn.execute(
            f"""
            SELECT
                c.ticker,
                SUM(CASE WHEN i.transaction_code = 'P' THEN 1 ELSE 0 END) AS buy_count_90d,
                SUM(CASE WHEN i.transaction_code = 'S' THEN 1 ELSE 0 END) AS sell_count_90d,
                SUM(CASE WHEN i.transaction_code = 'P' THEN ABS(COALESCE(i.transaction_value, 0)) ELSE 0 END) AS buy_value_90d,
                SUM(CASE WHEN i.transaction_code = 'S' THEN ABS(COALESCE(i.transaction_value, 0)) ELSE 0 END) AS sell_value_90d
            FROM insider_transactions i
            JOIN companies c ON c.id = i.company_id
            WHERE c.ticker IN ({placeholders})
              AND i.transaction_code IN ('P', 'S')
              AND i.transaction_date >= ?
            GROUP BY c.ticker
            """,
            [*[t.upper() for t in tickers], cutoff],
        ).fetchall()
        results = []
        for row in rows:
            buy_count = row["buy_count_90d"] or 0
            sell_count = row["sell_count_90d"] or 0
            buy_value = row["buy_value_90d"] or 0.0
            sell_value = row["sell_value_90d"] or 0.0
            ratio = buy_count / sell_count if sell_count else (float(buy_count) if buy_count else None)
            results.append(
                {
                    "ticker": row["ticker"],
                    "buyCount90d": buy_count,
                    "sellCount90d": sell_count,
                    "buySellRatio": ratio,
                    "totalBuyValue90d": buy_value,
                    "totalSellValue90d": sell_value,
                }
            )
        return results

    def fetch_insider_transactions_raw(self, company_id: int, limit: int = 2000) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT
                filing_date,
                transaction_date,
                owner_name,
                transaction_code,
                shares,
                price_per_share,
                transaction_value,
                security_title
            FROM insider_transactions
            WHERE company_id = ?
            ORDER BY transaction_date DESC, filing_date DESC
            LIMIT ?
            """,
            (company_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def fetch_insider_transactions_raw_batch(
        self,
        company_ids: list[int],
        *,
        limit_per_company: int = 500,
    ) -> dict[int, list[dict]]:
        if not company_ids:
            return {}
        unique_ids = sorted({int(company_id) for company_id in company_ids})
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self.conn.execute(
            f"""
            SELECT company_id, filing_date, transaction_date, owner_name,
                   transaction_code, shares, price_per_share, transaction_value,
                   security_title
            FROM (
                SELECT
                    company_id,
                    filing_date,
                    transaction_date,
                    owner_name,
                    transaction_code,
                    shares,
                    price_per_share,
                    transaction_value,
                    security_title,
                    ROW_NUMBER() OVER (
                        PARTITION BY company_id
                        ORDER BY transaction_date DESC, filing_date DESC
                    ) AS row_rank
                FROM insider_transactions
                WHERE company_id IN ({placeholders})
            )
            WHERE row_rank <= ?
            ORDER BY company_id, transaction_date DESC, filing_date DESC
            """,
            [*unique_ids, max(1, int(limit_per_company))],
        ).fetchall()
        output: dict[int, list[dict]] = {company_id: [] for company_id in unique_ids}
        for row in rows:
            item = dict(row)
            company_id = int(item.pop("company_id"))
            output.setdefault(company_id, []).append(item)
        return output

    def upsert_insider_cluster_analysis(self, company_id: int, records: Iterable[dict]) -> int:
        rows = [
            (
                company_id,
                record["window_start"],
                record["window_end"],
                record.get("buy_count", 0),
                record.get("sell_count", 0),
                record.get("unique_buyers", 0),
                record.get("total_buy_value"),
                record.get("total_sell_value"),
                record.get("avg_buy_price"),
                record.get("intensity_score"),
            )
            for record in records
        ]
        # Replace all materialized windows for this company (avoids stale rows after rule changes).
        self.conn.execute(
            "DELETE FROM insider_cluster_analysis WHERE company_id = ?",
            (company_id,),
        )
        if not rows:
            self.commit()
            return 0
        self.conn.executemany(
            """
            INSERT INTO insider_cluster_analysis (
                company_id, window_start, window_end,
                buy_count, sell_count, unique_buyers,
                total_buy_value, total_sell_value, avg_buy_price, intensity_score,
                computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(company_id, window_start, window_end) DO UPDATE SET
                buy_count=excluded.buy_count,
                sell_count=excluded.sell_count,
                unique_buyers=excluded.unique_buyers,
                total_buy_value=excluded.total_buy_value,
                total_sell_value=excluded.total_sell_value,
                avg_buy_price=excluded.avg_buy_price,
                intensity_score=excluded.intensity_score,
                computed_at=CURRENT_TIMESTAMP
            """,
            rows,
        )
        self.commit()
        return len(rows)

    def fetch_insider_clusters_for_company(self, company_id: int, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT
                window_start,
                window_end,
                buy_count,
                sell_count,
                unique_buyers,
                total_buy_value,
                total_sell_value,
                avg_buy_price,
                intensity_score,
                computed_at
            FROM insider_cluster_analysis
            WHERE company_id = ?
              AND COALESCE(total_buy_value, 0) > 0
            ORDER BY intensity_score DESC, window_start DESC
            LIMIT ?
            """,
            (company_id, limit),
        ).fetchall()
        return [self._format_cluster_row(row) for row in rows]

    def fetch_insider_cluster_rankings(
        self,
        tickers: list[str] | None = None,
        *,
        limit: int = 50,
        min_unique_buyers: int = 3,
        min_buy_value: float | None = None,
    ) -> list[dict]:
        params: list = [min_unique_buyers]
        ticker_filter = ""
        value_filter = ""
        if tickers:
            placeholders = ",".join("?" for _ in tickers)
            ticker_filter = f" AND c.ticker IN ({placeholders})"
            params.extend([t.upper() for t in tickers])
        if min_buy_value is not None and min_buy_value > 0:
            value_filter = " AND COALESCE(ica.total_buy_value, 0) >= ?"
            params.append(min_buy_value)
        params.append(limit)
        rows = self.conn.execute(
            f"""
            SELECT
                c.ticker,
                c.name AS company_name,
                ica.window_start,
                ica.window_end,
                ica.buy_count,
                ica.sell_count,
                ica.unique_buyers,
                ica.total_buy_value,
                ica.total_sell_value,
                ica.avg_buy_price,
                ica.intensity_score,
                ica.computed_at
            FROM insider_cluster_analysis ica
            JOIN companies c ON c.id = ica.company_id
            WHERE ica.unique_buyers >= ?
              AND ica.intensity_score IS NOT NULL
              AND COALESCE(ica.total_buy_value, 0) > 0
              {ticker_filter}
              {value_filter}
            ORDER BY ica.intensity_score DESC, ica.total_buy_value DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        output = []
        for row in rows:
            cluster = self._format_cluster_row(row)
            cluster["ticker"] = row["ticker"]
            cluster["companyName"] = row["company_name"]
            output.append(cluster)
        return output

    def fetch_tickers_with_fundamentals(self, tickers: list[str]) -> set[str]:
        if not tickers:
            return set()
        placeholders = ",".join("?" for _ in tickers)
        rows = self.conn.execute(
            f"""
            SELECT DISTINCT c.ticker
            FROM companies c
            JOIN company_scores cs ON cs.company_id = c.id
            WHERE c.ticker IN ({placeholders})
            """,
            [t.upper() for t in tickers],
        ).fetchall()
        return {row["ticker"].upper() for row in rows if row["ticker"]}

    def fetch_portfolio_tickers(self) -> set[str]:
        portfolio = self.get_watchlist("Portfolio")
        if not portfolio:
            return set()
        return {t.upper() for t in (portfolio.get("tickers") or []) if t}

    def fetch_abnormal_returns_for_articles(self, article_ids: list[int]) -> dict[int, float]:
        if not article_ids:
            return {}
        placeholders = ",".join("?" for _ in article_ids)
        rows = self.conn.execute(
            f"""
            SELECT article_id, abnormal_return_1d
            FROM article_market_reactions
            WHERE article_id IN ({placeholders})
              AND abnormal_return_1d IS NOT NULL
            """,
            article_ids,
        ).fetchall()
        return {
            int(row["article_id"]): float(row["abnormal_return_1d"])
            for row in rows
            if row["article_id"] is not None and row["abnormal_return_1d"] is not None
        }

    def fetch_recent_edgar_signals(
        self,
        *,
        limit: int = 100,
        max_age_days: int = 30,
    ) -> list[dict]:
        cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
        rows = self.conn.execute(
            """
            SELECT
                c.ticker,
                c.name AS company_name,
                e.event_type AS signal_type,
                e.filed_date AS event_date,
                e.form_type,
                e.item_number,
                e.summary,
                e.accession,
                'edgar_event' AS source
            FROM company_edgar_events e
            JOIN companies c ON c.id = e.company_id
            WHERE e.filed_date >= ?
            UNION ALL
            SELECT
                c.ticker,
                c.name AS company_name,
                'going_concern_8k' AS signal_type,
                f.filed_date AS event_date,
                '10-K' AS form_type,
                NULL AS item_number,
                COALESCE(f.details, 'going concern flag') AS summary,
                f.accession,
                'edgar_flag' AS source
            FROM company_edgar_flags f
            JOIN companies c ON c.id = f.company_id
            WHERE f.flag_type = 'going_concern'
              AND f.active = 1
              AND COALESCE(f.filed_date, '') >= ?
            UNION ALL
            SELECT
                c.ticker,
                c.name AS company_name,
                'activist_13d' AS signal_type,
                a.filed_date AS event_date,
                a.form_type,
                NULL AS item_number,
                COALESCE(a.summary, a.filer_name) AS summary,
                a.accession,
                'activist_filing' AS source
            FROM company_activist_filings a
            JOIN companies c ON c.id = a.company_id
            WHERE a.filed_date >= ?
            ORDER BY event_date DESC
            LIMIT ?
            """,
            (cutoff, cutoff, cutoff, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _format_cluster_row(row: sqlite3.Row) -> dict:
        return {
            "windowStart": row["window_start"],
            "windowEnd": row["window_end"],
            "buyCount": row["buy_count"],
            "sellCount": row["sell_count"],
            "uniqueBuyers": row["unique_buyers"],
            "totalBuyValue": row["total_buy_value"],
            "totalSellValue": row["total_sell_value"],
            "avgBuyPrice": row["avg_buy_price"],
            "intensityScore": row["intensity_score"],
            "computedAt": row["computed_at"],
            "isCluster": (row["unique_buyers"] or 0) >= 3,
        }

    def upsert_feed(self, feed: dict) -> int:
        import json

        pack_tags = feed.get("pack_tags")
        if pack_tags is not None and not isinstance(pack_tags, str):
            pack_tags = json.dumps(pack_tags)
        cursor = self.conn.execute(
            """
            INSERT INTO feeds (
                name, feed_url, domain, category, is_active, etag, last_modified,
                last_polled_at, source_weight, enabled_by_default, pack_tags
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(feed_url) DO UPDATE SET
                name=excluded.name,
                domain=excluded.domain,
                category=excluded.category,
                is_active=excluded.is_active,
                etag=COALESCE(excluded.etag, feeds.etag),
                last_modified=COALESCE(excluded.last_modified, feeds.last_modified),
                last_polled_at=excluded.last_polled_at,
                source_weight=COALESCE(excluded.source_weight, feeds.source_weight),
                enabled_by_default=COALESCE(excluded.enabled_by_default, feeds.enabled_by_default),
                pack_tags=COALESCE(excluded.pack_tags, feeds.pack_tags),
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
                float(feed.get("source_weight", 0.55)),
                int(bool(feed.get("enabled_by_default", True))),
                pack_tags,
            ),
        )
        feed_id = cursor.fetchone()[0]
        self.commit()
        return feed_id

    def get_feed_source_weight(self, raw_source: str | None) -> float | None:
        if not raw_source or not raw_source.startswith("feed:"):
            return None
        feed_id = raw_source.split(":", 1)[1]
        if not feed_id.isdigit():
            return None
        row = self.conn.execute(
            "SELECT source_weight FROM feeds WHERE id = ?",
            (int(feed_id),),
        ).fetchone()
        if row is None or row["source_weight"] is None:
            return None
        return float(row["source_weight"])

    def get_enabled_feed_packs(self) -> list[str]:
        import json

        raw = self.get_config("enabled_feed_packs")
        if raw is None:
            return []
        if isinstance(raw, list):
            return [str(item) for item in raw]
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except json.JSONDecodeError:
                return []
        return []

    def set_enabled_feed_packs(self, packs: list[str]) -> list[str]:
        import json

        normalized = sorted({pack.strip().lower() for pack in packs if pack and pack.strip()})
        self.set_config("enabled_feed_packs", normalized)
        return normalized

    def get_inactive_feed_urls(self) -> set[str]:
        rows = self.conn.execute(
            "SELECT feed_url FROM feeds WHERE is_active = 0"
        ).fetchall()
        return {row["feed_url"] for row in rows}

    def list_feed_packs(self) -> dict:
        import json

        from .services.news import DEFAULT_FEEDS, FEED_PACKS

        enabled = set(self.get_enabled_feed_packs())
        pack_feeds: dict[str, list[dict]] = {pack: [] for pack in FEED_PACKS}
        for feed in DEFAULT_FEEDS:
            for pack in feed.get("pack_tags") or []:
                if pack in pack_feeds:
                    pack_feeds[pack].append(
                        {
                            "name": feed["name"],
                            "feedUrl": feed["feed_url"],
                            "enabledByDefault": bool(feed.get("enabled_by_default", True)),
                            "sourceWeight": feed.get("source_weight", 0.55),
                        }
                    )
        return {
            "packs": [
                {
                    "id": pack,
                    "enabled": pack in enabled,
                    "feedCount": len(pack_feeds.get(pack, [])),
                    "feeds": pack_feeds.get(pack, []),
                }
                for pack in FEED_PACKS
            ],
            "enabledPacks": sorted(enabled),
        }

    def record_feed_poll(self, feed_id: int, *, success: bool, error_message: str | None = None) -> None:
        now = utc_now_iso()
        if success:
            self.conn.execute(
                """
                UPDATE feeds
                SET last_success_at = ?,
                    last_error_at = NULL,
                    last_error_message = NULL,
                    consecutive_failures = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (now, feed_id),
            )
        else:
            self.conn.execute(
                """
                UPDATE feeds
                SET last_error_at = ?,
                    last_error_message = ?,
                    consecutive_failures = COALESCE(consecutive_failures, 0) + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (now, (error_message or "unknown error")[:500], feed_id),
            )
        self.commit()

    def list_job_runs(self, *, limit: int = 25) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT
                jr.id,
                jr.job_id,
                j.job_type,
                jr.started_at,
                jr.finished_at,
                jr.status,
                jr.error_message
            FROM job_runs jr
            JOIN ingestion_jobs j ON j.id = jr.job_id
            ORDER BY jr.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_article_ticker_matches(self, article_ids: list[int]) -> dict[int, list[dict]]:
        if not article_ids:
            return {}
        display_clause, display_params = _news_display_match_clause("ac")
        placeholders = ",".join("?" for _ in article_ids)
        rows = self.conn.execute(
            f"""
            SELECT
                ac.article_id,
                c.ticker,
                ac.match_strategy,
                ac.confidence,
                ac.extraction_stage
            FROM article_company ac
            JOIN companies c ON c.id = ac.company_id
            WHERE ac.article_id IN ({placeholders})
              AND ac.confidence >= 0.85
              AND {display_clause}
            ORDER BY ac.article_id, ac.confidence DESC, c.ticker
            """,
            [*article_ids, *display_params],
        ).fetchall()
        matches: dict[int, list[dict]] = {}
        for row in rows:
            matches.setdefault(row["article_id"], []).append(
                {
                    "ticker": row["ticker"],
                    "matchStrategy": row["match_strategy"],
                    "confidence": round(float(row["confidence"]), 4),
                    "extractionStage": row["extraction_stage"],
                }
            )
        return matches

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

    def find_duplicate_article(
        self,
        title: str,
        summary: str | None = None,
        *,
        threshold: int = 88,
        lookback: int = 500,
    ) -> int | None:
        from .services.article_dedup import find_semantic_duplicate

        rows = self.conn.execute(
            """
            SELECT id, title, summary
            FROM articles
            WHERE duplicate_of_article_id IS NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            (lookback,),
        ).fetchall()
        return find_semantic_duplicate(
            title,
            summary,
            [dict(row) for row in rows],
            threshold=threshold,
        )

    def normalize_published_dates(self) -> int:
        from .services.article_dedup import normalize_published_at

        rows = self.conn.execute(
            "SELECT id, published_at FROM articles WHERE published_at IS NOT NULL",
        ).fetchall()
        updated = 0
        for row in rows:
            normalized = normalize_published_at(row["published_at"])
            if normalized and normalized != row["published_at"]:
                self.conn.execute(
                    "UPDATE articles SET published_at = ? WHERE id = ?",
                    (normalized, row["id"]),
                )
                updated += 1
        self.commit()
        return updated

    def deduplicate_articles(self, lookback: int = 2000) -> dict:
        from .services.article_dedup import dedup_fingerprint, find_semantic_duplicate_indexed

        exact_dupes = self.conn.execute(
            """
            UPDATE articles
            SET duplicate_of_article_id = (
                SELECT a2.id FROM articles a2
                WHERE a2.content_hash = articles.content_hash
                  AND a2.id < articles.id
                  AND a2.duplicate_of_article_id IS NULL
                ORDER BY a2.id ASC LIMIT 1
            )
            WHERE duplicate_of_article_id IS NULL
              AND content_hash IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM articles a2
                  WHERE a2.content_hash = articles.content_hash
                    AND a2.id < articles.id
                    AND a2.duplicate_of_article_id IS NULL
              )
            """
        ).rowcount

        simhash_dupes = self.conn.execute(
            """
            UPDATE articles
            SET duplicate_of_article_id = (
                SELECT a2.id FROM articles a2
                WHERE a2.simhash_fingerprint = articles.simhash_fingerprint
                  AND a2.id < articles.id
                  AND a2.duplicate_of_article_id IS NULL
                ORDER BY a2.id ASC LIMIT 1
            )
            WHERE duplicate_of_article_id IS NULL
              AND simhash_fingerprint IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM articles a2
                  WHERE a2.simhash_fingerprint = articles.simhash_fingerprint
                    AND a2.id < articles.id
                    AND a2.duplicate_of_article_id IS NULL
              )
            """
        ).rowcount

        rows = self.conn.execute(
            """
            SELECT id, title, summary
            FROM (
                SELECT id, title, summary
                FROM articles
                WHERE duplicate_of_article_id IS NULL
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
            """,
            (lookback,),
        ).fetchall()
        canonical_ids: list[int] = []
        canonical_fps: list[str] = []
        marked = 0
        for row in rows:
            article = dict(row)
            fingerprint = dedup_fingerprint(article["title"], article.get("summary"))
            duplicate_id = find_semantic_duplicate_indexed(
                fingerprint,
                canonical_fps,
                canonical_ids,
            )
            if duplicate_id is not None:
                self.conn.execute(
                    "UPDATE articles SET duplicate_of_article_id = ? WHERE id = ?",
                    (duplicate_id, article["id"]),
                )
                marked += 1
            else:
                canonical_ids.append(article["id"])
                canonical_fps.append(fingerprint)
        self.commit()
        return {
            "scanned": len(rows),
            "exactDuplicates": exact_dupes,
            "simhashDuplicates": simhash_dupes,
            "fuzzyDuplicates": marked,
            "markedDuplicates": exact_dupes + simhash_dupes + marked,
            "uniqueRemaining": len(canonical_ids),
        }

    def upsert_article(self, article: dict, *, skip_dedup: bool = False, defer_commit: bool = False) -> int:
        from .services.article_dedup import compute_simhash

        fingerprint = article.get("simhash_fingerprint") or compute_simhash(
            article["title"], article.get("summary")
        )
        duplicate_id = article.get("duplicate_of_article_id")
        if duplicate_id is None and not skip_dedup:
            existing_row = self.conn.execute(
                "SELECT id FROM articles WHERE url_hash = ?",
                (article["url_hash"],),
            ).fetchone()
            existing_id = existing_row["id"] if existing_row else None
            match = self.conn.execute(
                """
                SELECT id FROM articles
                WHERE simhash_fingerprint = ? AND duplicate_of_article_id IS NULL
                LIMIT 1
                """,
                (fingerprint,),
            ).fetchone()
            if match and match["id"] != existing_id:
                duplicate_id = match["id"]
            else:
                found = self.find_duplicate_article(
                    article["title"],
                    article.get("summary"),
                )
                if found and found != existing_id:
                    duplicate_id = found
        cursor = self.conn.execute(
            """
            INSERT INTO articles (
                canonical_url, url_hash, title, summary, body_text, source_domain,
                published_at, fetched_at, content_hash, language, duplicate_of_article_id,
                sentiment_label, sentiment_score, topic_cluster_id, raw_source,
                simhash_fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                simhash_fingerprint=COALESCE(excluded.simhash_fingerprint, articles.simhash_fingerprint),
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
                fingerprint,
            ),
        )
        article_id = cursor.fetchone()[0]
        if not defer_commit:
            self.commit()
        return article_id

    def link_article_company(self, article_id: int, company_id: int, match_type: str, confidence: float, *, defer_commit: bool = False) -> None:
        self.link_entity_match(
            article_id,
            {
                "company_id": company_id,
                "match_type": match_type,
                "match_strategy": match_type,
                "confidence": confidence,
                "extraction_stage": "ingest",
            },
            defer_commit=defer_commit,
        )

    def link_entity_match(self, article_id: int, match: dict, *, defer_commit: bool = False) -> None:
        now = utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO article_company (
                article_id, company_id, match_type, confidence,
                match_strategy, extraction_stage, evidence_text, embedding_similarity, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id, company_id) DO UPDATE SET
                match_type=excluded.match_type,
                confidence=excluded.confidence,
                match_strategy=excluded.match_strategy,
                extraction_stage=excluded.extraction_stage,
                evidence_text=excluded.evidence_text,
                embedding_similarity=excluded.embedding_similarity,
                updated_at=excluded.updated_at
            WHERE excluded.confidence >= article_company.confidence
            """,
            (
                article_id,
                match["company_id"],
                match.get("match_type") or match.get("match_strategy") or "entity",
                match["confidence"],
                match.get("match_strategy") or match.get("match_type") or "entity",
                match.get("extraction_stage") or "ingest",
                match.get("evidence_text"),
                match.get("embedding_similarity"),
                now,
            ),
        )
        if not defer_commit:
            self.commit()

    def save_entity_matches(
        self,
        article_id: int,
        matches: list,
        *,
        merge: bool = False,
        defer_commit: bool = False,
    ) -> int:
        from .services.entity_linking import EntityMatch

        saved = 0
        company_ids = set()
        for item in matches:
            if isinstance(item, EntityMatch):
                payload = {
                    "company_id": item.company_id,
                    "match_type": item.match_strategy,
                    "match_strategy": item.match_strategy,
                    "confidence": item.confidence,
                    "extraction_stage": item.extraction_stage,
                    "evidence_text": item.evidence_text,
                    "embedding_similarity": item.embedding_similarity,
                }
            else:
                payload = item
            company_ids.add(payload["company_id"])
            self.link_entity_match(article_id, payload, defer_commit=True)
            saved += 1

        if merge:
            self.conn.execute(
                """
                DELETE FROM article_company
                WHERE article_id = ? AND extraction_stage = 'ingest'
                """,
                (article_id,),
            )
            if company_ids:
                placeholders = ",".join("?" for _ in company_ids)
                self.conn.execute(
                    f"""
                    DELETE FROM article_company
                    WHERE article_id = ?
                      AND extraction_stage = 'enrichment'
                      AND company_id NOT IN ({placeholders})
                    """,
                    (article_id, *company_ids),
                )
            else:
                self.conn.execute(
                    """
                    DELETE FROM article_company
                    WHERE article_id = ? AND extraction_stage = 'enrichment'
                    """,
                    (article_id,),
                )
        if not defer_commit:
            self.commit()
        return saved

    def copy_article_entity_matches(self, source_article_id: int, target_article_id: int, *, defer_commit: bool = False) -> int:
        rows = self.conn.execute(
            """
            SELECT company_id, match_type, confidence, match_strategy, extraction_stage,
                   evidence_text, embedding_similarity
            FROM article_company
            WHERE article_id = ?
            """,
            (source_article_id,),
        ).fetchall()
        for row in rows:
            self.link_entity_match(
                target_article_id,
                {
                    "company_id": row["company_id"],
                    "match_type": row["match_type"],
                    "match_strategy": row["match_strategy"] or row["match_type"],
                    "confidence": row["confidence"],
                    "extraction_stage": row["extraction_stage"] or "ingest",
                    "evidence_text": row["evidence_text"],
                    "embedding_similarity": row["embedding_similarity"],
                },
                defer_commit=True,
            )
        if not defer_commit:
            self.commit()
        return len(rows)

    def upsert_curated_company_aliases(self) -> int:
        from .services.company_aliases import CURATED_ALIASES, normalize_entity_text

        companies_by_ticker = {
            (company.get("ticker") or "").upper(): company
            for company in self.list_companies_for_matching()
        }
        updated = 0
        for ticker, aliases in CURATED_ALIASES.items():
            company = companies_by_ticker.get(ticker.upper())
            if not company:
                continue
            for alias in aliases:
                normalized = normalize_entity_text(alias)
                if len(normalized) < 2:
                    continue
                self.conn.execute(
                    """
                    INSERT INTO company_aliases (company_id, alias, alias_type, normalized_alias)
                    VALUES (?, ?, 'curated', ?)
                    ON CONFLICT(company_id, normalized_alias) DO UPDATE SET
                        alias_type = 'curated',
                        alias = excluded.alias
                    """,
                    (company["id"], alias, normalized),
                )
                updated += 1
        self.commit()
        return updated

    def seed_company_aliases(self) -> int:
        from .services.company_aliases import build_alias_records

        self.upsert_curated_company_aliases()
        existing = self.conn.execute("SELECT COUNT(*) FROM company_aliases").fetchone()[0]
        if existing:
            return existing
        companies = self.list_companies_for_matching()
        records = build_alias_records(companies)
        if not records:
            return 0
        self.conn.executemany(
            """
            INSERT INTO company_aliases (company_id, alias, alias_type, normalized_alias)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(company_id, normalized_alias) DO NOTHING
            """,
            [
                (record["company_id"], record["alias"], record["alias_type"], record["normalized_alias"])
                for record in records
            ],
        )
        self.commit()
        return len(records)

    def get_alias_index(self) -> dict[str, list[dict]]:
        rows = self.conn.execute(
            """
            SELECT ca.company_id, ca.alias, ca.alias_type, ca.normalized_alias, c.ticker
            FROM company_aliases ca
            JOIN companies c ON c.id = ca.company_id
            ORDER BY ca.company_id, ca.alias_type
            """
        ).fetchall()
        index: dict[str, list[dict]] = {}
        for row in rows:
            ticker = (row["ticker"] or "").upper()
            index.setdefault(ticker, []).append(dict(row))
        return index

    def get_boosted_tickers(self) -> set[str]:
        tickers: set[str] = set()
        portfolio = self.get_watchlist("Portfolio")
        if portfolio:
            tickers.update(ticker.upper() for ticker in portfolio.get("tickers") or [])
        rows = self.conn.execute(
            """
            SELECT DISTINCT ticker
            FROM watchlist_tickers
            """
        ).fetchall()
        for row in rows:
            if row["ticker"]:
                tickers.add(row["ticker"].upper())
        return tickers

    def upsert_embedding_metadata(
        self,
        article_id: int,
        *,
        model: str,
        content_hash: str | None = None,
        storage_key: str | None = None,
        vector_dimensions: int | None = None,
        defer_commit: bool = False,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO embeddings_metadata (article_id, model, content_hash, storage_key, vector_dimensions)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(article_id, model) DO UPDATE SET
                content_hash=excluded.content_hash,
                storage_key=excluded.storage_key,
                vector_dimensions=excluded.vector_dimensions
            """,
            (article_id, model, content_hash, storage_key, vector_dimensions),
        )
        if not defer_commit:
            self.commit()

    def list_unique_articles(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        q: str | None = None,
        category: str | None = None,
        source_domain: str | None = None,
        tickers: list[str] | None = None,
        divergence_only: bool = False,
        sort: str = "importance",
    ) -> dict:
        clauses = ["a.duplicate_of_article_id IS NULL"]
        params: list = []
        if q:
            clauses.append(
                "(LOWER(a.title) LIKE ? OR LOWER(COALESCE(a.summary, '')) LIKE ?"
                " OR LOWER(COALESCE(a.body_text, '')) LIKE ?)"
            )
            needle = f"%{q.lower().strip()}%"
            params.extend([needle, needle, needle])
        if source_domain:
            clauses.append("LOWER(a.source_domain) LIKE ?")
            params.append(f"%{source_domain.lower().strip()}%")
        if divergence_only:
            clauses.append("a.divergence_context IS NOT NULL AND TRIM(a.divergence_context) != ''")
        if category:
            clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM feeds f
                    WHERE a.raw_source = 'feed:' || f.id AND LOWER(f.category) = ?
                )
                """
            )
            params.append(category.lower().strip())
        display_clause, display_params = _news_display_match_clause("ac2")
        if tickers:
            ticker_placeholders = ",".join("?" for _ in tickers)
            clauses.append(
                f"""
                EXISTS (
                    SELECT 1
                    FROM article_company ac2
                    JOIN companies c2 ON c2.id = ac2.company_id
                    WHERE ac2.article_id = a.id
                      AND ac2.confidence >= 0.85
                      AND {display_clause}
                      AND UPPER(c2.ticker) IN ({ticker_placeholders})
                )
                """
            )
            params.extend([*display_params, *[ticker.upper() for ticker in tickers]])
        where_sql = " AND ".join(clauses)
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM articles a WHERE {where_sql}",
            params,
        ).fetchone()[0]
        if sort == "latest":
            order_sql = "a.published_at DESC, a.id DESC"
        else:
            order_sql = (
                "COALESCE(a.news_importance_score, a.rank_score) DESC NULLS LAST,"
                " a.published_at DESC, a.id DESC"
            )
        ac_display_clause, ac_display_params = _news_display_match_clause("ac")
        rows = self.conn.execute(
            f"""
            SELECT
                a.id,
                a.title,
                a.summary,
                a.body_text,
                a.canonical_url,
                a.published_at,
                a.source_domain,
                a.sentiment_label,
                a.sentiment_score,
                a.vader_compound,
                a.finbert_label,
                a.rank_score,
                a.news_importance_score,
                a.divergence_context,
                a.event_cluster_id,
                a.topic_cluster_id,
                (
                    SELECT event_type
                    FROM article_event_classifications ec
                    WHERE ec.article_id = a.id
                    ORDER BY ec.confidence DESC
                    LIMIT 1
                ) AS primary_event,
                GROUP_CONCAT(DISTINCT c.ticker) AS tickers
            FROM articles a
            LEFT JOIN article_company ac ON ac.article_id = a.id
                AND ac.confidence >= 0.85
                AND {ac_display_clause}
            LEFT JOIN companies c ON c.id = ac.company_id
            WHERE {where_sql}
            GROUP BY a.id
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            # JOIN display-strategy placeholders precede WHERE params in the SQL text.
            [*ac_display_params, *params, limit, offset],
        ).fetchall()
        article_ids = [row["id"] for row in rows]
        match_index = self.get_article_ticker_matches(article_ids)
        articles = []
        for row in rows:
            ticker_matches = match_index.get(row["id"], [])
            tickers = [match["ticker"] for match in ticker_matches]
            if len(tickers) > 6:
                tickers = tickers[:6]
                ticker_matches = ticker_matches[:6]
            articles.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "description": row["summary"] or row["body_text"],
                    "url": row["canonical_url"],
                    "publishedDate": row["published_at"],
                    "sourceDomain": row["source_domain"],
                    "sentimentLabel": row["sentiment_label"],
                    "sentimentScore": row["sentiment_score"],
                    "vaderCompound": row["vader_compound"],
                    "finbertLabel": row["finbert_label"],
                    "primaryEvent": row["primary_event"],
                    "rankScore": row["rank_score"],
                    "newsImportanceScore": row["news_importance_score"] or row["rank_score"],
                    "divergenceContext": row["divergence_context"],
                    "eventClusterId": row["event_cluster_id"],
                    "topicCluster": row["topic_cluster_id"],
                    "tickers": tickers,
                    "tickerMatches": ticker_matches,
                }
            )
        return {"articles": articles, "total": total, "limit": limit, "offset": offset, "sort": sort}

    def list_news_source_domains(self, *, limit: int = 100) -> list[str]:
        limit = min(max(limit, 1), 500)
        rows = self.conn.execute(
            """
            SELECT source_domain
            FROM articles
            WHERE duplicate_of_article_id IS NULL
              AND source_domain IS NOT NULL
              AND TRIM(source_domain) != ''
            GROUP BY source_domain
            ORDER BY COUNT(*) DESC, source_domain ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [row["source_domain"] for row in rows]

    def get_company_news(self, ticker: str, limit: int = 25) -> list[dict]:
        display_clause, display_params = _news_display_match_clause("ac")
        rows = self.conn.execute(
            f"""
            SELECT a.id, a.title, a.summary, a.body_text, a.canonical_url, a.published_at, a.source_domain
            FROM articles a
            JOIN article_company ac ON ac.article_id = a.id
                AND ac.confidence >= 0.85
                AND {display_clause}
            JOIN companies c ON c.id = ac.company_id
            WHERE c.ticker = ?
              AND a.duplicate_of_article_id IS NULL
            ORDER BY a.published_at DESC, a.id DESC
            LIMIT ?
            """,
            [*display_params, ticker.upper(), limit],
        ).fetchall()
        article_ids = [row["id"] for row in rows]
        match_index = self.get_article_ticker_matches(article_ids)
        output = []
        for row in rows:
            ticker_matches = match_index.get(row["id"], [])
            output.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "description": row["summary"] or row["body_text"],
                    "url": row["canonical_url"],
                    "publishedDate": row["published_at"],
                    "sourceDomain": row["source_domain"],
                    "tickers": [match["ticker"] for match in ticker_matches],
                    "tickerMatches": ticker_matches,
                }
            )
        return output

    def fetch_narrative_articles_for_ticker(self, ticker: str, limit: int = 500) -> list[dict]:
        display_clause, display_params = _news_display_match_clause("ac")
        rows = self.conn.execute(
            f"""
            SELECT
                a.id,
                a.title,
                a.summary,
                a.canonical_url,
                a.published_at,
                a.sentiment_label,
                a.sentiment_score,
                amr.abnormal_return_1d,
                amr.return_1d,
                amr.price_at_publish,
                amr.primary_event,
                amr.sentiment_score AS reaction_sentiment,
                (
                    SELECT event_type
                    FROM article_event_classifications ec
                    WHERE ec.article_id = a.id
                    ORDER BY ec.confidence DESC
                    LIMIT 1
                ) AS event_type
            FROM articles a
            JOIN article_company ac ON ac.article_id = a.id
                AND ac.confidence >= 0.85
                AND {display_clause}
            JOIN companies c ON c.id = ac.company_id
            LEFT JOIN article_market_reactions amr
                ON amr.article_id = a.id AND amr.ticker = c.ticker
            WHERE c.ticker = ?
              AND a.duplicate_of_article_id IS NULL
            ORDER BY a.published_at DESC, a.id DESC
            LIMIT ?
            """,
            [*display_params, ticker.upper(), limit],
        ).fetchall()
        output: list[dict] = []
        for row in rows:
            output.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "url": row["canonical_url"],
                    "publishedAt": row["published_at"],
                    "sentimentLabel": row["sentiment_label"],
                    "sentimentScore": row["sentiment_score"],
                    "reactionSentiment": row["reaction_sentiment"],
                    "abnormalReturn1d": row["abnormal_return_1d"],
                    "return1d": row["return_1d"],
                    "priceAtPublish": row["price_at_publish"],
                    "primaryEvent": row["primary_event"],
                    "eventType": row["event_type"],
                }
            )
        return output

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
        self.commit()

    def upsert_prices(self, ticker: str, rows: Iterable[dict], source: str) -> int:
        fetched_at = utc_now_iso()
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
                fetched_at,
            )
            for row in rows
            if row.get("date") and row.get("close") is not None
        ]
        if not payload:
            return 0
        self.conn.executemany(
            """
            INSERT INTO prices (ticker, date, open, high, low, close, volume, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date, source) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume,
                fetched_at=excluded.fetched_at
            """,
            payload,
        )
        self.commit()
        return len(payload)

    def fetch_prices(self, ticker: str, since: str | None = None, limit: int | None = None) -> list[dict]:
        sql = """
            SELECT ticker, date, open, high, low, close, volume, source
            FROM prices
            WHERE ticker = ?
        """
        params: list = [ticker.upper()]
        if since:
            sql += " AND date >= ?"
            params.append(since)
        sql += " ORDER BY date DESC, CASE source WHEN 'stooq' THEN 0 ELSE 1 END"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def fetch_prices_batch(
        self,
        tickers: list[str],
        limit_per_ticker: int = 2,
    ) -> dict[str, list[dict]]:
        """Fetch recent prices for multiple tickers in a single query."""
        if not tickers:
            return {}
        upper = [t.upper() for t in tickers]
        placeholders = ",".join("?" for _ in upper)
        sql = f"""
            SELECT ticker, date, open, high, low, close, volume, source
            FROM (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY ticker, date
                           ORDER BY CASE source WHEN 'stooq' THEN 0 ELSE 1 END
                       ) AS src_rank,
                       ROW_NUMBER() OVER (
                           PARTITION BY ticker
                           ORDER BY date DESC, CASE source WHEN 'stooq' THEN 0 ELSE 1 END
                       ) AS date_rank
                FROM prices
                WHERE ticker IN ({placeholders})
            )
            WHERE src_rank = 1 AND date_rank <= ?
            ORDER BY ticker, date DESC
        """
        rows = self.conn.execute(sql, [*upper, limit_per_ticker]).fetchall()
        result: dict[str, list[dict]] = {t: [] for t in upper}
        for row in rows:
            result[row["ticker"]].append(dict(row))
        return result

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
        self.commit()
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
                i.shares,
                i.price_per_share,
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

    def fetch_insider_cluster_counts(self, tickers: list[str]) -> dict[str, int]:
        if not tickers:
            return {}
        placeholders = ",".join("?" for _ in tickers)
        rows = self.conn.execute(
            f"""
            SELECT c.ticker, COUNT(*) AS cluster_count
            FROM insider_cluster_analysis ica
            JOIN companies c ON c.id = ica.company_id
            WHERE c.ticker IN ({placeholders})
              AND COALESCE(ica.total_buy_value, 0) > 0
            GROUP BY c.ticker
            """,
            [t.upper() for t in tickers],
        ).fetchall()
        return {row["ticker"]: int(row["cluster_count"]) for row in rows}

    def fetch_insider_buying_sums(
        self,
        tickers: list[str] | None = None,
        min_buy6m: float | None = None,
    ) -> list[dict]:
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
              AND COALESCE(i.transaction_value, 0) >= 10000
              AND (i.security_title IS NULL OR i.security_title NOT LIKE '%Preferred%')
              {ticker_filter}
            GROUP BY c.ticker, c.name
            {"HAVING buy6m >= ?" if min_buy6m is not None else ""}
            ORDER BY buy6m DESC
            """,
            (*params, min_buy6m) if min_buy6m is not None else params,
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

    # -- watchlists ---------------------------------------------------------

    def list_watchlists(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT w.id, w.name, w.description, w.created_at, w.updated_at,
                   COUNT(wt.ticker) AS ticker_count
            FROM watchlists w
            LEFT JOIN watchlist_tickers wt ON wt.watchlist_id = w.id
            GROUP BY w.id
            ORDER BY w.name
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def get_watchlist(self, name: str) -> dict | None:
        row = self.conn.execute(
            "SELECT id, name, description, created_at, updated_at FROM watchlists WHERE name = ?",
            (name,),
        ).fetchone()
        if not row:
            return None
        wl = dict(row)
        ticker_rows = self.conn.execute(
            "SELECT ticker, added_at FROM watchlist_tickers WHERE watchlist_id = ? ORDER BY ticker",
            (wl["id"],),
        ).fetchall()
        wl["tickers"] = [r["ticker"] for r in ticker_rows]
        return wl

    def upsert_watchlist(self, name: str, description: str | None = None) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO watchlists (name, description)
            VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET
                description = COALESCE(excluded.description, watchlists.description),
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            (name, description),
        )
        wl_id = cursor.fetchone()[0]
        self.commit()
        return wl_id

    def add_ticker_to_watchlist(self, watchlist_id: int, ticker: str) -> None:
        self.conn.execute(
            """
            INSERT INTO watchlist_tickers (watchlist_id, ticker)
            VALUES (?, ?)
            ON CONFLICT(watchlist_id, ticker) DO NOTHING
            """,
            (watchlist_id, ticker.upper()),
        )
        self.conn.execute(
            "UPDATE watchlists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (watchlist_id,),
        )
        self.commit()

    def remove_ticker_from_watchlist(self, watchlist_id: int, ticker: str) -> None:
        self.conn.execute(
            "DELETE FROM watchlist_tickers WHERE watchlist_id = ? AND ticker = ?",
            (watchlist_id, ticker.upper()),
        )
        self.conn.execute(
            "UPDATE watchlists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (watchlist_id,),
        )
        self.commit()

    def set_watchlist_tickers(self, watchlist_id: int, tickers: list[str]) -> None:
        self.conn.execute(
            "DELETE FROM watchlist_tickers WHERE watchlist_id = ?",
            (watchlist_id,),
        )
        normalized = list({t.upper() for t in tickers if t.strip()})
        if normalized:
            self.conn.executemany(
                "INSERT INTO watchlist_tickers (watchlist_id, ticker) VALUES (?, ?)",
                [(watchlist_id, t) for t in sorted(normalized)],
            )
        self.conn.execute(
            "UPDATE watchlists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (watchlist_id,),
        )
        self.commit()

    # -- saved screens ------------------------------------------------------

    @staticmethod
    def _parse_saved_screen_row(row) -> dict:
        import json

        spec = json.loads(row["spec_json"] or "{}")
        return {
            "id": row["id"],
            "name": row["name"],
            "universe": spec.get("universe", "sp500"),
            "filterGroups": spec.get("filterGroups", []),
            "sort": spec.get("sort"),
            "limit": spec.get("limit", 100),
            "sourcePresetId": spec.get("sourcePresetId"),
            "savedAt": row["updated_at"],
        }

    def list_saved_screens(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT id, name, description, spec_json, created_at, updated_at
            FROM saved_screens
            ORDER BY updated_at DESC
            """
        ).fetchall()
        return [self._parse_saved_screen_row(row) for row in rows]

    def upsert_saved_screen(
        self,
        screen_id: str,
        name: str,
        spec: dict,
        *,
        description: str | None = None,
    ) -> dict:
        import json

        spec_json = json.dumps(spec, separators=(",", ":"), sort_keys=True)
        self.conn.execute(
            """
            INSERT INTO saved_screens (id, name, description, spec_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                description = COALESCE(excluded.description, saved_screens.description),
                spec_json = excluded.spec_json,
                updated_at = datetime('now')
            """,
            (screen_id, name, description, spec_json),
        )
        self.commit()
        row = self.conn.execute(
            """
            SELECT id, name, description, spec_json, created_at, updated_at
            FROM saved_screens
            WHERE id = ?
            """,
            (screen_id,),
        ).fetchone()
        return self._parse_saved_screen_row(row)

    def delete_saved_screen(self, screen_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM saved_screens WHERE id = ?",
            (screen_id,),
        )
        self.commit()
        return cursor.rowcount > 0

    def get_company_tags_map(self) -> dict[str, list[str]]:
        rows = self.conn.execute(
            """
            SELECT ticker, tag
            FROM company_tags
            ORDER BY ticker, rowid ASC
            """
        ).fetchall()
        out: dict[str, list[str]] = {}
        for row in rows:
            ticker = row["ticker"]
            out.setdefault(ticker, []).append(row["tag"])
        return out

    def replace_company_tags(self, ticker_tags: dict[str, list[str]]) -> dict[str, list[str]]:
        self.conn.execute("DELETE FROM company_tags")
        rows = [
            (ticker, tag)
            for ticker, tags in sorted(ticker_tags.items())
            for tag in tags
        ]
        if rows:
            self.conn.executemany(
                "INSERT INTO company_tags (ticker, tag) VALUES (?, ?)",
                rows,
            )
        self.commit()
        return ticker_tags

    def set_ticker_company_tags(self, ticker: str, tags: list[str]) -> list[str]:
        normalized_ticker = ticker.strip().upper()
        self.conn.execute(
            "DELETE FROM company_tags WHERE ticker = ?",
            (normalized_ticker,),
        )
        if tags:
            self.conn.executemany(
                "INSERT INTO company_tags (ticker, tag) VALUES (?, ?)",
                [(normalized_ticker, tag) for tag in tags],
            )
        self.commit()
        return tags

    def _load_ui_prefs_dict(self) -> dict:
        row = self.conn.execute(
            "SELECT ui_prefs_json FROM user_preferences WHERE id = 1",
        ).fetchone()
        return self._parse_ui_prefs(row["ui_prefs_json"] if row else None)

    def _save_ui_prefs_dict(self, ui_prefs: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO user_preferences (id, theme, ui_prefs_json)
            VALUES (1, 'dark', ?)
            ON CONFLICT(id) DO UPDATE SET
                ui_prefs_json = excluded.ui_prefs_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (json.dumps(ui_prefs),),
        )

    def get_user_preferences(self) -> dict:
        row = self.conn.execute(
            "SELECT theme, ui_prefs_json FROM user_preferences WHERE id = 1",
        ).fetchone()
        theme = row["theme"] if row else "dark"
        ui_prefs = self._parse_ui_prefs(row["ui_prefs_json"] if row else None)
        wl = self.get_watchlist("Portfolio")
        tickers = (wl or {}).get("tickers", [])
        pinned = ui_prefs.get("researchPinnedTickers") or []
        if not isinstance(pinned, list):
            pinned = []
        seen_pins: set[str] = set()
        pinned_tickers = []
        for ticker in pinned:
            symbol = str(ticker).strip().upper()
            if not symbol or symbol in seen_pins:
                continue
            seen_pins.add(symbol)
            pinned_tickers.append(symbol)
            if len(pinned_tickers) >= 24:
                break
        return {
            "theme": theme,
            "portfolio": tickers,
            "researchColorMode": ui_prefs.get("researchColorMode", "deep_value"),
            "researchHeatLegend": ui_prefs.get("researchHeatLegend", True),
            "researchPinnedTickers": pinned_tickers,
        }

    @staticmethod
    def _parse_ui_prefs(raw: str | None) -> dict:
        if not raw:
            return {}
        try:
            import json

            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}

    def update_user_preferences(
        self,
        *,
        theme: str | None = None,
        portfolio: list[str] | None = None,
        research_color_mode: str | None = None,
        research_heat_legend: bool | None = None,
        research_pinned_tickers: list[str] | None = None,
    ) -> dict:
        if theme is not None:
            self.conn.execute(
                """
                INSERT INTO user_preferences (id, theme, ui_prefs_json)
                VALUES (1, ?, '{}')
                ON CONFLICT(id) DO UPDATE SET
                    theme = excluded.theme,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (theme,),
            )
        ui_updates: dict = {}
        if research_color_mode is not None:
            ui_updates["researchColorMode"] = research_color_mode
        if research_heat_legend is not None:
            ui_updates["researchHeatLegend"] = research_heat_legend
        if ui_updates:
            merged = self._load_ui_prefs_dict()
            merged.update(ui_updates)
            self._save_ui_prefs_dict(merged)
        if research_pinned_tickers is not None:
            seen_pins: set[str] = set()
            normalized_pins = []
            for ticker in research_pinned_tickers:
                symbol = str(ticker).strip().upper()
                if not symbol or symbol in seen_pins:
                    continue
                seen_pins.add(symbol)
                normalized_pins.append(symbol)
                if len(normalized_pins) >= 24:
                    break
            merged = self._load_ui_prefs_dict()
            merged["researchPinnedTickers"] = normalized_pins
            self._save_ui_prefs_dict(merged)
        if portfolio is not None:
            normalized = [
                str(ticker).strip().upper()
                for ticker in portfolio
                if str(ticker).strip()
            ]
            wl_id = self.upsert_watchlist("Portfolio", description="Default portfolio watchlist")
            self.set_watchlist_tickers(wl_id, normalized)
        self.commit()
        return self.get_user_preferences()

    # -- jobs ---------------------------------------------------------------

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
        self.commit()
        return job_id

    def claim_next_job(self) -> dict | None:
        def _claim() -> sqlite3.Row | None:
            # Drop any read transaction left open by a prior handler on this connection.
            self.conn.rollback()
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = self.conn.execute(
                    """
                    UPDATE ingestion_jobs
                    SET status = 'running',
                        locked_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = (
                        SELECT id FROM ingestion_jobs
                        WHERE status = 'queued' AND available_at <= CURRENT_TIMESTAMP
                        ORDER BY priority ASC, id ASC
                        LIMIT 1
                    )
                    RETURNING id, job_type, payload_json, attempt_count
                    """
                ).fetchone()
                self.commit()
                return row
            except Exception:
                self.conn.rollback()
                raise

        row = retry_on_sqlite_lock(_claim, operation="claim_next_job")
        if not row:
            return None
        return {
            "id": row["id"],
            "job_type": row["job_type"],
            "payload": json.loads(row["payload_json"]),
            "attempt_count": row["attempt_count"],
        }

    def complete_job(self, job_id: int, status: str = "done", error_message: str | None = None) -> None:
        now = utc_now_iso()
        self.conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = ?, updated_at = ?, locked_at = NULL
            WHERE id = ?
            """,
            (status, now, job_id),
        )
        self.conn.execute(
            """
            INSERT INTO job_runs (job_id, finished_at, status, error_message)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, now, status, error_message),
        )
        self.commit()

    def get_article_by_id(self, article_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        return dict(row) if row else None

    def get_articles_by_ids(self, article_ids: list[int]) -> dict[int, dict]:
        if not article_ids:
            return {}
        placeholders = ",".join("?" for _ in article_ids)
        rows = self.conn.execute(
            f"SELECT * FROM articles WHERE id IN ({placeholders})",
            article_ids,
        ).fetchall()
        return {row["id"]: dict(row) for row in rows}

    def get_article_tickers(self, article_id: int) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT c.ticker
            FROM article_company ac
            JOIN companies c ON c.id = ac.company_id
            WHERE ac.article_id = ? AND ac.confidence >= 0.80
            ORDER BY ac.confidence DESC
            """,
            (article_id,),
        ).fetchall()
        return [row["ticker"] for row in rows]

    def recover_stuck_pipeline_articles(self) -> int:
        """Reset orphaned processing rows after a cancelled run or crashed request."""
        cursor = self.conn.execute(
            """
            UPDATE articles
            SET pipeline_status = 'pending', updated_at = CURRENT_TIMESTAMP
            WHERE duplicate_of_article_id IS NULL
              AND pipeline_status = 'processing'
            """
        )
        self.commit()
        return cursor.rowcount

    def get_pipeline_status_counts(self) -> dict:
        rows = self.conn.execute(
            """
            SELECT COALESCE(pipeline_status, 'pending') AS status, COUNT(*) AS count
            FROM articles
            WHERE duplicate_of_article_id IS NULL
            GROUP BY COALESCE(pipeline_status, 'pending')
            """
        ).fetchall()
        counts = {row["status"]: row["count"] for row in rows}
        pending = counts.get("pending", 0) + counts.get("error", 0)
        return {
            "pending": pending,
            "processing": counts.get("processing", 0),
            "complete": counts.get("complete", 0),
            "error": counts.get("error", 0),
            "duplicate": self.conn.execute(
                "SELECT COUNT(*) FROM articles WHERE duplicate_of_article_id IS NOT NULL"
            ).fetchone()[0],
            "by_status": counts,
        }

    def requeue_completed_articles(self, *, limit: int = 500) -> int:
        cursor = self.conn.execute(
            """
            UPDATE articles
            SET pipeline_status = 'pending', updated_at = CURRENT_TIMESTAMP
            WHERE id IN (
                SELECT id
                FROM articles
                WHERE duplicate_of_article_id IS NULL
                  AND pipeline_status = 'complete'
                ORDER BY published_at DESC, id DESC
                LIMIT ?
            )
            """,
            (limit,),
        )
        self.commit()
        return cursor.rowcount

    def list_articles_for_retag(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        only_missing_enrichment: bool = False,
    ) -> list[int]:
        clauses = [
            "duplicate_of_article_id IS NULL",
            "pipeline_status = 'complete'",
        ]
        if only_missing_enrichment:
            clauses.append(
                """
                NOT EXISTS (
                    SELECT 1
                    FROM article_company ac
                    WHERE ac.article_id = articles.id
                      AND ac.extraction_stage = 'enrichment'
                )
                """
            )
        where_sql = " AND ".join(clauses)
        rows = self.conn.execute(
            f"""
            SELECT id
            FROM articles
            WHERE {where_sql}
            ORDER BY published_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [row["id"] for row in rows]

    def count_articles_for_retag(self, *, only_missing_enrichment: bool = False) -> int:
        clauses = [
            "duplicate_of_article_id IS NULL",
            "pipeline_status = 'complete'",
        ]
        if only_missing_enrichment:
            clauses.append(
                """
                NOT EXISTS (
                    SELECT 1
                    FROM article_company ac
                    WHERE ac.article_id = articles.id
                      AND ac.extraction_stage = 'enrichment'
                )
                """
            )
        where_sql = " AND ".join(clauses)
        return self.conn.execute(
            f"SELECT COUNT(*) FROM articles WHERE {where_sql}",
        ).fetchone()[0]

    def get_article_embedding_vector(self, article_id: int, *, model: str) -> list[float] | None:
        row = self.conn.execute(
            """
            SELECT vector_json
            FROM article_embedding_vectors
            WHERE article_id = ? AND model = ?
            """,
            (article_id, model),
        ).fetchone()
        if not row:
            return None
        from .services.embeddings_service import vector_from_json

        return vector_from_json(row["vector_json"])

    def list_articles_pending_pipeline(self, *, limit: int = 25) -> list[int]:
        rows = self.conn.execute(
            """
            SELECT id
            FROM articles
            WHERE duplicate_of_article_id IS NULL
              AND COALESCE(pipeline_status, 'pending') IN ('pending', 'error')
            ORDER BY published_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [row["id"] for row in rows]

    def set_article_pipeline_status(self, article_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE articles SET pipeline_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, article_id),
        )
        self.commit()

    def set_article_extraction_status(self, article_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE articles SET extraction_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, article_id),
        )
        self.commit()

    def update_article_body(
        self,
        article_id: int,
        body_text: str,
        *,
        content_hash: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE articles
            SET body_text = ?, content_hash = COALESCE(?, content_hash), updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (body_text, content_hash, article_id),
        )
        self.commit()

    def update_article_sentiment(self, article_id: int, sentiment) -> None:
        self.conn.execute(
            """
            UPDATE articles
            SET sentiment_label = ?,
                sentiment_score = ?,
                vader_compound = ?,
                vader_pos = ?,
                vader_neu = ?,
                vader_neg = ?,
                finbert_label = ?,
                finbert_pos = ?,
                finbert_neu = ?,
                finbert_neg = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                sentiment.label,
                sentiment.score,
                sentiment.vader_compound,
                sentiment.vader_pos,
                sentiment.vader_neu,
                sentiment.vader_neg,
                sentiment.finbert_label,
                sentiment.finbert_pos,
                sentiment.finbert_neu,
                sentiment.finbert_neg,
                article_id,
            ),
        )
        self.commit()

    def update_article_ranking(
        self,
        article_id: int,
        *,
        rank_score: float,
        novelty_score: float | None = None,
        engagement_score: float | None = None,
        news_importance_score: float | None = None,
        divergence_context: str | None = None,
        event_cluster_id: int | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE articles
            SET rank_score = ?,
                novelty_score = COALESCE(?, novelty_score),
                engagement_score = COALESCE(?, engagement_score),
                news_importance_score = COALESCE(?, news_importance_score),
                divergence_context = COALESCE(?, divergence_context),
                event_cluster_id = COALESCE(?, event_cluster_id),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                rank_score,
                novelty_score,
                engagement_score,
                news_importance_score,
                divergence_context,
                event_cluster_id,
                article_id,
            ),
        )
        self.commit()

    def get_article_entity_confidence_avg(self, article_id: int) -> float | None:
        row = self.conn.execute(
            "SELECT AVG(confidence) AS avg_conf FROM article_company WHERE article_id = ?",
            (article_id,),
        ).fetchone()
        if row is None or row["avg_conf"] is None:
            return None
        return float(row["avg_conf"])

    def get_recent_narrative_divergence_for_ticker(
        self,
        ticker: str,
        *,
        window_days: int = 7,
    ) -> dict | None:
        from datetime import date, timedelta

        cutoff = (date.today() - timedelta(days=window_days)).isoformat()
        row = self.conn.execute(
            """
            SELECT divergence_signal, divergence_score, snapshot_date
            FROM company_narrative_snapshots
            WHERE ticker = ?
              AND snapshot_date >= ?
              AND divergence_signal IN ('rerating_candidate', 'high_conviction', 'risk_flag')
            ORDER BY snapshot_date DESC, divergence_score DESC
            LIMIT 1
            """,
            (ticker.upper(), cutoff),
        ).fetchone()
        return dict(row) if row else None

    def list_event_clusters(
        self,
        *,
        event_type: str | None = None,
        hours: int = 72,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        import json
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        clauses = ["last_seen_at >= ?"]
        params: list = [cutoff]
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        where_sql = " AND ".join(clauses)
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM article_event_clusters WHERE {where_sql}",
            params,
        ).fetchone()[0]
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM article_event_clusters
            WHERE {where_sql}
            ORDER BY article_count DESC, last_seen_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
        clusters = []
        for row in rows:
            domains = []
            if row["source_domains_json"]:
                try:
                    domains = json.loads(row["source_domains_json"])
                except json.JSONDecodeError:
                    domains = []
            clusters.append(
                {
                    "id": row["id"],
                    "eventType": row["event_type"],
                    "headline": row["headline"],
                    "firstSeenAt": row["first_seen_at"],
                    "lastSeenAt": row["last_seen_at"],
                    "articleCount": row["article_count"],
                    "sourceCount": row["source_count"],
                    "sourceDomains": domains,
                    "consensusSentiment": row["consensus_sentiment"],
                }
            )
        return clusters, int(total)

    def get_event_cluster_members(self, cluster_id: int, *, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT id, title, canonical_url, source_domain, published_at, sentiment_score, rank_score, news_importance_score
            FROM articles
            WHERE event_cluster_id = ?
              AND duplicate_of_article_id IS NULL
            ORDER BY COALESCE(news_importance_score, rank_score) DESC NULLS LAST, published_at DESC
            LIMIT ?
            """,
            (cluster_id, limit),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "url": row["canonical_url"],
                "sourceDomain": row["source_domain"],
                "publishedAt": row["published_at"],
                "sentimentScore": row["sentiment_score"],
                "newsImportanceScore": row["news_importance_score"] or row["rank_score"],
            }
            for row in rows
        ]

    def find_recent_event_clusters(
        self,
        event_type: str,
        *,
        hours: int = 72,
        limit: int = 100,
    ) -> list[dict]:
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        rows = self.conn.execute(
            """
            SELECT *
            FROM article_event_clusters
            WHERE event_type = ?
              AND last_seen_at >= ?
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            (event_type, cutoff, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def create_event_cluster(self, record: dict) -> int:
        import json

        cursor = self.conn.execute(
            """
            INSERT INTO article_event_clusters (
                event_type, headline, first_seen_at, last_seen_at,
                article_count, source_count, source_domains_json,
                consensus_sentiment, centroid_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["event_type"],
                record.get("headline"),
                record.get("first_seen_at"),
                record.get("last_seen_at"),
                int(record.get("article_count", 1)),
                int(record.get("source_count", len(record.get("source_domains") or [])) or 1),
                json.dumps(record.get("source_domains") or []),
                record.get("consensus_sentiment"),
                json.dumps(record.get("centroid") or []),
            ),
        )
        self.commit()
        return int(cursor.lastrowid)

    def update_event_cluster(self, cluster_id: int, record: dict) -> None:
        import json

        self.conn.execute(
            """
            UPDATE article_event_clusters
            SET headline = COALESCE(?, headline),
                last_seen_at = COALESCE(?, last_seen_at),
                article_count = COALESCE(?, article_count),
                source_count = COALESCE(?, source_count),
                source_domains_json = COALESCE(?, source_domains_json),
                consensus_sentiment = COALESCE(?, consensus_sentiment),
                centroid_json = COALESCE(?, centroid_json),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                record.get("headline"),
                record.get("last_seen_at"),
                record.get("article_count"),
                record.get("source_count"),
                json.dumps(record["source_domains"]) if "source_domains" in record else None,
                record.get("consensus_sentiment"),
                json.dumps(record["centroid"]) if "centroid" in record else None,
                cluster_id,
            ),
        )
        self.commit()

    def assign_article_event_cluster(self, article_id: int, cluster_id: int) -> None:
        self.conn.execute(
            "UPDATE articles SET event_cluster_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (cluster_id, article_id),
        )
        self.commit()

    def mark_article_duplicate(self, article_id: int, duplicate_of: int) -> None:
        self.conn.execute(
            """
            UPDATE articles
            SET duplicate_of_article_id = ?, pipeline_status = 'duplicate', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (duplicate_of, article_id),
        )
        self.commit()

    def replace_article_events(self, article_id: int, events: list) -> None:
        self.conn.execute(
            "DELETE FROM article_event_classifications WHERE article_id = ?",
            (article_id,),
        )
        if events:
            self.conn.executemany(
                """
                INSERT INTO article_event_classifications (article_id, event_type, confidence, method)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(article_id, event_type) DO UPDATE SET
                    confidence = excluded.confidence,
                    method = excluded.method
                """,
                [(article_id, e.event_type, e.confidence, e.method) for e in events],
            )
        self.commit()

    def replace_article_market_reactions(self, article_id: int, reactions: list) -> None:
        self.conn.execute(
            "DELETE FROM article_market_reactions WHERE article_id = ?",
            (article_id,),
        )
        if reactions:
            self.conn.executemany(
                """
                INSERT INTO article_market_reactions (
                    article_id, ticker, published_at, sentiment_score, primary_event,
                    price_at_publish, return_1d, return_1w, benchmark_return_1d, abnormal_return_1d
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        article_id,
                        r.ticker,
                        r.published_at,
                        r.sentiment_score,
                        r.primary_event,
                        r.price_at_publish,
                        r.return_1d,
                        r.return_1w,
                        r.benchmark_return_1d,
                        r.abnormal_return_1d,
                    )
                    for r in reactions
                ],
            )
        self.commit()

    # Embedding storage touchpoint — migration options in docs/SCALING.md (pgvector / FAISS).
    def upsert_article_embedding(
        self,
        article_id: int,
        *,
        model: str,
        vector: list[float],
        content_hash: str | None = None,
    ) -> None:
        from .services.embeddings_service import vector_to_json

        now = utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO article_embedding_vectors (
                article_id, model, dimensions, vector_json, content_hash, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id, model) DO UPDATE SET
                dimensions = excluded.dimensions,
                vector_json = excluded.vector_json,
                content_hash = excluded.content_hash,
                updated_at = excluded.updated_at,
                created_at = CURRENT_TIMESTAMP
            """,
            (article_id, model, len(vector), vector_to_json(vector), content_hash, now),
        )
        self.upsert_embedding_metadata(
            article_id,
            model=model,
            content_hash=content_hash,
            vector_dimensions=len(vector),
            defer_commit=True,
        )
        self.commit()

    def find_embedding_duplicate(
        self,
        article_id: int,
        vector: list[float],
        *,
        model: str,
        threshold: float = 0.92,
        lookback: int = 500,
    ) -> tuple[int | None, float | None]:
        from .services.embeddings_service import cosine_similarity, vector_from_json

        rows = self.conn.execute(
            """
            SELECT aev.article_id, aev.vector_json
            FROM article_embedding_vectors aev
            JOIN articles a ON a.id = aev.article_id
            WHERE aev.model = ?
              AND aev.article_id != ?
              AND a.duplicate_of_article_id IS NULL
            ORDER BY aev.article_id DESC
            LIMIT ?
            """,
            (model, article_id, lookback),
        ).fetchall()
        best_id = None
        best_sim = None
        for row in rows:
            other = vector_from_json(row["vector_json"])
            sim = cosine_similarity(vector, other)
            if sim >= threshold and (best_sim is None or sim > best_sim):
                best_id = row["article_id"]
                best_sim = sim
        return best_id, best_sim

    def get_domain_fetch_state(self, domain: str) -> dict | None:
        row = self.conn.execute(
            "SELECT domain, last_fetched_at, consecutive_failures, backoff_until FROM domain_fetch_state WHERE domain = ?",
            (domain.lower(),),
        ).fetchone()
        return dict(row) if row else None

    def upsert_domain_fetch_state(self, domain: str, *, success: bool) -> int:
        domain = domain.lower()
        now = utc_now_iso()
        row = self.get_domain_fetch_state(domain)
        failures = (row or {}).get("consecutive_failures") or 0
        if success:
            self.conn.execute(
                """
                INSERT INTO domain_fetch_state (domain, last_fetched_at, consecutive_failures, backoff_until)
                VALUES (?, ?, 0, NULL)
                ON CONFLICT(domain) DO UPDATE SET
                    last_fetched_at = excluded.last_fetched_at,
                    consecutive_failures = 0,
                    backoff_until = NULL
                """,
                (domain, now),
            )
            self.commit()
            return 0
        failures += 1
        backoff_minutes = min(60, 2 ** min(failures, 6))
        backoff_until = (datetime.now(timezone.utc) + timedelta(minutes=backoff_minutes)).replace(microsecond=0)
        backoff_iso = backoff_until.isoformat().replace("+00:00", "Z")
        self.conn.execute(
            """
            INSERT INTO domain_fetch_state (domain, last_fetched_at, consecutive_failures, backoff_until)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                consecutive_failures = excluded.consecutive_failures,
                backoff_until = excluded.backoff_until
            """,
            (domain, (row or {}).get("last_fetched_at"), failures, backoff_iso),
        )
        self.commit()
        return failures

    def event_reaction_analytics(self, *, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT
                amr.primary_event AS event_type,
                COUNT(*) AS sample_size,
                AVG(amr.abnormal_return_1d) AS avg_abnormal_return_1d,
                AVG(amr.return_1d) AS avg_return_1d
            FROM article_market_reactions amr
            WHERE amr.primary_event IS NOT NULL
              AND amr.abnormal_return_1d IS NOT NULL
            GROUP BY amr.primary_event
            ORDER BY ABS(AVG(amr.abnormal_return_1d)) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def fail_job(self, job_id: int, error_message: str, retry_in_minutes: int = 15) -> None:
        row = self.conn.execute("SELECT attempt_count FROM ingestion_jobs WHERE id = ?", (job_id,)).fetchone()
        attempts = (row["attempt_count"] if row else 0) + 1
        status = "failed" if attempts >= 3 else "queued"
        now = utc_now_iso()
        self.conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = ?,
                attempt_count = ?,
                available_at = datetime('now', ?),
                locked_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (status, attempts, f"+{retry_in_minutes} minutes", now, job_id),
        )
        self.conn.execute(
            """
            INSERT INTO job_runs (job_id, finished_at, status, error_message)
            VALUES (?, ?, 'error', ?)
            """,
            (job_id, now, error_message),
        )
        self.commit()

    def upsert_company_edgar_events(self, company_id: int, events: Iterable[dict]) -> int:
        rows = [
            (
                company_id,
                item.get("form_type"),
                item.get("item_number"),
                item.get("filed_date"),
                item.get("event_type"),
                item.get("summary"),
                item.get("accession"),
                item.get("source", "sec_edgar"),
            )
            for item in events
        ]
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO company_edgar_events (
                company_id, form_type, item_number, filed_date, event_type, summary, accession, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, accession, item_number, event_type) DO UPDATE SET
                filed_date=excluded.filed_date,
                summary=excluded.summary
            """,
            rows,
        )
        self.commit()
        return len(rows)

    def upsert_company_edgar_flags(self, company_id: int, flags: Iterable[dict]) -> int:
        rows = [
            (
                company_id,
                item.get("flag_type"),
                item.get("filed_date"),
                item.get("accession"),
                item.get("details"),
                int(item.get("active", 1)),
            )
            for item in flags
        ]
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO company_edgar_flags (
                company_id, flag_type, filed_date, accession, details, active, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(company_id, flag_type) DO UPDATE SET
                filed_date=excluded.filed_date,
                accession=excluded.accession,
                details=excluded.details,
                active=excluded.active,
                updated_at=CURRENT_TIMESTAMP
            """,
            rows,
        )
        self.commit()
        return len(rows)

    def upsert_company_insider_ownership(self, company_id: int, record: dict) -> int:
        self.conn.execute(
            """
            INSERT INTO company_insider_ownership (
                company_id, as_of_date, ownership_pct, shares_held, shares_outstanding, source, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(company_id, as_of_date) DO UPDATE SET
                ownership_pct=excluded.ownership_pct,
                shares_held=excluded.shares_held,
                shares_outstanding=excluded.shares_outstanding,
                computed_at=CURRENT_TIMESTAMP
            """,
            (
                company_id,
                record.get("as_of_date"),
                record.get("ownership_pct"),
                record.get("shares_held"),
                record.get("shares_outstanding"),
                record.get("source", "sec_edgar"),
            ),
        )
        self.commit()
        return 1

    def upsert_company_activist_filings(self, company_id: int, filings: Iterable[dict]) -> int:
        rows = [
            (
                company_id,
                item.get("filed_date"),
                item.get("form_type"),
                item.get("accession"),
                item.get("filer_name"),
                item.get("ownership_pct"),
                item.get("summary"),
                item.get("source", "sec_edgar"),
            )
            for item in filings
        ]
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO company_activist_filings (
                company_id, filed_date, form_type, accession, filer_name, ownership_pct, summary, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, accession) DO UPDATE SET
                filed_date=excluded.filed_date,
                form_type=excluded.form_type,
                summary=excluded.summary
            """,
            rows,
        )
        self.commit()
        return len(rows)

    def upsert_company_debt_maturities(
        self,
        company_id: int,
        period_end: str | None,
        rows: Iterable[dict],
    ) -> int:
        if not period_end:
            return 0
        payload = [
            (company_id, period_end, item.get("maturity_year"), item.get("amount"), item.get("source", "sec_edgar"))
            for item in rows
        ]
        if not payload:
            return 0
        self.conn.executemany(
            """
            INSERT INTO company_debt_maturities (
                company_id, period_end, maturity_year, amount, source, computed_at
            ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(company_id, period_end, maturity_year) DO UPDATE SET
                amount=excluded.amount,
                computed_at=CURRENT_TIMESTAMP
            """,
            payload,
        )
        self.commit()
        return len(payload)

    def upsert_company_segments(
        self,
        company_id: int,
        period_end: str | None,
        rows: Iterable[dict],
    ) -> int:
        if not period_end:
            return 0
        payload = [
            (
                company_id,
                period_end,
                item.get("segment_name"),
                item.get("revenue"),
                item.get("operating_income"),
                item.get("margin"),
                item.get("source", "sec_edgar"),
            )
            for item in rows
        ]
        if not payload:
            return 0
        self.conn.executemany(
            """
            INSERT INTO company_segments (
                company_id, period_end, segment_name, revenue, operating_income, margin, source, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(company_id, period_end, segment_name) DO UPDATE SET
                revenue=excluded.revenue,
                operating_income=excluded.operating_income,
                margin=excluded.margin,
                computed_at=CURRENT_TIMESTAMP
            """,
            payload,
        )
        self.commit()
        return len(payload)

    def upsert_company_market_data(
        self,
        ticker: str,
        as_of_date: str | None,
        metric: str,
        value: float | None,
        *,
        source: str,
    ) -> int:
        if not as_of_date:
            return 0
        self.conn.execute(
            """
            INSERT INTO company_market_data (ticker, as_of_date, metric, value, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(ticker, as_of_date, metric) DO UPDATE SET
                value=excluded.value,
                source=excluded.source,
                fetched_at=CURRENT_TIMESTAMP
            """,
            (ticker.upper(), as_of_date, metric, value, source),
        )
        self.commit()
        return 1

    def fetch_company_edgar_flags(self, ticker: str) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT f.flag_type, f.filed_date, f.accession, f.details, f.active, f.updated_at
            FROM company_edgar_flags f
            JOIN companies c ON c.id = f.company_id
            WHERE c.ticker = ? AND f.active = 1
            ORDER BY f.filed_date DESC
            """,
            (ticker.upper(),),
        ).fetchall()
        return [dict(row) for row in rows]

    def fetch_company_edgar_events(self, ticker: str, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT e.form_type, e.item_number, e.filed_date, e.event_type, e.summary, e.accession
            FROM company_edgar_events e
            JOIN companies c ON c.id = e.company_id
            WHERE c.ticker = ?
            ORDER BY e.filed_date DESC
            LIMIT ?
            """,
            (ticker.upper(), limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def fetch_latest_insider_ownership(self, ticker: str) -> dict | None:
        row = self.conn.execute(
            """
            SELECT o.as_of_date, o.ownership_pct, o.shares_held, o.shares_outstanding, o.computed_at
            FROM company_insider_ownership o
            JOIN companies c ON c.id = o.company_id
            WHERE c.ticker = ?
            ORDER BY o.as_of_date DESC
            LIMIT 1
            """,
            (ticker.upper(),),
        ).fetchone()
        return dict(row) if row else None

    def fetch_company_activist_filings(self, ticker: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT a.filed_date, a.form_type, a.accession, a.filer_name, a.ownership_pct, a.summary
            FROM company_activist_filings a
            JOIN companies c ON c.id = a.company_id
            WHERE c.ticker = ?
            ORDER BY a.filed_date DESC
            LIMIT ?
            """,
            (ticker.upper(), limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def fetch_company_debt_maturities(self, ticker: str) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT d.period_end, d.maturity_year, d.amount, d.computed_at
            FROM company_debt_maturities d
            JOIN companies c ON c.id = d.company_id
            WHERE c.ticker = ?
            ORDER BY d.period_end DESC, d.maturity_year ASC
            """,
            (ticker.upper(),),
        ).fetchall()
        return [dict(row) for row in rows]

    def fetch_company_segments(self, ticker: str) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT s.period_end, s.segment_name, s.revenue, s.operating_income, s.margin, s.computed_at
            FROM company_segments s
            JOIN companies c ON c.id = s.company_id
            WHERE c.ticker = ?
            ORDER BY s.period_end DESC, s.revenue DESC
            """,
            (ticker.upper(),),
        ).fetchall()
        return [dict(row) for row in rows]

    def fetch_latest_market_data(self, ticker: str, metric: str) -> dict | None:
        row = self.conn.execute(
            """
            SELECT ticker, as_of_date, metric, value, source, fetched_at
            FROM company_market_data
            WHERE ticker = ? AND metric = ?
            ORDER BY as_of_date DESC
            LIMIT 1
            """,
            (ticker.upper(), metric),
        ).fetchone()
        return dict(row) if row else None
