from __future__ import annotations

import logging
import time
from collections import defaultdict

import requests

from ..repositories import Repository
from .company_enrichment import metadata_from_submissions
from .metric_primitives import normalize_fundamentals_row
from .metrics_engine import build_company_metrics
from .scoring import compute_scores_for_periods, scores_to_json
from .sec import normalize_company_facts
from .sec_eligibility import sec_http_outcome, should_skip_sec_fundamentals

logger = logging.getLogger("stock_tracker.pipeline.fundamentals")


RAW_COLUMNS = [
    {"name": "ticker", "type": "text"},
    {"name": "company_name", "type": "text"},
    {"name": "calendardate", "type": "Date"},
    {"name": "dimension", "type": "text"},
    {"name": "filingdate", "type": "Date"},
    {"name": "periodtype", "type": "text"},
    {"name": "revenue", "type": "double"},
    {"name": "cor", "type": "double"},
    {"name": "gp", "type": "double"},
    {"name": "opex", "type": "double"},
    {"name": "sgna", "type": "double"},
    {"name": "rnd", "type": "double"},
    {"name": "opinc", "type": "double"},
    {"name": "ebit", "type": "double"},
    {"name": "ebitda", "type": "double"},
    {"name": "netinc", "type": "double"},
    {"name": "compinc", "type": "double"},
    {"name": "eps", "type": "double"},
    {"name": "taxexp", "type": "double"},
    {"name": "interestexp", "type": "double"},
    {"name": "depamor", "type": "double"},
    {"name": "assets", "type": "double"},
    {"name": "assetscurrent", "type": "double"},
    {"name": "liabilities", "type": "double"},
    {"name": "liabilitiescurrent", "type": "double"},
    {"name": "equity", "type": "double"},
    {"name": "cashneq", "type": "double"},
    {"name": "debt", "type": "double"},
    {"name": "debtlt", "type": "double"},
    {"name": "debtcurrent", "type": "double"},
    {"name": "ppnenet", "type": "double"},
    {"name": "inventory", "type": "double"},
    {"name": "receivables", "type": "double"},
    {"name": "payables", "type": "double"},
    {"name": "retearn", "type": "double"},
    {"name": "goodwill", "type": "double"},
    {"name": "intangibles", "type": "double"},
    {"name": "workingcapital", "type": "double"},
    {"name": "ncfo", "type": "double"},
    {"name": "capex", "type": "double"},
    {"name": "fcf", "type": "double"},
    {"name": "ncfi", "type": "double"},
    {"name": "ncff", "type": "double"},
    {"name": "ncfdiv", "type": "double"},
    {"name": "ncfdebt", "type": "double"},
    {"name": "ncfcommon", "type": "double"},
    {"name": "sbcomp", "type": "double"},
    {"name": "ncf", "type": "double"},
    {"name": "sharesbas", "type": "double"},
]

STATEMENT_METRICS = frozenset(
    {
        "revenue", "cor", "gp", "opex", "sgna", "rnd", "opinc", "ebit", "ebitda",
        "netinc", "compinc", "eps", "taxexp", "interestexp", "depamor",
        "assets", "assetscurrent", "liabilities", "liabilitiescurrent", "equity",
        "cashneq", "debt", "debtlt", "debtcurrent", "ppnenet", "inventory",
        "receivables", "payables", "retearn", "goodwill", "intangibles",
        "workingcapital", "ncfo", "capex", "fcf", "ncfi", "ncff", "ncfdiv",
        "ncfdebt", "ncfcommon", "sbcomp", "ncf",
    }
)

# Full fiscal-year 10-K rows typically carry many statement metrics; interim 8-K orphans do not.
CANONICAL_ANNUAL_MIN_STATEMENT_METRICS = 8


def _dedupe_narrow_rows(rows: list[dict]) -> list[dict]:
    latest: dict[tuple, dict] = {}
    for row in rows:
        key = (row["ticker"], row["metric"], row["period_end"], row["dimension"])
        existing = latest.get(key)
        if not existing or (row.get("filing_date") or "") > (existing.get("filing_date") or ""):
            latest[key] = row
    return list(latest.values())


def collapse_narrow_fundamentals_rows(rows: list[dict], *, annual: bool) -> list[dict]:
    """Keep one snapshot per fiscal period/metric for history charts."""
    deduped = _dedupe_narrow_rows(rows)
    buckets: dict[tuple, dict] = {}
    for row in deduped:
        if annual:
            cal_year = (row.get("period_end") or "")[:4]
            key = (
                (row["ticker"], row["dimension"], cal_year, row["metric"])
                if cal_year
                else (row["ticker"], row["dimension"], row["period_end"], row["metric"])
            )
        else:
            fy = row.get("fiscal_year")
            fq = row.get("fiscal_quarter")
            key = (
                (row["ticker"], row["dimension"], fy, fq, row["metric"])
                if fy is not None and fq
                else (row["ticker"], row["dimension"], row["period_end"], row["metric"])
            )

        current = buckets.get(key)
        if current is None:
            buckets[key] = row
            continue
        if annual:
            row_rank = (abs(row.get("value") or 0), row.get("period_end") or "")
            current_rank = (abs(current.get("value") or 0), current.get("period_end") or "")
            if row_rank > current_rank:
                buckets[key] = row
            continue
        row_date = row.get("period_end") or ""
        current_date = current.get("period_end") or ""
        if row_date > current_date:
            buckets[key] = row
        elif row_date == current_date and (row.get("filing_date") or "") > (current.get("filing_date") or ""):
            buckets[key] = row
    return list(buckets.values())


def _statement_metric_count(wide_row: dict) -> int:
    return sum(1 for metric in STATEMENT_METRICS if wide_row.get(metric) is not None)


def _canonical_annual_rank(wide_row: dict) -> tuple:
    return (
        _statement_metric_count(wide_row),
        wide_row.get("calendardate") or "",
        wide_row.get("filingdate") or "",
    )


def filter_canonical_annual_wide_rows(wide_rows: list[dict]) -> list[dict]:
    """Keep full fiscal-year statement columns; drop interim 8-K orphan snapshots."""
    selected: dict[tuple[str, str], dict] = {}

    for row in wide_rows:
        if row.get("revenue") is None:
            continue
        if _statement_metric_count(row) < CANONICAL_ANNUAL_MIN_STATEMENT_METRICS:
            continue
        bucket_key = (row.get("ticker") or "", row.get("calendardate") or "")
        current = selected.get(bucket_key)
        if current is None or _canonical_annual_rank(row) > _canonical_annual_rank(current):
            selected[bucket_key] = row

    filtered = list(selected.values())
    filtered.sort(
        key=lambda item: (item.get("ticker") or "", item.get("calendardate") or ""),
        reverse=True,
    )
    return filtered


def _load_financials_wide_rows(
    narrow_rows: list[dict],
    storage_dimension: str | None,
) -> list[dict]:
    """Apply Research-parity collapse + canonical annual filter for financials history."""
    if storage_dimension == "ARY":
        collapsed = collapse_narrow_fundamentals_rows(narrow_rows, annual=True)
        return pivot_fundamentals_rows(collapsed, canonical_annual=True)
    if storage_dimension == "ARQ":
        collapsed = collapse_narrow_fundamentals_rows(narrow_rows, annual=False)
        return pivot_fundamentals_rows(collapsed, canonical_annual=False)
    return pivot_fundamentals_rows(narrow_rows)


def pivot_fundamentals_rows(rows: list[dict], *, canonical_annual: bool = False) -> list[dict]:
    """Pivot narrow fundamentals into wide-row period layout.

    SEC CompanyFacts uses different period_end dates for shares (DEI snapshot)
    vs statement line items. Group statement metrics by period_end, then attach
    shares by fiscal_year/fiscal_quarter. Deduplicate comparative restatements
    by keeping the latest filing per (ticker, metric, period_end, dimension).
    """
    deduped = _dedupe_narrow_rows(rows)
    shares_rows = [row for row in deduped if row["metric"] == "sharesbas"]
    statement_rows = [row for row in deduped if row["metric"] != "sharesbas"]

    grouped: dict[tuple[str, str, str], dict] = {}
    fiscal_index: dict[tuple, list[tuple[str, str, str]]] = defaultdict(list)

    for row in statement_rows:
        key = (row["ticker"], row["period_end"], row["dimension"])
        wide_row = grouped.setdefault(
            key,
            {
                "ticker": row["ticker"],
                "company_name": row["company_name"],
                "calendardate": row["period_end"],
                "dimension": row["dimension"],
                "filingdate": row["filing_date"],
                "periodtype": row["period_type"],
                "_fiscal_year": row.get("fiscal_year"),
                "_fiscal_quarter": row.get("fiscal_quarter"),
            },
        )
        if (row.get("filing_date") or "") > (wide_row.get("filingdate") or ""):
            wide_row["filingdate"] = row["filing_date"]
        wide_row[row["metric"]] = row["value"]
        if row.get("fiscal_year") is not None:
            wide_row["_fiscal_year"] = row["fiscal_year"]
        if row.get("fiscal_quarter"):
            wide_row["_fiscal_quarter"] = row["fiscal_quarter"]
        fiscal_key = (
            row["ticker"],
            row["dimension"],
            row.get("fiscal_year"),
            row.get("fiscal_quarter"),
        )
        if fiscal_key not in fiscal_index or key not in fiscal_index[fiscal_key]:
            fiscal_index[fiscal_key].append(key)

    for row in shares_rows:
        fiscal_key = (
            row["ticker"],
            row["dimension"],
            row.get("fiscal_year"),
            row.get("fiscal_quarter"),
        )
        candidate_keys = fiscal_index.get(fiscal_key, [])
        if candidate_keys:
            target_key = max(candidate_keys, key=lambda key: key[1])
            wide_row = grouped[target_key]
            if wide_row.get("sharesbas") is None or (row.get("filing_date") or "") >= (wide_row.get("filingdate") or ""):
                wide_row["sharesbas"] = row["value"]
            continue
        key = (row["ticker"], row["period_end"], row["dimension"])
        wide_row = grouped.setdefault(
            key,
            {
                "ticker": row["ticker"],
                "company_name": row["company_name"],
                "calendardate": row["period_end"],
                "dimension": row["dimension"],
                "filingdate": row["filing_date"],
                "periodtype": row["period_type"],
            },
        )
        wide_row["sharesbas"] = row["value"]

    latest_shares_by_ticker_dimension: dict[tuple[str, str], tuple[str, float]] = {}
    for row in shares_rows:
        if not row.get("value"):
            continue
        bucket_key = (row["ticker"], row["dimension"])
        candidate = (row.get("period_end") or "", row["value"])
        existing = latest_shares_by_ticker_dimension.get(bucket_key)
        if existing is None or candidate[0] > existing[0]:
            latest_shares_by_ticker_dimension[bucket_key] = candidate

    wide_rows = []
    for wide_row in grouped.values():
        assets = wide_row.get("assets")
        liabilities = wide_row.get("liabilities")
        if wide_row.get("equity") is None and assets is not None and liabilities is not None:
            wide_row["equity"] = assets - liabilities
        revenue = wide_row.get("revenue")
        cor = wide_row.get("cor")
        if wide_row.get("gp") is None and revenue is not None and cor is not None:
            wide_row["gp"] = revenue - cor
        if wide_row.get("ebit") is None and wide_row.get("opinc") is not None:
            wide_row["ebit"] = wide_row["opinc"]
        if wide_row.get("sharesbas") is None:
            fallback = latest_shares_by_ticker_dimension.get(
                (wide_row["ticker"], wide_row["dimension"])
            )
            if fallback is not None:
                wide_row["sharesbas"] = fallback[1]
        wide_rows.append(wide_row)

    if canonical_annual:
        wide_rows = filter_canonical_annual_wide_rows(wide_rows)

    wide_rows = sorted(
        wide_rows,
        key=lambda item: (item["ticker"], item["calendardate"], item["dimension"]),
        reverse=True,
    )
    for wide_row in wide_rows:
        wide_row.pop("_fiscal_year", None)
        wide_row.pop("_fiscal_quarter", None)
    return wide_rows


def resolve_financial_dimension(
    dimension: str | None,
    most_recent: bool,
) -> dict[str, object]:
    """Map legacy dimension codes (MRY, ARY, MRQ, …) to SQLite query behavior."""
    if not dimension:
        return {
            "storage_dimension": None,
            "ttm_only": False,
            "include_ttm": False,
            "most_recent": most_recent,
        }
    code = dimension.upper()
    if code in {"ARY", "ARQ"}:
        return {
            "storage_dimension": code,
            "ttm_only": False,
            "include_ttm": False,
            "most_recent": most_recent,
        }
    if code == "MRY":
        return {
            "storage_dimension": "MRY",
            "ttm_only": False,
            "include_ttm": False,
            "most_recent": False,
            "legacy_storage_dimension": "ARY",
            "legacy_most_recent": True,
        }
    if code == "MRQ":
        return {
            "storage_dimension": "MRQ",
            "ttm_only": False,
            "include_ttm": False,
            "most_recent": False,
            "legacy_storage_dimension": "ARQ",
            "legacy_most_recent": True,
        }
    if code in {"TTM", "ART"}:
        return {
            "storage_dimension": None,
            "ttm_only": True,
            "include_ttm": True,
            "most_recent": False,
        }
    if code == "MRT":
        return {
            "storage_dimension": "MRT",
            "ttm_only": False,
            "include_ttm": False,
            "most_recent": False,
            "legacy_storage_dimension": None,
            "legacy_ttm_only": True,
            "legacy_most_recent": True,
        }
    return {
        "storage_dimension": code,
        "ttm_only": False,
        "include_ttm": False,
        "most_recent": most_recent,
    }


SNAPSHOT_DIMENSIONS = frozenset({"MRY", "MRQ", "MRT"})


def wide_row_to_fundamental_records(
    company_id: int,
    wide_row: dict,
    *,
    target_dimension: str,
) -> list[dict]:
    """Flatten a pivoted wide row into narrow fundamentals records for snapshot upsert."""
    period_end = wide_row.get("calendardate")
    if not period_end:
        return []
    period_type = wide_row.get("periodtype")
    if not period_type:
        if target_dimension == "MRY":
            period_type = "annual"
        elif target_dimension == "MRQ":
            period_type = "quarterly"
        else:
            period_type = "ttm"
    metric_names = {col["name"] for col in RAW_COLUMNS if col["type"] == "double"}
    records: list[dict] = []
    for metric in metric_names:
        value = wide_row.get(metric)
        if value is None:
            continue
        records.append(
            {
                "company_id": company_id,
                "metric": metric,
                "value": value,
                "unit": "USD" if metric != "eps" else "USD/shares",
                "period_end": period_end,
                "period_type": period_type,
                "dimension": target_dimension,
                "fiscal_year": None,
                "fiscal_quarter": None,
                "filing_date": wide_row.get("filingdate"),
                "form": None,
                "accession": None,
                "source": "fundamentals_snapshot",
                "taxonomy": None,
                "xbrl_concept": f"snapshot_{target_dimension}",
            }
        )
    return records


def fetch_resolved_wide_rows(
    repo: Repository,
    tickers: list[str],
    *,
    gte: str | None,
    resolved: dict[str, object],
) -> list[dict]:
    """Load fundamentals wide rows with snapshot-dimension fallback to legacy ARY/ARQ/TTM compute."""
    storage_dimension = resolved.get("storage_dimension")
    rows = repo.fetch_fundamentals_rows(
        tickers,
        gte=gte,
        dimension=storage_dimension if isinstance(storage_dimension, str) else None,
    )
    dim = storage_dimension if isinstance(storage_dimension, str) else None
    wide_rows = _load_financials_wide_rows(rows, dim)

    if not wide_rows:
        legacy_dim = resolved.get("legacy_storage_dimension")
        if legacy_dim or resolved.get("legacy_ttm_only"):
            source_dim = legacy_dim if isinstance(legacy_dim, str) else "ARQ"
            legacy_rows = repo.fetch_fundamentals_rows(tickers, gte=gte, dimension=source_dim)
            wide_rows = _load_financials_wide_rows(
                legacy_rows,
                source_dim if isinstance(source_dim, str) else None,
            )
            if resolved.get("legacy_ttm_only"):
                wide_rows = compute_ttm_rows(wide_rows)

    if (resolved.get("legacy_most_recent") or resolved.get("most_recent")) and wide_rows:
        latest_by_ticker: dict[str, dict] = {}
        for row in wide_rows:
            current = latest_by_ticker.get(row["ticker"])
            if current is None or (row.get("calendardate") or "") > (current.get("calendardate") or ""):
                latest_by_ticker[row["ticker"]] = row
        return list(latest_by_ticker.values())
    return wide_rows


BALANCE_SHEET_METRICS = frozenset(
    {
        "assets", "assetscurrent", "liabilities", "liabilitiescurrent", "equity",
        "cashneq", "debt", "debtlt", "debtcurrent", "ppnenet", "inventory",
        "receivables", "payables", "retearn", "goodwill", "intangibles",
        "workingcapital", "sharesbas",
    }
)


def compute_ttm_rows(wide_rows: list[dict]) -> list[dict]:
    quarterly_by_ticker: dict[str, list[dict]] = defaultdict(list)
    for row in wide_rows:
        if row.get("periodtype") == "quarterly":
            quarterly_by_ticker[row["ticker"]].append(row)

    ttm_rows = []
    for ticker, quarters in quarterly_by_ticker.items():
        sorted_q = sorted(quarters, key=lambda r: r["calendardate"], reverse=True)[:4]
        if len(sorted_q) < 4:
            continue
        latest = sorted_q[0]
        ttm_row = {
            "ticker": latest["ticker"],
            "company_name": latest.get("company_name"),
            "calendardate": latest["calendardate"],
            "dimension": "TTM",
            "filingdate": latest.get("filingdate"),
            "periodtype": "ttm",
        }
        metric_names = {col["name"] for col in RAW_COLUMNS if col["type"] == "double"}
        for metric in metric_names:
            if metric in BALANCE_SHEET_METRICS:
                ttm_row[metric] = latest.get(metric)
            else:
                values = [q.get(metric) for q in sorted_q]
                if all(v is not None for v in values):
                    ttm_row[metric] = sum(values)
        ttm_rows.append(ttm_row)
    return ttm_rows


class FundamentalsService:
    def __init__(self, repo: Repository, sec_client) -> None:
        self.repo = repo
        self.sec_client = sec_client

    def refresh_company_tickers(self, url: str) -> dict:
        logger.info("refresh_company_tickers start")
        t0 = time.monotonic()
        companies = self.sec_client.fetch_company_tickers(url)
        count = self.repo.upsert_companies(companies)
        logger.info("refresh_company_tickers done companies=%d elapsed=%.1fs", count, time.monotonic() - t0)
        return {"inserted": count}

    def refresh_fundamentals(
        self,
        tickers: list[str],
        *,
        force_refresh: bool = False,
        dry_run: bool = False,
    ) -> dict:
        logger.info(
            "refresh_fundamentals start tickers=%d force_refresh=%s dry_run=%s",
            len(tickers),
            force_refresh,
            dry_run,
        )
        t0 = time.monotonic()
        inserted = 0
        refreshed = []
        skipped: list[dict] = []
        errors: list[dict] = []
        skipped_overwrites = 0
        planned_upserts = 0
        overwrite_samples: list[dict] = []
        from .safe_overwrite import partition_fundamentals_records

        for ticker in [ticker.upper() for ticker in tickers if ticker]:
            company = self.repo.get_company_by_ticker(ticker)
            if not company or not company.get("cik"):
                logger.debug("refresh_fundamentals skip ticker=%s (no CIK)", ticker)
                skipped.append({"ticker": ticker, "reason": "no_cik"})
                continue
            skip_reason = should_skip_sec_fundamentals(company)
            if skip_reason:
                logger.debug("refresh_fundamentals skip ticker=%s (%s)", ticker, skip_reason)
                skipped.append({"ticker": ticker, "reason": skip_reason})
                continue
            try:
                payload = self.sec_client.fetch_company_facts(company["cik"])
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if sec_http_outcome(status) == "skip":
                    logger.debug(
                        "refresh_fundamentals skip ticker=%s cik=%s (SEC HTTP %s)",
                        ticker,
                        company["cik"],
                        status,
                    )
                    skipped.append(
                        {
                            "ticker": ticker,
                            "reason": "no_sec_companyfacts",
                            "status": status,
                        }
                    )
                    continue
                logger.warning(
                    "refresh_fundamentals ticker=%s cik=%s SEC HTTP %s",
                    ticker,
                    company["cik"],
                    status,
                )
                errors.append(
                    {
                        "ticker": ticker,
                        "reason": "sec_http_error",
                        "status": status,
                        "message": str(exc),
                    }
                )
                continue
            except requests.RequestException as exc:
                logger.warning("refresh_fundamentals ticker=%s request failed: %s", ticker, exc)
                errors.append(
                    {"ticker": ticker, "reason": "sec_request_error", "message": str(exc)}
                )
                continue
            records = normalize_company_facts(company["id"], payload)
            stored = self.repo.fetch_fundamentals_overwrite_state(company["id"])
            to_write, skipped_records = partition_fundamentals_records(
                records,
                stored,
                force_refresh=force_refresh,
            )
            skipped_overwrites += len(skipped_records)
            if len(overwrite_samples) < 5 and skipped_records:
                overwrite_samples.extend(skipped_records[: max(0, 5 - len(overwrite_samples))])

            if dry_run:
                planned_upserts += len(to_write)
                refreshed.append(ticker)
                logger.debug(
                    "refresh_fundamentals dry_run ticker=%s planned=%d skipped_overwrites=%d",
                    ticker,
                    len(to_write),
                    len(skipped_records),
                )
                continue

            count = self.repo.upsert_fundamentals(to_write)
            inserted += count
            logger.debug(
                "refresh_fundamentals ticker=%s records=%d skipped_overwrites=%d",
                ticker,
                count,
                len(skipped_records),
            )
            self._refresh_company_scores(company["id"], ticker)
            inserted += self._materialize_snapshot_dimensions(company["id"], ticker)
            try:
                submissions = self.sec_client.fetch_submissions(company["cik"])
                meta = metadata_from_submissions(submissions)
                self.repo.update_company_metadata(ticker, meta)
            except Exception:
                pass
            refreshed.append(ticker)
        elapsed = time.monotonic() - t0
        logger.info(
            "refresh_fundamentals done tickers=%d skipped=%d errors=%d records=%d skipped_overwrites=%d dry_run=%s elapsed=%.1fs",
            len(refreshed),
            len(skipped),
            len(errors),
            inserted if not dry_run else planned_upserts,
            skipped_overwrites,
            dry_run,
            elapsed,
        )
        return {
            "tickers": refreshed,
            "recordsWritten": inserted,
            "plannedUpserts": planned_upserts if dry_run else None,
            "skippedOverwrites": skipped_overwrites,
            "overwriteSamples": overwrite_samples,
            "dryRun": dry_run,
            "forceRefresh": force_refresh,
            "skipped": skipped,
            "errors": errors,
        }

    def _refresh_company_scores(self, company_id: int, ticker: str) -> tuple[int, str]:
        from .freshness import CURRENT_SCORING_VERSION

        rows = collapse_narrow_fundamentals_rows(
            self.repo.fetch_fundamentals_rows([ticker], dimension="ARY"),
            annual=True,
        )
        annual_rows = pivot_fundamentals_rows(rows, canonical_annual=True)
        if not annual_rows:
            return 0, "no_annual_data"
        period_ends = [row.get("calendardate") for row in annual_rows if row.get("calendardate")]
        if self.repo.should_skip_score_recompute(
            company_id,
            period_ends,
            CURRENT_SCORING_VERSION,
        ):
            logger.debug(
                "refresh_company_scores ticker=%s skipped_unchanged periods=%d",
                ticker,
                len(period_ends),
            )
            return 0, "skipped_unchanged"
        prices_by_period = self.repo.fetch_prices_by_period_ends(ticker, period_ends)
        score_records = compute_scores_for_periods(annual_rows, prices_by_period=prices_by_period)
        db_records = [
            {
                "period_end": record["period_end"],
                "dimension": record["dimension"],
                "piotroski_f": record["piotroski_f"],
                "altman_z": record["altman_z"],
                "beneish_m": record["beneish_m"],
                "survivability": record["survivability"],
                "survivability_bucket": record.get("survivability_bucket"),
                "piotroski_components": scores_to_json(record["piotroski_components"]),
                "altman_components": scores_to_json(record["altman_components"]),
                "beneish_components": scores_to_json(record.get("beneish_components")),
            }
            for record in score_records
        ]
        written = self.repo.upsert_company_scores(company_id, db_records)
        logger.debug("refresh_company_scores ticker=%s periods=%d", ticker, written)
        return written, "recomputed"

    def _materialize_snapshot_dimensions(self, company_id: int, ticker: str) -> int:
        """Persist latest MRY/MRQ/MRT snapshot rows for fast dimension queries."""
        self.repo.delete_fundamentals_snapshots(company_id, SNAPSHOT_DIMENSIONS)
        records: list[dict] = []

        annual_rows = pivot_fundamentals_rows(
            collapse_narrow_fundamentals_rows(
                self.repo.fetch_fundamentals_rows([ticker], dimension="ARY"),
                annual=True,
            ),
        )
        if annual_rows:
            canonical_rows = filter_canonical_annual_wide_rows(annual_rows)
            source_rows = canonical_rows or annual_rows
            latest_annual = max(source_rows, key=lambda row: row.get("calendardate") or "")
            records.extend(
                wide_row_to_fundamental_records(company_id, latest_annual, target_dimension="MRY")
            )

        quarterly_rows = pivot_fundamentals_rows(
            self.repo.fetch_fundamentals_rows([ticker], dimension="ARQ")
        )
        if quarterly_rows:
            latest_quarter = max(quarterly_rows, key=lambda row: row.get("calendardate") or "")
            records.extend(
                wide_row_to_fundamental_records(company_id, latest_quarter, target_dimension="MRQ")
            )
            ttm_rows = compute_ttm_rows(quarterly_rows)
            if ttm_rows:
                latest_ttm = max(ttm_rows, key=lambda row: row.get("calendardate") or "")
                mrt_row = {**latest_ttm, "dimension": "MRT", "periodtype": "ttm"}
                records.extend(
                    wide_row_to_fundamental_records(company_id, mrt_row, target_dimension="MRT")
                )

        if not records:
            return 0
        written = self.repo.upsert_fundamentals(records)
        logger.debug("materialize_snapshot_dimensions ticker=%s records=%d", ticker, written)
        return written

    def refresh_company_scores_batch(self, tickers: list[str] | None = None, *, verbose: bool = False) -> dict:
        """Recompute materialized scores for tickers or all companies with annual fundamentals."""
        if tickers:
            target = [item.strip().upper() for item in tickers if item and str(item).strip()]
        else:
            target = self.repo.fetch_tickers_with_fundamentals(dimension="ARY")
        logger.info("refresh_company_scores_batch start tickers=%d", len(target))
        t0 = time.monotonic()
        refreshed: list[str] = []
        skipped: list[dict] = []
        ticker_timings: list[dict] = []
        periods_written = 0
        skipped_unchanged = 0
        for ticker in target:
            company = self.repo.get_company_by_ticker(ticker)
            if not company:
                skipped.append({"ticker": ticker, "reason": "not_found"})
                continue
            t_ticker = time.monotonic()
            written, outcome = self._refresh_company_scores(company["id"], ticker)
            ticker_elapsed = time.monotonic() - t_ticker
            ticker_timings.append(
                {
                    "ticker": ticker,
                    "periods": written,
                    "outcome": outcome,
                    "elapsedSec": round(ticker_elapsed, 3),
                }
            )
            if verbose:
                logger.info(
                    "refresh_company_scores_batch ticker=%s outcome=%s periods=%d elapsed=%.3fs",
                    ticker,
                    outcome,
                    written,
                    ticker_elapsed,
                )
            elif outcome == "skipped_unchanged":
                logger.debug(
                    "refresh_company_scores_batch ticker=%s skipped_unchanged elapsed=%.3fs",
                    ticker,
                    ticker_elapsed,
                )
            else:
                logger.debug(
                    "refresh_company_scores_batch ticker=%s outcome=%s periods=%d elapsed=%.3fs",
                    ticker,
                    outcome,
                    written,
                    ticker_elapsed,
                )
            if outcome == "recomputed" and written:
                refreshed.append(ticker)
                periods_written += written
            elif outcome == "skipped_unchanged":
                skipped_unchanged += 1
            else:
                skipped.append({"ticker": ticker, "reason": outcome})
        elapsed = time.monotonic() - t0
        logger.info(
            "refresh_company_scores_batch done recomputed=%d skipped_unchanged=%d skipped=%d periods=%d elapsed=%.1fs",
            len(refreshed),
            skipped_unchanged,
            len(skipped),
            periods_written,
            elapsed,
        )
        return {
            "tickers": refreshed,
            "recomputed": len(refreshed),
            "skipped_unchanged": skipped_unchanged,
            "periodsWritten": periods_written,
            "skipped": skipped,
            "tickerTimings": ticker_timings,
            "elapsedSec": round(elapsed, 3),
        }

    def enrich_company_metadata(self, tickers: list[str] | None = None, *, all_missing: bool = False) -> dict:
        if all_missing or not tickers:
            tickers = self.repo.fetch_tickers_missing_metadata(limit=500 if all_missing else 100)
        logger.info("enrich_company_metadata start tickers=%d", len(tickers))
        t0 = time.monotonic()
        enriched = []
        skipped = []
        for ticker in [t.upper() for t in tickers if t]:
            company = self.repo.get_company_by_ticker(ticker)
            if not company or not company.get("cik"):
                logger.debug("enrich_company_metadata skip ticker=%s (no CIK)", ticker)
                skipped.append({"ticker": ticker, "reason": "no_cik"})
                continue
            try:
                submissions = self.sec_client.fetch_submissions(company["cik"])
            except Exception:
                logger.debug("enrich_company_metadata fetch failed ticker=%s", ticker)
                skipped.append({"ticker": ticker, "reason": "sec_fetch_failed"})
                continue
            meta = metadata_from_submissions(submissions)
            self.repo.update_company_metadata(ticker, meta)
            enriched.append(ticker)
        elapsed = time.monotonic() - t0
        logger.info("enrich_company_metadata done enriched=%d elapsed=%.1fs", len(enriched), elapsed)
        return {"enriched": len(enriched), "tickers": enriched, "skipped": skipped}

    def get_financials_payload(self, tickers: list[str], gte: str | None, dimension: str | None, most_recent: bool) -> dict:
        resolved = resolve_financial_dimension(dimension, most_recent)
        storage_dimension = resolved["storage_dimension"]
        ttm_only = bool(resolved["ttm_only"])
        include_ttm = bool(resolved["include_ttm"])
        use_most_recent = bool(resolved["most_recent"])

        if dimension:
            wide_rows = fetch_resolved_wide_rows(self.repo, tickers, gte=gte, resolved=resolved)
        else:
            rows = self.repo.fetch_fundamentals_rows(
                tickers,
                gte=gte,
                dimension=storage_dimension if isinstance(storage_dimension, str) else None,
            )
            wide_rows = pivot_fundamentals_rows(rows)

        if not dimension and (include_ttm or not use_most_recent):
            ttm_rows = compute_ttm_rows(wide_rows)
            if ttm_only:
                wide_rows = ttm_rows
            elif include_ttm:
                wide_rows = ttm_rows + wide_rows
            else:
                wide_rows = ttm_rows + wide_rows

        if use_most_recent:
            candidates = wide_rows
            if not storage_dimension and not ttm_only:
                annual_rows = [row for row in wide_rows if row.get("dimension") == "ARY"]
                if annual_rows:
                    candidates = annual_rows
            latest_by_ticker: dict[str, dict] = {}
            for row in candidates:
                current = latest_by_ticker.get(row["ticker"])
                if current is None or (row.get("calendardate") or "") > (current.get("calendardate") or ""):
                    latest_by_ticker[row["ticker"]] = row
            wide_rows = list(latest_by_ticker.values())

        from ..repositories import utc_now_iso

        period_ends_by_ticker: dict[str, list[str]] = {}
        for row in wide_rows:
            ticker = row["ticker"]
            period_end = row.get("calendardate")
            if period_end:
                period_ends_by_ticker.setdefault(ticker, []).append(period_end)

        prices_by_ticker_period: dict[tuple[str, str], float | None] = {}
        for ticker, period_ends in period_ends_by_ticker.items():
            mapped = self.repo.fetch_prices_by_period_ends(ticker, period_ends)
            for period_end, price in mapped.items():
                prices_by_ticker_period[(ticker, period_end)] = price

        latest_prices: dict[str, float] = {}
        for ticker in tickers:
            try:
                price_rows = self.repo.fetch_prices(ticker.upper(), limit=1)
            except Exception:
                continue
            if price_rows:
                latest_prices[ticker.upper()] = price_rows[0]["close"]

        datatable_rows = []
        metrics: dict[str, dict] = {}
        period_series: dict[str, list] = defaultdict(list)
        for row in wide_rows:
            normalized = normalize_fundamentals_row(row)
            ticker = normalized["ticker"]
            period_end = normalized.get("calendardate")
            price = None
            if period_end:
                price = prices_by_ticker_period.get((ticker, period_end))
            if price is None:
                price = latest_prices.get(ticker)
            datatable_rows.append([normalized.get(column["name"]) for column in RAW_COLUMNS])
            period_metrics = build_company_metrics(normalized, price=price)
            period_series[ticker].append({
                "periodEnd": period_end,
                "dimension": normalized.get("dimension"),
                "periodType": normalized.get("periodtype"),
                "fundamentals": {
                    col["name"]: normalized.get(col["name"])
                    for col in RAW_COLUMNS
                    if col["type"] == "double"
                },
                "metrics": period_metrics,
            })
            if ticker not in metrics or (period_end or "") >= (metrics[ticker].get("_periodEnd") or ""):
                metrics[ticker] = {**period_metrics, "_periodEnd": period_end}
        for ticker in list(metrics.keys()):
            metrics[ticker].pop("_periodEnd", None)
            period_series[ticker].sort(
                key=lambda item: item.get("periodEnd") or "",
                reverse=True,
            )
        return {
            "metrics": metrics,
            "periodSeries": period_series,
            "raw": {
                "datatable": {
                    "columns": RAW_COLUMNS,
                    "data": datatable_rows,
                }
            },
            "meta": {
                "source": "sqlite",
                "computedAt": utc_now_iso(),
            },
        }
