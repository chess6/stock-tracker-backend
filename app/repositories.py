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
        self.conn.commit()

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
        self.conn.commit()

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
        self.conn.commit()
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

    def upsert_company_scores(self, company_id: int, records: Iterable[dict]) -> int:
        rows = [
            (
                company_id,
                record["period_end"],
                record.get("dimension", "ARY"),
                record.get("piotroski_f"),
                record.get("altman_z"),
                record.get("beneish_m"),
                record.get("survivability"),
                record.get("piotroski_components"),
                record.get("altman_components"),
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
                piotroski_components, altman_components, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(company_id, period_end, dimension) DO UPDATE SET
                piotroski_f=excluded.piotroski_f,
                altman_z=excluded.altman_z,
                beneish_m=excluded.beneish_m,
                survivability=excluded.survivability,
                piotroski_components=excluded.piotroski_components,
                altman_components=excluded.altman_components,
                computed_at=CURRENT_TIMESTAMP
            """,
            rows,
        )
        self.conn.commit()
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
                piotroski_components,
                altman_components,
                computed_at
            FROM company_scores
            WHERE company_id = ? AND dimension = ?
            ORDER BY period_end DESC
            """,
            (company_id, dimension),
        ).fetchall()
        return [self._format_score_row(row) for row in rows]

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
                cs.piotroski_components,
                cs.altman_components,
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
            self.conn.commit()
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
        self.conn.commit()
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
        self.conn.commit()

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
        self.conn.commit()
        return updated

    def deduplicate_articles(self, lookback: int = 2000) -> dict:
        from .services.article_dedup import find_semantic_duplicate

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
            FROM articles
            WHERE duplicate_of_article_id IS NULL
            ORDER BY id ASC
            LIMIT ?
            """,
            (lookback,),
        ).fetchall()
        canonical: list[dict] = []
        fuzzy_window = 50
        marked = 0
        for row in rows:
            article = dict(row)
            recent_canonical = canonical[-fuzzy_window:] if len(canonical) > fuzzy_window else canonical
            duplicate_id = find_semantic_duplicate(
                article["title"],
                article.get("summary"),
                recent_canonical,
            )
            if duplicate_id is not None:
                self.conn.execute(
                    "UPDATE articles SET duplicate_of_article_id = ? WHERE id = ?",
                    (duplicate_id, article["id"]),
                )
                marked += 1
            else:
                canonical.append(article)
        self.conn.commit()
        return {
            "scanned": len(rows),
            "exactDuplicates": exact_dupes,
            "simhashDuplicates": simhash_dupes,
            "fuzzyDuplicates": marked,
            "markedDuplicates": exact_dupes + simhash_dupes + marked,
            "uniqueRemaining": len(canonical),
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
            self.conn.commit()
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
            self.conn.commit()

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
            self.conn.commit()
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
            self.conn.commit()
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
        self.conn.commit()
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
        self.conn.commit()
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
            self.conn.commit()

    def list_unique_articles(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        q: str | None = None,
        category: str | None = None,
        source_domain: str | None = None,
        tickers: list[str] | None = None,
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
            ORDER BY COALESCE(a.rank_score, 0) DESC, a.published_at DESC, a.id DESC
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
                    "topicCluster": row["topic_cluster_id"],
                    "tickers": tickers,
                    "tickerMatches": ticker_matches,
                }
            )
        return {"articles": articles, "total": total, "limit": limit, "offset": offset}

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
        self.conn.commit()
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
        self.conn.commit()

    def remove_ticker_from_watchlist(self, watchlist_id: int, ticker: str) -> None:
        self.conn.execute(
            "DELETE FROM watchlist_tickers WHERE watchlist_id = ? AND ticker = ?",
            (watchlist_id, ticker.upper()),
        )
        self.conn.execute(
            "UPDATE watchlists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (watchlist_id,),
        )
        self.conn.commit()

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
        self.conn.commit()

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
        return {
            "theme": theme,
            "portfolio": tickers,
            "researchColorMode": ui_prefs.get("researchColorMode", "deep_value"),
            "researchHeatLegend": ui_prefs.get("researchHeatLegend", True),
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
            current = self.get_user_preferences()
            merged = {
                "researchColorMode": current.get("researchColorMode", "deep_value"),
                "researchHeatLegend": current.get("researchHeatLegend", True),
            }
            merged.update(ui_updates)
            self._save_ui_prefs_dict(merged)
        if portfolio is not None:
            normalized = [
                str(ticker).strip().upper()
                for ticker in portfolio
                if str(ticker).strip()
            ]
            wl_id = self.upsert_watchlist("Portfolio", description="Default portfolio watchlist")
            self.set_watchlist_tickers(wl_id, normalized)
        self.conn.commit()
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
        self.conn.commit()
        return job_id

    def claim_next_job(self) -> dict | None:
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
        self.conn.commit()
        if not row:
            return None
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
        self.conn.commit()
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
        self.conn.commit()
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
        self.conn.commit()

    def set_article_extraction_status(self, article_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE articles SET extraction_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, article_id),
        )
        self.conn.commit()

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
        self.conn.commit()

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
        self.conn.commit()

    def update_article_ranking(
        self,
        article_id: int,
        *,
        rank_score: float,
        novelty_score: float | None = None,
        engagement_score: float | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE articles
            SET rank_score = ?,
                novelty_score = COALESCE(?, novelty_score),
                engagement_score = COALESCE(?, engagement_score),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (rank_score, novelty_score, engagement_score, article_id),
        )
        self.conn.commit()

    def mark_article_duplicate(self, article_id: int, duplicate_of: int) -> None:
        self.conn.execute(
            """
            UPDATE articles
            SET duplicate_of_article_id = ?, pipeline_status = 'duplicate', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (duplicate_of, article_id),
        )
        self.conn.commit()

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
        self.conn.commit()

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
        self.conn.commit()

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

        self.conn.execute(
            """
            INSERT INTO article_embedding_vectors (article_id, model, dimensions, vector_json, content_hash)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(article_id, model) DO UPDATE SET
                dimensions = excluded.dimensions,
                vector_json = excluded.vector_json,
                content_hash = excluded.content_hash,
                created_at = CURRENT_TIMESTAMP
            """,
            (article_id, model, len(vector), vector_to_json(vector), content_hash),
        )
        self.upsert_embedding_metadata(
            article_id,
            model=model,
            content_hash=content_hash,
            vector_dimensions=len(vector),
            defer_commit=True,
        )
        self.conn.commit()

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
            self.conn.commit()
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
        self.conn.commit()
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
