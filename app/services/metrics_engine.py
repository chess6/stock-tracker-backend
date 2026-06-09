"""Canonical per-period financial metrics computation."""

from __future__ import annotations

from .metric_primitives import (
    asset_turnover,
    book_value_per_share,
    cash_to_debt,
    cfo_margin,
    current_ratio,
    debt_equity,
    ebitda_margin,
    fcf_margin,
    free_cash_flow,
    gross_margin,
    interest_coverage,
    leverage,
    net_margin,
    operating_margin,
    quick_ratio,
    resolve_ebitda,
    roa,
    roe,
    safe_div,
    total_debt,
)


def compute_period_metrics(row: dict, price: float | None = None) -> dict[str, float | None]:
    """Compute canonical snake_case metrics for one fundamentals wide row."""
    shares = row.get("sharesbas")
    revenue = row.get("revenue")
    assets = row.get("assets")
    liabilities = row.get("liabilities")
    equity = row.get("equity")
    ncfo = row.get("ncfo")
    fcf = free_cash_flow(row)
    netinc = row.get("netinc")
    eps = row.get("eps")
    ebitda = resolve_ebitda(row)
    debt = total_debt(row)
    cashneq = row.get("cashneq")
    ncf = row.get("ncf")
    ncfdiv = row.get("ncfdiv")

    market_cap = None
    if shares not in (None, 0) and price is not None:
        market_cap = shares * price

    enterprise_value = None
    if market_cap is not None:
        enterprise_value = market_cap + (debt or 0) - (cashneq or 0)
        if enterprise_value <= 0:
            enterprise_value = None

    book_value = book_value_per_share(row)
    sales_per_share = safe_div(revenue, shares)
    cashflow_ops_per_share = safe_div(ncfo, shares)
    sfcf_per_share = safe_div(fcf, shares)
    ncf_per_share = safe_div(ncf, shares)
    cash_per_share = safe_div(cashneq, shares)
    asset_per_share = safe_div(assets, shares)

    ebitda_ev = safe_div(ebitda, enterprise_value)
    rev_debt = safe_div(revenue, debt) if debt not in (None, 0) else None
    mc_ev = safe_div(market_cap, enterprise_value)

    pe = safe_div(price, eps) if price is not None and eps not in (None, 0) else None
    pb = safe_div(price, book_value) if price is not None and book_value not in (None, 0) else None
    earnings_yield = safe_div(eps, price) if price not in (None, 0) and eps is not None else None

    div_yield = None
    if ncfdiv is not None and shares not in (None, 0) and price not in (None, 0):
        dps = abs(ncfdiv) / shares
        div_yield = safe_div(dps, price)

    return {
        "market_cap": market_cap,
        "revenue": revenue,
        "sales_per_share": sales_per_share,
        "ebitda_ev": ebitda_ev,
        "book_value_per_share": book_value,
        "eps": eps,
        "cashflow_ops_per_share": cashflow_ops_per_share,
        "sfcf_per_share": sfcf_per_share,
        "ncf_per_share": ncf_per_share,
        "cash_per_share": cash_per_share,
        "asset_per_share": asset_per_share,
        "rev_debt": rev_debt,
        "mc_ev": mc_ev,
        "pe": pe,
        "pb": pb,
        "earnings_yield": earnings_yield,
        "roe": roe(row),
        "roa": roa(row),
        "gross_margin": gross_margin(row),
        "operating_margin": operating_margin(row),
        "ebitda_margin": ebitda_margin(row),
        "net_margin": net_margin(row),
        "fcf_margin": fcf_margin(row),
        "cfo_margin": cfo_margin(row),
        "debt_equity": debt_equity(row),
        "debt_assets": leverage(row),
        "current_ratio": current_ratio(row),
        "quick_ratio": quick_ratio(row),
        "interest_coverage": interest_coverage(row),
        "cash_to_debt": cash_to_debt(row),
        "div_yield": div_yield,
        "asset_turnover": asset_turnover(row),
    }


# Stable JSON API field names (camelCase) — preserve existing contracts.
_API_FIELD_MAP: dict[str, str] = {
    "market_cap": "marketCap",
    "revenue": "revenue",
    "sales_per_share": "sp",
    "ebitda_ev": "ebitdaEv",
    "book_value_per_share": "tbp",
    "eps": "ep",
    "cashflow_ops_per_share": "cfop",
    "sfcf_per_share": "sfcfp",
    "ncf_per_share": "ncfp",
    "cash_per_share": "cashp",
    "asset_per_share": "assetp",
    "rev_debt": "revDebt",
    "mc_ev": "mcEv",
    "pe": "pe",
    "pb": "pb",
    "earnings_yield": "earningsYield",
    "roe": "roe",
    "roa": "roa",
    "gross_margin": "grossMargin",
    "operating_margin": "operatingMargin",
    "ebitda_margin": "ebitdaMargin",
    "net_margin": "netMargin",
    "fcf_margin": "fcfMargin",
    "cfo_margin": "cfoMargin",
    "debt_equity": "de",
    "debt_assets": "debtAssets",
    "current_ratio": "currentRatio",
    "quick_ratio": "quickRatio",
    "interest_coverage": "interestCoverage",
    "cash_to_debt": "cashToDebt",
    "div_yield": "divYield",
    "asset_turnover": "assetTurnover",
}


def metrics_to_api_payload(metrics: dict[str, float | None]) -> dict[str, float | None]:
    """Map canonical snake_case metrics to legacy camelCase API keys."""
    payload: dict[str, float | None] = {}
    for canonical, api_key in _API_FIELD_MAP.items():
        payload[api_key] = metrics.get(canonical)
    # Legacy duplicate: book value per share also exposed as bp
    payload["bp"] = metrics.get("book_value_per_share")
    return payload


def build_company_metrics(row: dict, price: float | None = None) -> dict[str, float | None]:
    """Public API — same shape as historical build_company_metrics consumers."""
    return metrics_to_api_payload(compute_period_metrics(row, price=price))
