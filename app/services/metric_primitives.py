"""Shared financial primitives for metrics engine and scoring models."""

from __future__ import annotations


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def total_debt(row: dict) -> float | None:
    debt = row.get("debt")
    if debt is not None:
        return debt
    debt_current = row.get("debtcurrent")
    debt_lt = row.get("debtlt")
    if debt_current is not None or debt_lt is not None:
        return (debt_current or 0.0) + (debt_lt or 0.0)
    return None


def free_cash_flow(row: dict) -> float | None:
    fcf = row.get("fcf")
    if fcf is not None:
        return fcf
    ncfo = row.get("ncfo")
    capex = row.get("capex")
    if ncfo is None:
        return None
    if capex is not None:
        return ncfo - capex
    return ncfo


def resolve_ebitda(row: dict) -> float | None:
    ebitda = row.get("ebitda")
    if ebitda is not None:
        return ebitda
    ebitda = row.get("opinc") or row.get("ebit")
    if ebitda is not None:
        return ebitda
    netinc = row.get("netinc")
    if netinc is None:
        return None
    taxexp = row.get("taxexp")
    interestexp = row.get("interestexp")
    pretax_proxy = netinc + abs(taxexp or 0)
    return pretax_proxy + abs(interestexp or 0)


def _coherent_gross_profit(gp, revenue, cor) -> float | None:
    """Pick gross profit only when SEC revenue / COGS / GrossProfit line items agree."""
    if revenue is not None and revenue <= 0:
        return None

    tolerance = 1.02  # minor filing rounding

    if gp is not None and revenue is not None:
        if 0 <= gp <= revenue * tolerance:
            return gp

    if revenue is not None and cor is not None:
        if 0 <= cor <= revenue * tolerance:
            derived = revenue - cor
            if derived >= 0:
                if gp is None or abs(derived - gp) <= max(revenue * 0.05, 1.0):
                    return derived
        return None

    if gp is not None and revenue is None:
        return gp

    return None


def gross_profit(row: dict) -> float | None:
    return _coherent_gross_profit(row.get("gp"), row.get("revenue"), row.get("cor"))


def gross_margin(row: dict) -> float | None:
    revenue = row.get("revenue")
    profit = gross_profit(row)
    margin = safe_div(profit, revenue)
    if margin is None:
        return None
    # Reject impossible margins from mismatched XBRL concepts (e.g. tower REIT segment revenue vs consolidated GP).
    if margin < -0.5 or margin > 1.0:
        return None
    return margin


def operating_margin(row: dict) -> float | None:
    revenue = row.get("revenue")
    opinc = row.get("opinc") or row.get("ebit")
    return safe_div(opinc, revenue)


def ebitda_margin(row: dict) -> float | None:
    return safe_div(resolve_ebitda(row), row.get("revenue"))


def net_margin(row: dict) -> float | None:
    return safe_div(row.get("netinc"), row.get("revenue"))


def fcf_margin(row: dict) -> float | None:
    return safe_div(free_cash_flow(row), row.get("revenue"))


def cfo_margin(row: dict) -> float | None:
    return safe_div(row.get("ncfo"), row.get("revenue"))


def current_ratio(row: dict) -> float | None:
    return safe_div(row.get("assetscurrent"), row.get("liabilitiescurrent"))


def quick_ratio(row: dict) -> float | None:
    assets_current = row.get("assetscurrent")
    inventory = row.get("inventory")
    liabilities_current = row.get("liabilitiescurrent")
    if assets_current is None:
        return None
    quick_assets = assets_current - (inventory or 0.0)
    return safe_div(quick_assets, liabilities_current)


def roa(row: dict) -> float | None:
    return safe_div(row.get("netinc"), row.get("assets"))


def roe(row: dict) -> float | None:
    return safe_div(row.get("netinc"), row.get("equity"))


def asset_turnover(row: dict) -> float | None:
    return safe_div(row.get("revenue"), row.get("assets"))


def leverage(row: dict) -> float | None:
    return safe_div(total_debt(row), row.get("assets"))


def debt_equity(row: dict) -> float | None:
    return safe_div(total_debt(row), row.get("equity"))


def interest_coverage(row: dict) -> float | None:
    ebit = row.get("ebit") or row.get("opinc")
    interest = row.get("interestexp")
    if ebit is None or interest in (None, 0):
        return None
    return ebit / abs(interest)


def cash_to_debt(row: dict) -> float | None:
    cash = row.get("cashneq")
    debt = total_debt(row)
    if cash is None:
        return None
    if debt in (None, 0):
        return None
    return cash / debt


def book_value_per_share(row: dict) -> float | None:
    shares = row.get("sharesbas")
    if shares in (None, 0):
        return None
    equity = row.get("equity")
    if equity is not None:
        return equity / shares
    assets = row.get("assets")
    liabilities = row.get("liabilities")
    if assets is not None and liabilities is not None:
        return (assets - liabilities) / shares
    return None


# Conservative NAV haircuts (Phase 0 — thesis engine latent layer)
RECEIVABLES_HAIRCUT = 0.80
INVENTORY_HAIRCUT = 0.50
PPNE_HAIRCUT = 0.375  # midpoint of 25–50% liquidation range

# Sloan accruals: earnings quality threshold (Gate 2 supporting)
SLOAN_ACCRUALS_HIGH_THRESHOLD = 0.10

# Time-cheap: structural impairment probable at 5+ years of cheapness
TIME_CHEAP_STRUCTURAL_YEARS = 5

# Gate stack thresholds (Phase 1 — non-compensatory investability screen)
GATE_RUNWAY_PASS_MONTHS = 18.0
GATE_INTEREST_COVERAGE_PASS = 2.0
GATE_INTEREST_COVERAGE_FAIL = 1.0
GATE_SURVIVABILITY_STRONG = 80.0
BENEISH_MANIPULATION_THRESHOLD = -1.78
GATE_FCF_YIELD_PASS = 0.08
GATE_OWNER_EARNINGS_YIELD_PASS = 0.10
GATE_AUDITOR_CHANGE_LOOKBACK_DAYS = 365


def conservative_nav_components(row: dict) -> dict[str, float | None]:
    """Haircut asset components for liquidation-style NAV."""
    cash = row.get("cashneq") or 0.0
    receivables = (row.get("receivables") or 0.0) * RECEIVABLES_HAIRCUT
    inventory = (row.get("inventory") or 0.0) * INVENTORY_HAIRCUT
    ppne = (row.get("ppnenet") or 0.0) * PPNE_HAIRCUT
    other_current = row.get("assetscurrent")
    if other_current is not None:
        other_current = max(
            0.0,
            other_current
            - (row.get("cashneq") or 0.0)
            - (row.get("receivables") or 0.0)
            - (row.get("inventory") or 0.0),
        )
    else:
        other_current = 0.0
    liabilities = row.get("liabilities")
    return {
        "cash": cash,
        "receivables_haircut": receivables,
        "inventory_haircut": inventory,
        "ppne_haircut": ppne,
        "other_current": other_current,
        "goodwill_written_off": row.get("goodwill") or 0.0,
        "intangibles_written_off": row.get("intangibles") or 0.0,
        "liabilities": liabilities,
    }


def conservative_nav_total(row: dict) -> float | None:
    """Net asset value with conservative asset haircuts; goodwill/intangibles excluded."""
    components = conservative_nav_components(row)
    liabilities = components.get("liabilities")
    if liabilities is None:
        return None
    asset_sum = (
        (components["cash"] or 0.0)
        + (components["receivables_haircut"] or 0.0)
        + (components["inventory_haircut"] or 0.0)
        + (components["ppne_haircut"] or 0.0)
        + (components["other_current"] or 0.0)
    )
    return asset_sum - liabilities


def conservative_nav_per_share(row: dict) -> float | None:
    nav = conservative_nav_total(row)
    shares = row.get("sharesbas")
    if nav is None or shares in (None, 0):
        return None
    return nav / shares


def price_to_conservative_nav(price: float | None, nav_per_share: float | None) -> float | None:
    """Price / conservative NAV per share; values in (0, 1) indicate discount to haircut NAV."""
    if price in (None, 0) or nav_per_share in (None, 0) or nav_per_share < 0:
        return None
    return price / nav_per_share


NEAR_TERM_DEBT_MATURITY_BUCKETS = frozenset({"year_1", "year_2"})


def debt_maturity_near_term_wall(
    schedule: list[dict] | None,
    *,
    min_amount: float = 0.0,
) -> bool | None:
    """True when material debt matures within two annual buckets (XBRL year_1 / year_2 tags)."""
    if not schedule:
        return None
    for row in schedule:
        bucket = row.get("maturity_year")
        amount = row.get("amount")
        if bucket not in NEAR_TERM_DEBT_MATURITY_BUCKETS:
            continue
        if amount is not None and float(amount) > min_amount:
            return True
    return False


def sloan_accruals(row: dict, prior_row: dict | None = None) -> float | None:
    """(Net income − operating cash flow) / average total assets."""
    netinc = row.get("netinc")
    ncfo = row.get("ncfo")
    assets = row.get("assets")
    if netinc is None or ncfo is None or assets is None:
        return None
    prior_assets = (prior_row or {}).get("assets")
    if prior_assets is not None:
        avg_assets = (assets + prior_assets) / 2.0
    else:
        avg_assets = assets
    if avg_assets in (None, 0):
        return None
    return (netinc - ncfo) / avg_assets


def quarterly_cash_runway_months(row: dict) -> float | None:
    """Months of runway at current quarterly cash-burn rate (ARQ/MRQ row)."""
    cash = row.get("cashneq")
    if cash is None or cash <= 0:
        return 0.0 if cash == 0 else None
    fcf = free_cash_flow(row)
    if fcf is None:
        ncfo = row.get("ncfo")
        if ncfo is None:
            return None
        fcf = ncfo
    if fcf >= 0:
        return None  # not burning cash — runway undefined (infinite)
    monthly_burn = abs(fcf) / 3.0
    if monthly_burn <= 0:
        return None
    return cash / monthly_burn


def _metric_cheap(val: float | None, *, higher_is_better: bool, sector_median: float | None) -> bool | None:
    if val is None:
        return None
    if sector_median is not None and sector_median > 0:
        if higher_is_better:
            return val >= sector_median
        return val <= sector_median
    return None


def time_cheap_persistence(
    annual_history: list[dict],
    *,
    sector_pe_median: float | None = None,
    sector_pb_median: float | None = None,
    sector_ey_median: float | None = None,
) -> dict[str, float | int | str | None]:
    """
    Count consecutive recent annual periods where valuation metrics indicate cheapness.
    Returns periods count, classification, and per-metric streaks.
    """
    if not annual_history:
        return {
            "consecutive_periods": None,
            "classification": None,
            "pe_streak": None,
            "pb_streak": None,
            "earnings_yield_streak": None,
        }

    sorted_rows = sorted(annual_history, key=lambda r: r.get("calendardate") or "", reverse=True)

    def streak(metric_key: str, *, higher_is_better: bool, sector_median: float | None) -> int:
        count = 0
        for row in sorted_rows:
            val = row.get(metric_key)
            if val is None:
                break
            cheap = _metric_cheap(val, higher_is_better=higher_is_better, sector_median=sector_median)
            if cheap is True or (
                cheap is None
                and (
                    (metric_key == "pe" and val > 0 and val < 12)
                    or (metric_key == "pb" and val > 0 and val < 1.0)
                    or (metric_key == "earnings_yield" and val > 0.08)
                )
            ):
                count += 1
            else:
                break
        return count

    pe_streak = streak("pe", higher_is_better=False, sector_median=sector_pe_median)
    pb_streak = streak("pb", higher_is_better=False, sector_median=sector_pb_median)
    ey_streak = streak("earnings_yield", higher_is_better=True, sector_median=sector_ey_median)
    consecutive = max(pe_streak, pb_streak, ey_streak)

    if consecutive >= TIME_CHEAP_STRUCTURAL_YEARS:
        classification = "structural"
    elif consecutive >= 2:
        classification = "persistent"
    elif consecutive >= 1:
        classification = "recent"
    else:
        classification = "none"

    return {
        "consecutive_periods": consecutive,
        "classification": classification,
        "pe_streak": pe_streak,
        "pb_streak": pb_streak,
        "earnings_yield_streak": ey_streak,
    }


def peer_industry_secular_trend(peer_annual_rows: list[list[dict]]) -> dict[str, float | bool | None]:
    """
    Median 3-year revenue CAGR and gross-margin delta across peer annual histories.
    peer_declining=True when median revenue CAGR < 0 and margin delta negative.
    """
    revenue_cagrs: list[float] = []
    margin_deltas: list[float] = []

    for history in peer_annual_rows:
        sorted_rows = sorted(history, key=lambda r: r.get("calendardate") or "", reverse=True)
        if len(sorted_rows) < 4:
            continue
        current = sorted_rows[0]
        three_yr_ago = sorted_rows[3]
        rev_now = current.get("revenue")
        rev_then = three_yr_ago.get("revenue")
        if rev_now is not None and rev_then not in (None, 0) and rev_now > 0:
            cagr = (rev_now / rev_then) ** (1 / 3.0) - 1.0
            revenue_cagrs.append(cagr)
        gm_now = gross_margin(current)
        gm_then = gross_margin(three_yr_ago)
        if gm_now is not None and gm_then is not None:
            margin_deltas.append(gm_now - gm_then)

    if not revenue_cagrs and not margin_deltas:
        return {
            "median_revenue_cagr_3yr": None,
            "median_gross_margin_delta_3yr": None,
            "peer_declining": None,
            "peer_count": 0,
        }

    def _median(vals: list[float]) -> float | None:
        if not vals:
            return None
        s = sorted(vals)
        mid = len(s) // 2
        if len(s) % 2:
            return s[mid]
        return (s[mid - 1] + s[mid]) / 2.0

    med_cagr = _median(revenue_cagrs)
    med_margin = _median(margin_deltas)
    peer_declining = None
    if med_cagr is not None and med_margin is not None:
        peer_declining = med_cagr < 0 and med_margin < 0

    return {
        "median_revenue_cagr_3yr": med_cagr,
        "median_gross_margin_delta_3yr": med_margin,
        "peer_declining": peer_declining,
        "peer_count": len(revenue_cagrs),
    }


def capital_allocation_track_record(
    annual_history: list[dict],
    *,
    prices_by_period: dict[str, float] | None = None,
) -> dict[str, float | int | bool | None]:
    """
    Revealed capital-allocation quality from share count, buybacks, dividends, and equity raises.
    Returns composite score 0–100 and component evidence.
    """
    if len(annual_history) < 2:
        return {
            "score": None,
            "buyback_at_discount_pct": None,
            "dilution_rate_3yr": None,
            "dividend_fcf_coverage": None,
            "equity_raises_vs_retained_earnings": None,
        }

    sorted_rows = sorted(annual_history, key=lambda r: r.get("calendardate") or "", reverse=True)
    current = sorted_rows[0]
    prior_3yr = sorted_rows[3] if len(sorted_rows) > 3 else sorted_rows[-1]

    shares_now = current.get("sharesbas")
    shares_then = prior_3yr.get("sharesbas")
    dilution_rate = None
    if shares_now and shares_then and shares_then > 0:
        years = max(1, min(3, len(sorted_rows) - 1))
        dilution_rate = (shares_now / shares_then) ** (1 / years) - 1.0

    buyback_discount_pct = None
    buyback_events = 0
    buyback_at_discount = 0
    for row in sorted_rows[:5]:
        repurchase = row.get("ncfcommon")
        if repurchase is None or repurchase >= 0:
            continue
        period = row.get("calendardate")
        price = (prices_by_period or {}).get(period or "")
        bvps = book_value_per_share(row)
        if price is not None and bvps not in (None, 0):
            buyback_events += 1
            if price < bvps:
                buyback_at_discount += 1
    if buyback_events:
        buyback_discount_pct = buyback_at_discount / buyback_events

    fcf = free_cash_flow(current)
    div = current.get("ncfdiv")
    dividend_coverage = None
    if fcf is not None and div is not None and div < 0:
        dividend_coverage = safe_div(fcf, abs(div))

    equity_raised = sum(abs(r.get("ncfcommon") or 0) for r in sorted_rows[:3] if (r.get("ncfcommon") or 0) > 0)
    retearn_growth = None
    re_now = current.get("retearn")
    re_then = prior_3yr.get("retearn")
    if re_now is not None and re_then is not None:
        retearn_growth = re_now - re_then
    raises_vs_value = None
    if retearn_growth is not None:
        raises_vs_value = retearn_growth - equity_raised

    score_parts: list[float] = []
    if buyback_discount_pct is not None:
        score_parts.append(buyback_discount_pct * 100)
    if dilution_rate is not None:
        score_parts.append(max(0.0, min(100.0, 50.0 - dilution_rate * 500)))
    if dividend_coverage is not None:
        score_parts.append(min(100.0, dividend_coverage * 50))
    if raises_vs_value is not None:
        score_parts.append(70.0 if raises_vs_value > 0 else 30.0)

    score = round(sum(score_parts) / len(score_parts), 1) if score_parts else None

    return {
        "score": score,
        "buyback_at_discount_pct": buyback_discount_pct,
        "dilution_rate_3yr": dilution_rate,
        "dividend_fcf_coverage": dividend_coverage,
        "equity_raises_vs_retained_earnings": raises_vs_value,
    }
