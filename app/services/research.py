from __future__ import annotations

import logging

from ..repositories import Repository
from .fundamentals import RAW_COLUMNS, build_company_metrics, pivot_fundamentals_rows, resolve_financial_dimension
from .prices import PricesService
from .scoring import margin_trend_delta, share_dilution_rate, _gross_margin, _operating_margin

logger = logging.getLogger("stock_tracker.research")


class ResearchService:
    def __init__(self, repo: Repository, prices_service: PricesService) -> None:
        self.repo = repo
        self.prices_service = prices_service

    def get_screener(self, tickers: list[str], dimension: str | None = None) -> dict:
        if not tickers:
            return {"tickers": [], "results": {}}

        resolved = resolve_financial_dimension(dimension or "MRY", most_recent=False)
        storage_dimension = resolved["storage_dimension"]
        use_most_recent = bool(resolved["most_recent"])

        rows = self.repo.fetch_fundamentals_rows(
            tickers,
            dimension=storage_dimension if isinstance(storage_dimension, str) else "ARY",
        )
        wide_rows = pivot_fundamentals_rows(rows)

        if use_most_recent:
            latest_by_ticker: dict[str, dict] = {}
            for row in wide_rows:
                current = latest_by_ticker.get(row["ticker"])
                if current is None or (row.get("calendardate") or "") > (current.get("calendardate") or ""):
                    latest_by_ticker[row["ticker"]] = row
            wide_rows = list(latest_by_ticker.values())

        all_annual = pivot_fundamentals_rows(
            self.repo.fetch_fundamentals_rows(tickers, dimension="ARY")
        )
        annual_by_ticker: dict[str, list[dict]] = {}
        for row in all_annual:
            annual_by_ticker.setdefault(row["ticker"], []).append(row)
        for ticker_rows in annual_by_ticker.values():
            ticker_rows.sort(key=lambda r: r.get("calendardate") or "", reverse=True)

        prices_batch = self.repo.fetch_prices_batch(tickers, limit_per_ticker=1)
        market_stats = self.prices_service.get_market_stats(tickers)
        scores_by_ticker = self.repo.fetch_latest_company_scores(tickers, dimension="ARY")
        insider_summary = {
            row["ticker"]: row for row in self.repo.fetch_insider_summary_90d(tickers)
        }

        results: dict[str, dict] = {}
        for row in wide_rows:
            ticker = row["ticker"]
            price_rows = prices_batch.get(ticker, [])
            price = price_rows[0]["close"] if price_rows else None
            metrics = build_company_metrics(row, price=price)
            annual_rows = annual_by_ticker.get(ticker, [])
            prior = annual_rows[1] if len(annual_rows) > 1 else None

            company = self.repo.get_company_by_ticker(ticker)
            results[ticker] = {
                "ticker": ticker,
                "companyName": row.get("company_name") or (company or {}).get("name"),
                "sector": (company or {}).get("sector"),
                "industry": (company or {}).get("industry"),
                "periodEnd": row.get("calendardate"),
                "dimension": row.get("dimension"),
                "fundamentals": {col["name"]: row.get(col["name"]) for col in RAW_COLUMNS if col["type"] == "double"},
                "metrics": metrics,
                "scores": scores_by_ticker.get(ticker),
                "marginTrends": {
                    "grossMargin3yrDelta": margin_trend_delta(annual_rows, 3, _gross_margin),
                    "operatingMargin3yrDelta": margin_trend_delta(annual_rows, 3, _operating_margin),
                },
                "shareDilutionRate": share_dilution_rate(row, prior),
                "insiderSummary": insider_summary.get(ticker),
                "price": {
                    "latest": price,
                    "stats": market_stats.get(ticker, {}),
                },
            }

        for ticker in tickers:
            symbol = ticker.upper()
            if symbol not in results:
                company = self.repo.get_company_by_ticker(symbol)
                results[symbol] = {
                    "ticker": symbol,
                    "companyName": (company or {}).get("name"),
                    "sector": (company or {}).get("sector"),
                    "industry": (company or {}).get("industry"),
                    "error": "no_fundamentals",
                }

        return {"tickers": [t.upper() for t in tickers], "results": results}

    def get_ticker_detail(self, ticker: str) -> dict:
        symbol = ticker.upper()
        company = self.repo.get_company_by_ticker(symbol)
        if not company:
            return {"ticker": symbol, "error": "not_found"}

        annual_rows = pivot_fundamentals_rows(
            self.repo.fetch_fundamentals_rows([symbol], dimension="ARY")
        )
        quarterly_rows = pivot_fundamentals_rows(
            self.repo.fetch_fundamentals_rows([symbol], dimension="ARQ")
        )
        all_rows = sorted(
            annual_rows + quarterly_rows,
            key=lambda r: (r.get("calendardate") or "", r.get("dimension") or ""),
            reverse=True,
        )

        price_rows = self.repo.fetch_prices(symbol, limit=260)
        latest_price = price_rows[0]["close"] if price_rows else None
        market_stats = self.prices_service.get_market_stats([symbol]).get(symbol, {})

        periods = []
        for row in all_rows:
            period_end = row.get("calendardate")
            price_at_period = self._price_near_date(price_rows, period_end)
            metrics = build_company_metrics(row, price=price_at_period or latest_price)
            periods.append(
                {
                    "periodEnd": period_end,
                    "dimension": row.get("dimension"),
                    "periodType": row.get("periodtype"),
                    "filingDate": row.get("filingdate"),
                    "fundamentals": {
                        col["name"]: row.get(col["name"]) for col in RAW_COLUMNS if col["type"] == "double"
                    },
                    "metrics": metrics,
                }
            )

        score_history = self.repo.fetch_company_scores(company["id"], dimension="ARY")
        insiders = self.repo.fetch_insider_transactions(symbol, limit=500)

        return {
            "ticker": symbol,
            "company": company,
            "periods": periods,
            "scoreHistory": score_history,
            "insiders": insiders,
            "price": {
                "latest": latest_price,
                "stats": market_stats,
                "history": list(reversed(price_rows[:252])),
            },
        }

    @staticmethod
    def _price_near_date(price_rows: list[dict], period_end: str | None) -> float | None:
        if not period_end or not price_rows:
            return None
        target = period_end[:10]
        for row in price_rows:
            if (row.get("date") or "") <= target:
                return row.get("close")
        return price_rows[-1].get("close") if price_rows else None
