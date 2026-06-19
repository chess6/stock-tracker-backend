from __future__ import annotations

import logging

from ..repositories import Repository
from .fundamentals import (
    RAW_COLUMNS,
    SNAPSHOT_DIMENSIONS,
    build_company_metrics,
    collapse_narrow_fundamentals_rows,
    compute_ttm_rows,
    fetch_resolved_wide_rows,
    normalize_fundamentals_row,
    pivot_fundamentals_rows,
    resolve_financial_dimension,
)
from .prices import PricesService
from .metric_registry import METRIC_REGISTRY
from .metric_trends import build_metric_trends
from .scoring import margin_trend_delta, share_dilution_rate, _gross_margin, _operating_margin
from .insider_analysis import (
    analyze_insider_activity,
    cluster_records_for_storage,
    detect_clusters,
    format_transaction,
)
from .narrative import build_narrative_analysis

logger = logging.getLogger("stock_tracker.research")


class ResearchService:
    def __init__(self, repo: Repository, prices_service: PricesService) -> None:
        self.repo = repo
        self.prices_service = prices_service

    def get_screener(self, tickers: list[str], dimension: str | None = None) -> dict:
        if not tickers:
            return {"tickers": [], "results": {}}

        resolved = resolve_financial_dimension(dimension or "MRY", most_recent=False)
        wide_rows = fetch_resolved_wide_rows(self.repo, tickers, gte=None, resolved=resolved)

        all_annual = pivot_fundamentals_rows(
            collapse_narrow_fundamentals_rows(
                self.repo.fetch_fundamentals_rows(tickers, dimension="ARY"),
                annual=True,
            ),
            canonical_annual=True,
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
        self._enrich_insider_summaries(insider_summary, tickers)
        narrative_snapshots = self.repo.fetch_latest_narrative_snapshots(tickers)

        results: dict[str, dict] = {}
        for row in wide_rows:
            ticker = row["ticker"]
            price_rows = prices_batch.get(ticker, [])
            price = price_rows[0]["close"] if price_rows else None
            metrics = build_company_metrics(row, price=price)
            annual_rows = annual_by_ticker.get(ticker, [])
            prior = annual_rows[1] if len(annual_rows) > 1 else None

            company = self.repo.get_company_by_ticker(ticker)
            narrative_snap = narrative_snapshots.get(ticker)
            narrative_divergence = None
            if narrative_snap:
                narrative_divergence = {
                    "signal": narrative_snap.get("divergence_signal"),
                    "divergenceScore": narrative_snap.get("divergence_score"),
                }
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
                "narrativeDivergence": narrative_divergence,
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

    def get_ticker_detail(
        self,
        ticker: str,
        dimension: str | None = None,
        gte: str | None = None,
    ) -> dict:
        symbol = ticker.upper()
        company = self.repo.get_company_by_ticker(symbol)
        if not company:
            return {"ticker": symbol, "error": "not_found"}

        resolved = resolve_financial_dimension(dimension or "MRY", most_recent=False)
        storage_dimension = resolved["storage_dimension"]
        ttm_only = bool(resolved["ttm_only"])
        include_ttm = bool(resolved["include_ttm"])
        dimension_code = (dimension or "MRY").upper()
        collapse_annual = dimension_code in {"MRY", "ARY"}
        collapse_quarterly = dimension_code in {"MRQ", "ARQ"}

        def load_wide_rows(tickers: list[str], *, storage_dim: str | None, annual: bool | None) -> list[dict]:
            narrow_rows = self.repo.fetch_fundamentals_rows(tickers, gte=gte, dimension=storage_dim)
            if annual is True:
                narrow_rows = collapse_narrow_fundamentals_rows(narrow_rows, annual=True)
            elif annual is False:
                narrow_rows = collapse_narrow_fundamentals_rows(narrow_rows, annual=False)
            return pivot_fundamentals_rows(
                narrow_rows,
                canonical_annual=True if annual is True else False,
            )

        if isinstance(storage_dimension, str) and storage_dimension in SNAPSHOT_DIMENSIONS:
            all_rows = fetch_resolved_wide_rows(self.repo, [symbol], gte=gte, resolved=resolved)
        elif ttm_only or include_ttm:
            quarterly_rows = load_wide_rows([symbol], storage_dim="ARQ", annual=False)
            ttm_rows = compute_ttm_rows(quarterly_rows)
            all_rows = ttm_rows if ttm_only else ttm_rows + quarterly_rows
        elif isinstance(storage_dimension, str):
            all_rows = load_wide_rows(
                [symbol],
                storage_dim=storage_dimension,
                annual=True if collapse_annual else False if collapse_quarterly else None,
            )
        else:
            all_rows = load_wide_rows([symbol], storage_dim="ARY", annual=True) + load_wide_rows(
                [symbol], storage_dim="ARQ", annual=False
            )

        all_rows = sorted(
            all_rows,
            key=lambda r: (r.get("calendardate") or "", r.get("dimension") or ""),
            reverse=True,
        )

        price_rows = self.repo.fetch_prices(symbol, limit=260)
        latest_price = price_rows[0]["close"] if price_rows else None
        market_stats = self.prices_service.get_market_stats([symbol]).get(symbol, {})

        periods = []
        for row in all_rows:
            row = normalize_fundamentals_row(row)
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
        insider_detail = self.get_insider_detail(symbol)

        trend_keys = [
            meta["api_key"]
            for meta in METRIC_REGISTRY.values()
            if meta.get("api_key") and meta.get("trend_capable")
        ]
        metric_trends = build_metric_trends(periods, trend_keys)

        return {
            "ticker": symbol,
            "company": company,
            "dimension": (dimension or "MRY").upper(),
            "periods": periods,
            "metricTrends": metric_trends,
            "scoreHistory": score_history,
            "insiders": insiders,
            "insiderAnalysis": insider_detail,
            "price": {
                "latest": latest_price,
                "stats": market_stats,
                "history": list(reversed(price_rows[:252])),
            },
        }

    def get_insider_detail(self, ticker: str) -> dict:
        symbol = ticker.upper()
        company = self.repo.get_company_by_ticker(symbol)
        if not company:
            return {"ticker": symbol, "error": "not_found"}

        raw_transactions = self.repo.fetch_insider_transactions_raw(company["id"])
        summary = analyze_insider_activity(raw_transactions)
        stored_clusters = self.repo.fetch_insider_clusters_for_company(company["id"])
        clusters = stored_clusters if stored_clusters else detect_clusters(raw_transactions)
        recent = [format_transaction(txn) for txn in raw_transactions[:100]]

        return {
            "ticker": symbol,
            "companyName": company.get("name"),
            "summary": summary,
            "clusters": clusters,
            "recentTransactions": recent,
        }

    def get_insider_clusters(
        self,
        tickers: list[str] | None = None,
        *,
        limit: int = 50,
        min_buy_value: float | None = None,
    ) -> dict:
        clusters = self.repo.fetch_insider_cluster_rankings(
            tickers,
            limit=limit,
            min_buy_value=min_buy_value,
        )
        if clusters or not tickers:
            return {"clusters": clusters}

        # Fallback: compute on-the-fly when materialized data is empty
        computed: list[dict] = []
        for symbol in tickers:
            detail = self.get_insider_detail(symbol)
            if detail.get("error"):
                continue
            for cluster in detail.get("clusters") or []:
                computed.append(
                    {
                        **cluster,
                        "ticker": symbol,
                        "companyName": detail.get("companyName"),
                    }
                )
        computed.sort(
            key=lambda c: (c.get("intensityScore") or 0, c.get("totalBuyValue") or 0),
            reverse=True,
        )
        return {"clusters": computed[:limit]}

    def get_narrative(self, ticker: str) -> dict:
        return build_narrative_analysis(self.repo, ticker)

    def refresh_insider_clusters(self, company_id: int) -> int:
        raw_transactions = self.repo.fetch_insider_transactions_raw(company_id)
        records = cluster_records_for_storage(company_id, raw_transactions)
        written = self.repo.upsert_insider_cluster_analysis(company_id, records)
        logger.debug("refresh_insider_clusters company_id=%s clusters=%d", company_id, written)
        return written

    def _enrich_insider_summaries(self, insider_summary: dict[str, dict], tickers: list[str]) -> None:
        ticker_company_ids: dict[str, int] = {}
        for symbol in tickers:
            key = symbol.upper()
            if key not in insider_summary:
                continue
            company = self.repo.get_company_by_ticker(key)
            if company:
                ticker_company_ids[key] = int(company["id"])

        if not ticker_company_ids:
            return

        raw_by_company = self.repo.fetch_insider_transactions_raw_batch(
            list(ticker_company_ids.values()),
            limit_per_company=500,
        )
        company_to_ticker = {company_id: ticker for ticker, company_id in ticker_company_ids.items()}
        for company_id, raw in raw_by_company.items():
            ticker = company_to_ticker.get(company_id)
            base = insider_summary.get(ticker or "")
            if not base:
                continue
            analysis = analyze_insider_activity(raw)
            base["uniqueBuyers90d"] = analysis["uniqueBuyers90d"]
            base["intensityScore90d"] = analysis["intensityScore90d"]
            base["totalBuyValue90d"] = analysis["totalBuyValue90d"]
            base["totalSellValue90d"] = analysis["totalSellValue90d"]

    @staticmethod
    def _price_near_date(price_rows: list[dict], period_end: str | None) -> float | None:
        if not period_end or not price_rows:
            return None
        target = period_end[:10]
        for row in price_rows:
            if (row.get("date") or "") <= target:
                return row.get("close")
        return price_rows[-1].get("close") if price_rows else None
