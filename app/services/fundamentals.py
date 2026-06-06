from __future__ import annotations

from collections import defaultdict

from ..repositories import Repository
from .sec import normalize_company_facts


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
    {"name": "eps", "type": "double"},
    {"name": "taxexp", "type": "double"},
    {"name": "assets", "type": "double"},
    {"name": "liabilities", "type": "double"},
    {"name": "equity", "type": "double"},
    {"name": "cashneq", "type": "double"},
    {"name": "debt", "type": "double"},
    {"name": "ppnenet", "type": "double"},
    {"name": "inventory", "type": "double"},
    {"name": "receivables", "type": "double"},
    {"name": "payables", "type": "double"},
    {"name": "workingcapital", "type": "double"},
    {"name": "ncfo", "type": "double"},
    {"name": "capex", "type": "double"},
    {"name": "fcf", "type": "double"},
    {"name": "ncfi", "type": "double"},
    {"name": "ncff", "type": "double"},
    {"name": "ncfdiv", "type": "double"},
    {"name": "ncfdebt", "type": "double"},
    {"name": "ncf", "type": "double"},
    {"name": "sharesbas", "type": "double"},
]

STATEMENT_METRICS = frozenset(
    {
        "revenue", "cor", "gp", "opex", "sgna", "rnd", "opinc", "ebit", "ebitda",
        "netinc", "eps", "taxexp", "assets", "liabilities", "equity", "cashneq", "debt",
        "ppnenet", "inventory", "receivables", "payables", "workingcapital",
        "ncfo", "capex", "fcf", "ncfi", "ncff", "ncfdiv", "ncfdebt", "ncf",
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
        wide_row.pop("_fiscal_year", None)
        wide_row.pop("_fiscal_quarter", None)
        wide_rows.append(wide_row)

    return sorted(
        wide_rows,
        key=lambda item: (item["ticker"], item["calendardate"], item["dimension"]),
        reverse=True,
    )


def build_company_metrics(row: dict, price: float | None = None) -> dict:
    shares = row.get("sharesbas")
    revenue = row.get("revenue")
    assets = row.get("assets")
    liabilities = row.get("liabilities")
    equity = row.get("equity")
    ncfo = row.get("ncfo")
    capex = row.get("capex")
    fcf = row.get("fcf")
    if fcf is None and ncfo is not None and capex is not None:
        fcf = ncfo - capex
    eps = row.get("eps")
    ebitda = row.get("ebitda")
    if ebitda is None:
        ebitda = row.get("opinc") or row.get("ebit")
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
    if ebitda is not None and market_cap is not None:
        enterprise_value = market_cap + (debt or 0) - (cashneq or 0)
        if enterprise_value > 0:
            ebitda_ev = ebitda / enterprise_value
    return {
        "marketCap": market_cap,
        "sp": sales_per_share,
        "ebitdaEv": ebitda_ev,
        "tbp": book_value,
        "bp": book_value,
        "ep": eps,
        "cfop": cashflow_ops_per_share,
        "sfcfp": sfcf_per_share,
    }


class FundamentalsService:
    def __init__(self, repo: Repository, sec_client) -> None:
        self.repo = repo
        self.sec_client = sec_client

    def refresh_company_tickers(self, url: str) -> dict:
        companies = self.sec_client.fetch_company_tickers(url)
        count = self.repo.upsert_companies(companies)
        return {"inserted": count}

    def refresh_fundamentals(self, tickers: list[str]) -> dict:
        inserted = 0
        refreshed = []
        for ticker in [ticker.upper() for ticker in tickers if ticker]:
            company = self.repo.get_company_by_ticker(ticker)
            if not company or not company.get("cik"):
                continue
            payload = self.sec_client.fetch_company_facts(company["cik"])
            records = normalize_company_facts(company["id"], payload)
            inserted += self.repo.upsert_fundamentals(records)
            refreshed.append(ticker)
        return {"tickers": refreshed, "recordsWritten": inserted}

    def get_financials_payload(self, tickers: list[str], gte: str | None, dimension: str | None, most_recent: bool) -> dict:
        rows = self.repo.fetch_fundamentals_rows(tickers, gte=gte, dimension=dimension)
        wide_rows = pivot_fundamentals_rows(rows)
        if most_recent:
            candidates = wide_rows
            if not dimension:
                annual_rows = [row for row in wide_rows if row.get("dimension") == "ARY"]
                if annual_rows:
                    candidates = annual_rows
            latest_by_ticker = {}
            for row in candidates:
                latest_by_ticker.setdefault(row["ticker"], row)
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
