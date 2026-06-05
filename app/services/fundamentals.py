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
    {"name": "eps", "type": "double"},
    {"name": "assets", "type": "double"},
    {"name": "liabilities", "type": "double"},
    {"name": "cashneq", "type": "double"},
    {"name": "ncfo", "type": "double"},
    {"name": "capex", "type": "double"},
    {"name": "fcf", "type": "double"},
    {"name": "sharesbas", "type": "double"},
    {"name": "netinc", "type": "double"},
]


def build_company_metrics(row: dict) -> dict:
    shares = row.get("sharesbas")
    revenue = row.get("revenue")
    assets = row.get("assets")
    liabilities = row.get("liabilities")
    ncfo = row.get("ncfo")
    fcf = row.get("fcf")
    eps = row.get("eps")
    market_cap = None
    book_value = None
    sales_per_share = None
    cashflow_ops_per_share = None
    sfcf_per_share = None
    if shares not in (None, 0):
        if assets is not None and liabilities is not None:
            book_value = (assets - liabilities) / shares
        if revenue is not None:
            sales_per_share = revenue / shares
        if ncfo is not None:
            cashflow_ops_per_share = ncfo / shares
        if fcf is not None:
            sfcf_per_share = fcf / shares
    if shares not in (None, 0) and eps is not None:
        market_cap = shares * eps
    return {
        "marketCap": market_cap,
        "sp": sales_per_share,
        "ebitdaEv": None,
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
        grouped: dict[tuple[str, str, str], dict] = {}
        for row in rows:
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
            wide_row[row["metric"]] = row["value"]

        wide_rows = sorted(
            grouped.values(),
            key=lambda item: (item["ticker"], item["calendardate"], item["dimension"]),
            reverse=True,
        )
        if most_recent:
            latest_by_ticker = {}
            for row in wide_rows:
                latest_by_ticker.setdefault(row["ticker"], row)
            wide_rows = list(latest_by_ticker.values())

        datatable_rows = [
            [row.get(column["name"]) for column in RAW_COLUMNS]
            for row in wide_rows
        ]
        metrics = {row["ticker"]: build_company_metrics(row) for row in wide_rows}
        return {
            "metrics": metrics,
            "raw": {
                "datatable": {
                    "columns": RAW_COLUMNS,
                    "data": datatable_rows,
                }
            },
        }
