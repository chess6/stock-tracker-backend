from __future__ import annotations

import logging
import time
from collections import defaultdict

import requests

from ..repositories import Repository
from .company_enrichment import metadata_from_submissions
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


def pivot_fundamentals_rows(rows: list[dict]) -> list[dict]:
    """Pivot narrow fundamentals into SHARADAR-style wide rows.

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
    """Map SHARADAR-style dimension codes to SQLite query behavior."""
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
            "storage_dimension": "ARY",
            "ttm_only": False,
            "include_ttm": False,
            "most_recent": True,
        }
    if code == "MRQ":
        return {
            "storage_dimension": "ARQ",
            "ttm_only": False,
            "include_ttm": False,
            "most_recent": True,
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
            "storage_dimension": None,
            "ttm_only": True,
            "include_ttm": True,
            "most_recent": True,
        }
    return {
        "storage_dimension": code,
        "ttm_only": False,
        "include_ttm": False,
        "most_recent": most_recent,
    }


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


def build_company_metrics(row: dict, price: float | None = None) -> dict:
    shares = row.get("sharesbas")
    revenue = row.get("revenue")
    assets = row.get("assets")
    liabilities = row.get("liabilities")
    equity = row.get("equity")
    ncfo = row.get("ncfo")
    capex = row.get("capex")
    fcf = row.get("fcf")
    netinc = row.get("netinc")
    if fcf is None and ncfo is not None:
        if capex is not None:
            fcf = ncfo - capex
        else:
            # Banks and other filers often omit capex in XBRL; use operating CF as FCF proxy.
            fcf = ncfo
    eps = row.get("eps")
    taxexp = row.get("taxexp")
    interestexp = row.get("interestexp")
    ebitda = row.get("ebitda")
    if ebitda is None:
        ebitda = row.get("opinc") or row.get("ebit")
    if ebitda is None and netinc is not None:
        pretax_proxy = netinc + abs(taxexp or 0)
        ebitda = pretax_proxy + abs(interestexp or 0)
    debt = row.get("debt")
    if debt is None:
        debt_current = row.get("debtcurrent")
        debt_lt = row.get("debtlt")
        if debt_current is not None or debt_lt is not None:
            debt = (debt_current or 0.0) + (debt_lt or 0.0)
    cashneq = row.get("cashneq")
    market_cap = None
    book_value = None
    sales_per_share = None
    cashflow_ops_per_share = None
    sfcf_per_share = None
    ebitda_ev = None
    if shares not in (None, 0):
        if equity is not None:
            book_value = equity / shares
        elif assets is not None and liabilities is not None:
            book_value = (assets - liabilities) / shares
        if revenue is not None:
            sales_per_share = revenue / shares
        if ncfo is not None:
            cashflow_ops_per_share = ncfo / shares
        if fcf is not None:
            sfcf_per_share = fcf / shares
    if shares not in (None, 0) and price is not None:
        market_cap = shares * price
    ncf = row.get("ncf")
    enterprise_value = None
    if market_cap is not None:
        enterprise_value = market_cap + (debt or 0) - (cashneq or 0)
        if enterprise_value <= 0:
            enterprise_value = None
    if ebitda is not None and enterprise_value:
        ebitda_ev = ebitda / enterprise_value
    ncf_per_share = None
    cash_per_share = None
    asset_per_share = None
    rev_debt = None
    mc_ev = None
    if shares not in (None, 0):
        if ncf is not None:
            ncf_per_share = ncf / shares
        if cashneq is not None:
            cash_per_share = cashneq / shares
        if assets is not None:
            asset_per_share = assets / shares
    if revenue is not None and debt not in (None, 0):
        rev_debt = revenue / debt
    if market_cap is not None and enterprise_value:
        mc_ev = market_cap / enterprise_value
    pe = None
    if eps not in (None, 0) and price is not None:
        pe = price / eps
    roe = None
    if netinc is not None and equity not in (None, 0):
        roe = netinc / equity
    roa = None
    if netinc is not None and assets not in (None, 0):
        roa = netinc / assets
    gp = row.get("gp")
    gross_margin = None
    if gp is not None and revenue not in (None, 0):
        gross_margin = gp / revenue
    net_margin = None
    if netinc is not None and revenue not in (None, 0):
        net_margin = netinc / revenue
    de = None
    if debt is not None and equity not in (None, 0):
        de = debt / equity
    current_ratio = None
    assets_current = row.get("assetscurrent")
    liabilities_current = row.get("liabilitiescurrent")
    if assets_current is not None and liabilities_current not in (None, 0):
        current_ratio = assets_current / liabilities_current
    div_yield = None
    ncfdiv = row.get("ncfdiv")
    if ncfdiv is not None and shares not in (None, 0) and price not in (None, 0):
        dps = abs(ncfdiv) / shares
        div_yield = dps / price
    return {
        "marketCap": market_cap,
        "revenue": revenue,
        "sp": sales_per_share,
        "ebitdaEv": ebitda_ev,
        "tbp": book_value,
        "bp": book_value,
        "ep": eps,
        "cfop": cashflow_ops_per_share,
        "sfcfp": sfcf_per_share,
        "ncfp": ncf_per_share,
        "cashp": cash_per_share,
        "assetp": asset_per_share,
        "revDebt": rev_debt,
        "mcEv": mc_ev,
        "pe": pe,
        "roe": roe,
        "roa": roa,
        "grossMargin": gross_margin,
        "netMargin": net_margin,
        "de": de,
        "currentRatio": current_ratio,
        "divYield": div_yield,
    }


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

    def refresh_fundamentals(self, tickers: list[str]) -> dict:
        logger.info("refresh_fundamentals start tickers=%d", len(tickers))
        t0 = time.monotonic()
        inserted = 0
        refreshed = []
        skipped: list[dict] = []
        errors: list[dict] = []
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
            count = self.repo.upsert_fundamentals(records)
            inserted += count
            logger.debug("refresh_fundamentals ticker=%s records=%d", ticker, count)
            self._refresh_company_scores(company["id"], ticker)
            try:
                submissions = self.sec_client.fetch_submissions(company["cik"])
                meta = metadata_from_submissions(submissions)
                self.repo.update_company_metadata(ticker, meta)
            except Exception:
                pass
            refreshed.append(ticker)
        elapsed = time.monotonic() - t0
        logger.info(
            "refresh_fundamentals done tickers=%d skipped=%d errors=%d records=%d elapsed=%.1fs",
            len(refreshed),
            len(skipped),
            len(errors),
            inserted,
            elapsed,
        )
        return {
            "tickers": refreshed,
            "recordsWritten": inserted,
            "skipped": skipped,
            "errors": errors,
        }

    def _refresh_company_scores(self, company_id: int, ticker: str) -> int:
        rows = self.repo.fetch_fundamentals_rows([ticker], dimension="ARY")
        annual_rows = pivot_fundamentals_rows(rows)
        if not annual_rows:
            return 0
        prices_by_period: dict[str, float | None] = {}
        for row in annual_rows:
            period_end = row.get("calendardate")
            if period_end:
                prices_by_period[period_end] = self.repo.fetch_price_near_date(ticker, period_end)
        score_records = compute_scores_for_periods(annual_rows, prices_by_period=prices_by_period)
        db_records = [
            {
                "period_end": record["period_end"],
                "dimension": record["dimension"],
                "piotroski_f": record["piotroski_f"],
                "altman_z": record["altman_z"],
                "beneish_m": record["beneish_m"],
                "survivability": record["survivability"],
                "piotroski_components": scores_to_json(record["piotroski_components"]),
                "altman_components": scores_to_json(record["altman_components"]),
            }
            for record in score_records
        ]
        written = self.repo.upsert_company_scores(company_id, db_records)
        logger.debug("refresh_company_scores ticker=%s periods=%d", ticker, written)
        return written

    def refresh_company_scores_batch(self, tickers: list[str] | None = None) -> dict:
        """Recompute materialized scores for tickers or all companies with annual fundamentals."""
        if tickers:
            target = [item.strip().upper() for item in tickers if item and str(item).strip()]
        else:
            target = self.repo.fetch_tickers_with_fundamentals(dimension="ARY")
        logger.info("refresh_company_scores_batch start tickers=%d", len(target))
        t0 = time.monotonic()
        refreshed: list[str] = []
        skipped: list[dict] = []
        periods_written = 0
        for ticker in target:
            company = self.repo.get_company_by_ticker(ticker)
            if not company:
                skipped.append({"ticker": ticker, "reason": "not_found"})
                continue
            written = self._refresh_company_scores(company["id"], ticker)
            if written:
                refreshed.append(ticker)
                periods_written += written
            else:
                skipped.append({"ticker": ticker, "reason": "no_annual_data"})
        elapsed = time.monotonic() - t0
        logger.info(
            "refresh_company_scores_batch done tickers=%d skipped=%d periods=%d elapsed=%.1fs",
            len(refreshed),
            len(skipped),
            periods_written,
            elapsed,
        )
        return {
            "tickers": refreshed,
            "periodsWritten": periods_written,
            "skipped": skipped,
        }

    def enrich_company_metadata(self, tickers: list[str]) -> dict:
        logger.info("enrich_company_metadata start tickers=%d", len(tickers))
        t0 = time.monotonic()
        enriched = []
        for ticker in [t.upper() for t in tickers if t]:
            company = self.repo.get_company_by_ticker(ticker)
            if not company or not company.get("cik"):
                logger.debug("enrich_company_metadata skip ticker=%s (no CIK)", ticker)
                continue
            try:
                submissions = self.sec_client.fetch_submissions(company["cik"])
            except Exception:
                logger.debug("enrich_company_metadata fetch failed ticker=%s", ticker)
                continue
            meta = metadata_from_submissions(submissions)
            self.repo.update_company_metadata(ticker, meta)
            enriched.append(ticker)
        elapsed = time.monotonic() - t0
        logger.info("enrich_company_metadata done enriched=%d elapsed=%.1fs", len(enriched), elapsed)
        return {"enriched": len(enriched), "tickers": enriched}

    def get_financials_payload(self, tickers: list[str], gte: str | None, dimension: str | None, most_recent: bool) -> dict:
        resolved = resolve_financial_dimension(dimension, most_recent)
        storage_dimension = resolved["storage_dimension"]
        ttm_only = bool(resolved["ttm_only"])
        include_ttm = bool(resolved["include_ttm"])
        use_most_recent = bool(resolved["most_recent"])

        rows = self.repo.fetch_fundamentals_rows(
            tickers,
            gte=gte,
            dimension=storage_dimension if isinstance(storage_dimension, str) else None,
        )
        wide_rows = pivot_fundamentals_rows(rows)

        if include_ttm or (not dimension and not use_most_recent):
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

        prices_by_ticker: dict[str, float] = {}
        for ticker in tickers:
            try:
                price_rows = self.repo.fetch_prices(ticker.upper(), limit=1)
            except Exception:
                continue
            if price_rows:
                prices_by_ticker[ticker.upper()] = price_rows[0]["close"]
        datatable_rows = [
            [row.get(column["name"]) for column in RAW_COLUMNS]
            for row in wide_rows
        ]
        metrics = {
            row["ticker"]: build_company_metrics(row, price=prices_by_ticker.get(row["ticker"]))
            for row in wide_rows
        }
        return {
            "metrics": metrics,
            "raw": {
                "datatable": {
                    "columns": RAW_COLUMNS,
                    "data": datatable_rows,
                }
            },
        }
