"""Phase 0 latent computations for thesis engine — derived from existing DB data."""

from __future__ import annotations

from typing import Any

from ..repositories import Repository
from .fundamentals import collapse_narrow_fundamentals_rows, fetch_resolved_wide_rows, pivot_fundamentals_rows, resolve_financial_dimension
from .metric_primitives import (
    capital_allocation_track_record,
    conservative_nav_components,
    conservative_nav_per_share,
    conservative_nav_total,
    peer_industry_secular_trend,
    price_to_conservative_nav,
    quarterly_cash_runway_months,
    sloan_accruals,
    time_cheap_persistence,
)
from .metrics_engine import compute_period_metrics
from .sector_stats import build_sector_stats


def _annual_history_with_metrics(
    repo: Repository,
    ticker: str,
    prices_by_period: dict[str, float],
) -> list[dict]:
    narrow = collapse_narrow_fundamentals_rows(
        repo.fetch_fundamentals_rows([ticker], dimension="ARY"),
        annual=True,
    )
    wide = pivot_fundamentals_rows(narrow, canonical_annual=True)
    wide.sort(key=lambda r: r.get("calendardate") or "", reverse=True)
    history: list[dict] = []
    for row in wide:
        period = row.get("calendardate") or ""
        price = prices_by_period.get(period)
        metrics = compute_period_metrics(row, price=price)
        history.append({**row, **metrics})
    return history


def _quarterly_latest_row(repo: Repository, ticker: str) -> dict | None:
    resolved = resolve_financial_dimension("MRQ", most_recent=False)
    rows = fetch_resolved_wide_rows(repo, [ticker], gte=None, resolved=resolved)
    if not rows:
        resolved = resolve_financial_dimension("ARQ", most_recent=False)
        rows = fetch_resolved_wide_rows(repo, [ticker], gte=None, resolved=resolved)
    return rows[0] if rows else None


def _sector_medians(repo: Repository, sector: str | None) -> dict[str, float | None]:
    if not sector:
        return {"pe": None, "pb": None, "earnings_yield": None}
    stats = build_sector_stats(repo, sectors=[sector], metric_api_keys=["pe", "pb", "earningsYield"])
    sector_stats = (stats.get("bySector") or {}).get(sector) or {}
    pe_bp = sector_stats.get("pe") or {}
    pb_bp = sector_stats.get("pb") or {}
    ey_bp = sector_stats.get("earningsYield") or {}
    return {
        "pe": pe_bp.get("p40") or pe_bp.get("p60"),
        "pb": pb_bp.get("p40") or pb_bp.get("p60"),
        "earnings_yield": ey_bp.get("p60") or ey_bp.get("p80"),
    }


def _peer_annual_histories(repo: Repository, sector: str | None, *, exclude_ticker: str, limit: int = 80) -> list[list[dict]]:
    if not sector:
        return []
    peer_tickers = [
        t for t in repo.fetch_sector_tickers(sector, limit=limit) if t.upper() != exclude_ticker.upper()
    ]
    if not peer_tickers:
        return []
    narrow = collapse_narrow_fundamentals_rows(
        repo.fetch_fundamentals_rows(peer_tickers, dimension="ARY"),
        annual=True,
    )
    by_ticker: dict[str, list[dict]] = {}
    for row in narrow:
        ticker = row.get("ticker")
        if not ticker:
            continue
        by_ticker.setdefault(ticker, []).append(row)
    histories: list[list[dict]] = []
    for peer_rows in by_ticker.values():
        wide = pivot_fundamentals_rows(peer_rows, canonical_annual=True)
        if len(wide) >= 2:
            histories.append(wide)
    return histories


def compute_latent_metrics(
    repo: Repository,
    ticker: str,
    *,
    row: dict | None = None,
    price: float | None = None,
    sector: str | None = None,
) -> dict[str, Any]:
    """
    Compute all six Phase 0 latent metrics for one ticker.
    Returns canonical snake_case keys plus nested raw evidence payloads.
    """
    if row is None:
        resolved = resolve_financial_dimension("MRY", most_recent=False)
        rows = fetch_resolved_wide_rows(repo, [ticker], gte=None, resolved=resolved)
        row = rows[0] if rows else None
    if not row:
        return {}

    prices = repo.fetch_prices_by_period_ends(ticker, [r.get("calendardate") for r in repo.fetch_fundamentals_rows([ticker], dimension="ARY")[:10]])
    annual_history = _annual_history_with_metrics(repo, ticker, prices)
    prior_annual = annual_history[1] if len(annual_history) > 1 else None
    quarterly_row = _quarterly_latest_row(repo, ticker)

    nav_total = conservative_nav_total(row)
    nav_per_share = conservative_nav_per_share(row)
    p_to_nav = price_to_conservative_nav(price, nav_per_share)

    sector_medians = _sector_medians(repo, sector)
    time_cheap = time_cheap_persistence(
        annual_history,
        sector_pe_median=sector_medians["pe"],
        sector_pb_median=sector_medians["pb"],
        sector_ey_median=sector_medians["earnings_yield"],
    )

    peer_histories = _peer_annual_histories(repo, sector, exclude_ticker=ticker)
    peer_trend = peer_industry_secular_trend(peer_histories)

    runway = quarterly_cash_runway_months(quarterly_row) if quarterly_row else None

    cap_alloc = capital_allocation_track_record(annual_history, prices_by_period=prices)
    sloan = sloan_accruals(row, prior_annual)

    return {
        "conservative_nav_per_share": nav_per_share,
        "price_to_conservative_nav": p_to_nav,
        "conservative_nav_total": nav_total,
        "time_cheap_periods": time_cheap.get("consecutive_periods"),
        "time_cheap_classification": time_cheap.get("classification"),
        "peer_industry_revenue_cagr_3yr": peer_trend.get("median_revenue_cagr_3yr"),
        "peer_industry_margin_delta_3yr": peer_trend.get("median_gross_margin_delta_3yr"),
        "peer_industry_declining": peer_trend.get("peer_declining"),
        "runway_months": runway,
        "capital_allocation_score": cap_alloc.get("score"),
        "sloan_accruals": sloan,
        "raw": {
            "conservative_nav": {
                "navTotal": nav_total,
                "navPerShare": nav_per_share,
                "priceToNav": p_to_nav,
                "components": conservative_nav_components(row),
            },
            "time_cheap": time_cheap,
            "peer_industry_trend": peer_trend,
            "runway": {
                "cashNeq": (quarterly_row or {}).get("cashneq"),
                "quarterlyFcf": (quarterly_row or {}).get("fcf"),
                "runwayMonths": runway,
                "periodEnd": (quarterly_row or {}).get("calendardate"),
            },
            "capital_allocation": cap_alloc,
            "sloan_accruals": {
                "value": sloan,
                "netInc": row.get("netinc"),
                "ncfo": row.get("ncfo"),
                "assets": row.get("assets"),
                "priorAssets": (prior_annual or {}).get("assets"),
            },
        },
    }


def latent_metrics_to_api(payload: dict[str, Any]) -> dict[str, Any]:
    """Map latent metrics to camelCase API keys for screener/research consumers."""
    if not payload:
        return {}
    return {
        "conservativeNavPerShare": payload.get("conservative_nav_per_share"),
        "priceToConservativeNav": payload.get("price_to_conservative_nav"),
        "conservativeNavTotal": payload.get("conservative_nav_total"),
        "timeCheapPeriods": payload.get("time_cheap_periods"),
        "timeCheapClassification": payload.get("time_cheap_classification"),
        "peerIndustryRevenueCagr3yr": payload.get("peer_industry_revenue_cagr_3yr"),
        "peerIndustryMarginDelta3yr": payload.get("peer_industry_margin_delta_3yr"),
        "peerIndustryDeclining": payload.get("peer_industry_declining"),
        "runwayMonths": payload.get("runway_months"),
        "capitalAllocationScore": payload.get("capital_allocation_score"),
        "sloanAccruals": payload.get("sloan_accruals"),
        "raw": payload.get("raw"),
    }
