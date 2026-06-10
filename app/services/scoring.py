from __future__ import annotations

import json
import logging
from typing import Any

from .metric_primitives import (
    asset_turnover as _asset_turnover,
    current_ratio as _current_ratio,
    free_cash_flow as _fcf,
    gross_margin as _gross_margin,
    leverage as _leverage,
    operating_margin as _operating_margin,
    roa as _roa,
    safe_div as _safe_div,
    total_debt as _debt,
)

logger = logging.getLogger("stock_tracker.scoring")

# Canonical numeric scoring lives here. Display tiers and heatmap metadata are in
# metric_registry.py (score_type, heatmap_mode); do not duplicate score bands elsewhere.


def compute_piotroski_f(current: dict, prior: dict) -> tuple[int | None, dict[str, int]]:
    """Nine-point Piotroski F-score from two consecutive annual wide rows."""
    components: dict[str, int] = {}
    roa_current = _roa(current)
    roa_prior = _roa(prior)
    ncfo = current.get("ncfo")
    netinc = current.get("netinc")

    if roa_current is None or ncfo is None or netinc is None:
        return None, {}

    components["roa"] = 1 if roa_current > 0 else 0
    components["cfo"] = 1 if ncfo > 0 else 0
    if roa_prior is not None:
        components["delta_roa"] = 1 if roa_current > roa_prior else 0
    else:
        components["delta_roa"] = 0
    components["accruals"] = 1 if ncfo > netinc else 0

    leverage_current = _leverage(current)
    leverage_prior = _leverage(prior)
    if leverage_current is not None and leverage_prior is not None:
        components["delta_leverage"] = 1 if leverage_current < leverage_prior else 0
    else:
        components["delta_leverage"] = 0

    cr_current = _current_ratio(current)
    cr_prior = _current_ratio(prior)
    if cr_current is not None and cr_prior is not None:
        components["delta_liquidity"] = 1 if cr_current > cr_prior else 0
    else:
        components["delta_liquidity"] = 0

    shares_current = current.get("sharesbas")
    shares_prior = prior.get("sharesbas")
    if shares_current is not None and shares_prior is not None:
        components["no_dilution"] = 1 if shares_current <= shares_prior else 0
    else:
        components["no_dilution"] = 0

    gm_current = _gross_margin(current)
    gm_prior = _gross_margin(prior)
    if gm_current is not None and gm_prior is not None:
        components["delta_gross_margin"] = 1 if gm_current > gm_prior else 0
    else:
        components["delta_gross_margin"] = 0

    at_current = _asset_turnover(current)
    at_prior = _asset_turnover(prior)
    if at_current is not None and at_prior is not None:
        components["delta_asset_turnover"] = 1 if at_current > at_prior else 0
    else:
        components["delta_asset_turnover"] = 0

    total = sum(components.values())
    return total, components


def compute_altman_z(row: dict, market_cap: float | None = None) -> tuple[float | None, dict[str, float]]:
    """Altman Z-score for manufacturing-style distress model."""
    assets = row.get("assets")
    liabilities = row.get("liabilities")
    if assets in (None, 0) or liabilities in (None, 0):
        return None, {}

    working_capital = row.get("workingcapital")
    if working_capital is None:
        assets_current = row.get("assetscurrent")
        liabilities_current = row.get("liabilitiescurrent")
        if assets_current is not None and liabilities_current is not None:
            working_capital = assets_current - liabilities_current

    retearn = row.get("retearn")
    ebit = row.get("ebit") or row.get("opinc")
    revenue = row.get("revenue")

    wc_ta = _safe_div(working_capital, assets)
    re_ta = _safe_div(retearn, assets)
    ebit_ta = _safe_div(ebit, assets)
    mve_tl = _safe_div(market_cap, liabilities) if market_cap is not None else None
    sales_ta = _safe_div(revenue, assets)

    if any(v is None for v in (wc_ta, re_ta, ebit_ta, sales_ta)):
        return None, {}

    components = {
        "wc_ta": wc_ta,
        "re_ta": re_ta,
        "ebit_ta": ebit_ta,
        "mve_tl": mve_tl if mve_tl is not None else 0.0,
        "sales_ta": sales_ta,
    }
    z = (
        1.2 * components["wc_ta"]
        + 1.4 * components["re_ta"]
        + 3.3 * components["ebit_ta"]
        + 0.6 * components["mve_tl"]
        + 1.0 * components["sales_ta"]
    )
    return z, components


def altman_zone(z: float | None) -> str | None:
    if z is None:
        return None
    if z > 2.99:
        return "safe"
    if z >= 1.81:
        return "grey"
    return "distress"


def compute_beneish_m(current: dict, prior: dict) -> tuple[float | None, dict[str, float]]:
    """Beneish M-score; returns None when required inputs are missing."""
    revenue_c = current.get("revenue")
    revenue_p = prior.get("revenue")
    assets_c = current.get("assets")
    assets_p = prior.get("assets")
    if any(v in (None, 0) for v in (revenue_c, revenue_p, assets_c, assets_p)):
        return None, {}

    recv_c = current.get("receivables")
    recv_p = prior.get("receivables")
    if recv_c is None or recv_p is None:
        return None, {}

    dsri = _safe_div(_safe_div(recv_c, revenue_c), _safe_div(recv_p, revenue_p))
    gm_c = _gross_margin(current)
    gm_p = _gross_margin(prior)
    if gm_c in (None, 0) or gm_p is None:
        return None, {}
    gmi = gm_p / gm_c

    ca_c = current.get("assetscurrent")
    ca_p = prior.get("assetscurrent")
    ppe_c = current.get("ppnenet")
    ppe_p = prior.get("ppnenet")
    if ca_c is None or ca_p is None or ppe_c is None or ppe_p is None:
        return None, {}

    aqi_c = 1.0 - (ca_c + ppe_c) / assets_c
    aqi_p = 1.0 - (ca_p + ppe_p) / assets_p
    if aqi_p == 0:
        return None, {}
    aqi = aqi_c / aqi_p

    sgi = revenue_c / revenue_p

    dep_c = current.get("depamor")
    dep_p = prior.get("depamor")
    if dep_c is None or dep_p is None or ppe_c is None or ppe_p is None:
        return None, {}
    dep_rate_c = _safe_div(dep_c, dep_c + ppe_c)
    dep_rate_p = _safe_div(dep_p, dep_p + ppe_p)
    if dep_rate_c in (None, 0) or dep_rate_p is None:
        return None, {}
    depi = dep_rate_p / dep_rate_c

    sgna_c = current.get("sgna")
    sgna_p = prior.get("sgna")
    if sgna_c is not None and sgna_p is not None:
        sgai = _safe_div(_safe_div(sgna_c, revenue_c), _safe_div(sgna_p, revenue_p))
    else:
        sgai = 1.0

    liab_c = current.get("liabilities")
    liab_p = prior.get("liabilities")
    if liab_c is None or liab_p is None:
        return None, {}
    lvgi = _safe_div(_safe_div(liab_c, assets_c), _safe_div(liab_p, assets_p))

    ncfo = current.get("ncfo")
    netinc = current.get("netinc")
    if ncfo is None or netinc is None:
        return None, {}
    tata = (netinc - ncfo) / assets_c

    if any(v is None for v in (dsri, gmi, aqi, sgi, depi, sgai, lvgi)):
        return None, {}

    components = {
        "dsri": dsri,
        "gmi": gmi,
        "aqi": aqi,
        "sgi": sgi,
        "depi": depi,
        "sgai": sgai,
        "lvgi": lvgi,
        "tata": tata,
    }
    m = (
        -4.84
        + 0.92 * dsri
        + 0.528 * gmi
        + 0.404 * aqi
        + 0.892 * sgi
        + 0.115 * depi
        - 0.172 * sgai
        + 4.679 * tata
        - 0.327 * lvgi
    )
    return m, components


def compute_survivability(
    row: dict,
    prior: dict | None = None,
    altman_z: float | None = None,
    fcf_positive_streak: int = 0,
) -> tuple[float | None, str | None]:
    """Custom 0-100 survivability score with qualitative bucket."""
    score = 0.0
    weights_used = 0

    cr = _current_ratio(row)
    if cr is not None:
        score += min(cr / 2.0, 1.0) * 20
        weights_used += 20

    cash = row.get("cashneq")
    debt = _debt(row)
    if cash is not None:
        if debt in (None, 0):
            score += 20
        else:
            score += min(cash / debt, 2.0) / 2.0 * 20
        weights_used += 20

    fcf_val = _fcf(row)
    if fcf_val is not None:
        score += 15 if fcf_val > 0 else 0
        weights_used += 15
    if fcf_positive_streak >= 2:
        score += min(fcf_positive_streak, 4) * 2.5

    ebit = row.get("ebit") or row.get("opinc")
    interest = row.get("interestexp")
    if ebit is not None and interest not in (None, 0):
        coverage = ebit / abs(interest)
        score += min(max(coverage, 0), 10) / 10.0 * 15
        weights_used += 15

    if altman_z is not None:
        if altman_z > 2.99:
            score += 15
        elif altman_z >= 1.81:
            score += 8
        else:
            score += 0
        weights_used += 15

    equity = row.get("equity")
    if debt is not None and equity not in (None, 0):
        de = debt / equity
        if de < 0.5:
            score += 15
        elif de < 1.0:
            score += 10
        elif de < 2.0:
            score += 5
        weights_used += 15
        if prior is not None:
            prior_debt = _debt(prior)
            prior_equity = prior.get("equity")
            if prior_debt is not None and prior_equity not in (None, 0):
                prior_de = prior_debt / prior_equity
                if de < prior_de:
                    score += 5

    if weights_used == 0:
        return None, None

    normalized = min(100.0, (score / weights_used) * 100)
    bucket = survivability_bucket(normalized)
    return round(normalized, 2), bucket


def survivability_bucket(score: float | None) -> str | None:
    if score is None:
        return None
    if score < 20:
        return "critical"
    if score < 40:
        return "distressed"
    if score < 60:
        return "watchlist"
    if score < 80:
        return "stable"
    return "strong"


def share_dilution_rate(current: dict, prior: dict | None) -> float | None:
    shares_c = current.get("sharesbas")
    shares_p = prior.get("sharesbas") if prior else None
    if shares_c in (None, 0) or shares_p in (None, 0):
        return None
    return (shares_c - shares_p) / shares_p


def margin_trend_delta(rows_by_date: list[dict], years: int, margin_fn) -> float | None:
    """Delta between latest margin and margin N years earlier."""
    if len(rows_by_date) < 2:
        return None
    latest = rows_by_date[0]
    latest_margin = margin_fn(latest)
    if latest_margin is None:
        return None
    target_idx = min(years, len(rows_by_date) - 1)
    prior = rows_by_date[target_idx]
    prior_margin = margin_fn(prior)
    if prior_margin is None:
        return None
    return latest_margin - prior_margin


def compute_scores_for_periods(
    annual_rows: list[dict],
    prices_by_period: dict[str, float | None] | None = None,
) -> list[dict[str, Any]]:
    """Compute score records for each annual period (newest first)."""
    if not annual_rows:
        return []

    sorted_rows = sorted(annual_rows, key=lambda r: r.get("calendardate") or "", reverse=True)
    chronological = list(reversed(sorted_rows))
    streak_by_period: dict[str, int] = {}
    streak = 0
    for row in chronological:
        period_end = row.get("calendardate")
        if not period_end:
            continue
        fcf_val = _fcf(row)
        if fcf_val is not None and fcf_val > 0:
            streak += 1
        else:
            streak = 0
        streak_by_period[period_end] = streak

    records: list[dict[str, Any]] = []
    for idx, row in enumerate(sorted_rows):
        period_end = row.get("calendardate")
        if not period_end:
            continue
        prior = sorted_rows[idx + 1] if idx + 1 < len(sorted_rows) else None
        fcf_streak = streak_by_period.get(period_end, 0)

        market_cap = None
        if prices_by_period:
            price = prices_by_period.get(period_end)
            shares = row.get("sharesbas")
            if price is not None and shares not in (None, 0):
                market_cap = price * shares

        piotroski_f = None
        piotroski_components: dict[str, int] = {}
        if prior is not None:
            piotroski_f, piotroski_components = compute_piotroski_f(row, prior)

        altman_z, altman_components = compute_altman_z(row, market_cap=market_cap)

        beneish_m = None
        beneish_components: dict[str, float] = {}
        if prior is not None:
            beneish_m, beneish_components = compute_beneish_m(row, prior)

        survivability, survivability_bucket_val = compute_survivability(
            row,
            prior=prior,
            altman_z=altman_z,
            fcf_positive_streak=fcf_streak,
        )

        records.append(
            {
                "period_end": period_end,
                "dimension": row.get("dimension") or "ARY",
                "piotroski_f": piotroski_f,
                "altman_z": altman_z,
                "beneish_m": beneish_m,
                "survivability": survivability,
                "survivability_bucket": survivability_bucket_val,
                "piotroski_components": piotroski_components,
                "altman_components": altman_components,
                "beneish_components": beneish_components,
            }
        )

    return records


def scores_to_json(components: dict) -> str | None:
    if not components:
        return None
    return json.dumps(components)
