"""Phase 1 non-compensatory gate stack for the thesis engine."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Literal

from ..repositories import Repository
from .fundamentals import (
    collapse_narrow_fundamentals_rows,
    fetch_resolved_wide_rows,
    pivot_fundamentals_rows,
    resolve_financial_dimension,
)
from .latent_metrics import compute_latent_metrics
from .metric_primitives import (
    BENEISH_MANIPULATION_THRESHOLD,
    GATE_AUDITOR_CHANGE_LOOKBACK_DAYS,
    GATE_FCF_YIELD_PASS,
    GATE_INTEREST_COVERAGE_FAIL,
    GATE_INTEREST_COVERAGE_PASS,
    GATE_OWNER_EARNINGS_YIELD_PASS,
    GATE_RUNWAY_PASS_MONTHS,
    GATE_SURVIVABILITY_STRONG,
    SLOAN_ACCRUALS_HIGH_THRESHOLD,
    TIME_CHEAP_STRUCTURAL_YEARS,
    debt_maturity_near_term_wall,
    free_cash_flow,
    interest_coverage,
    safe_div,
    total_debt,
)
from .metrics_engine import compute_period_metrics
from .prices import PricesService
from .scoring import margin_trend_delta, _gross_margin, _operating_margin

logger = logging.getLogger("stock_tracker.gate_engine")

GateStatus = Literal["pass", "fail", "unknown"]
GATE_NAMES = (
    "solvency_runway",
    "accounting_integrity",
    "secular_decline",
    "margin_of_safety",
)

TURNAROUND_NARRATIVE_STATES = frozenset({"cyclical_recovery", "restructuring", "turnaround_optimism"})


def _gate_result(
    gate: str,
    status: GateStatus,
    *,
    evidence: dict[str, Any] | None = None,
    triggered_by: str = "",
) -> dict[str, Any]:
    return {
        "gate": gate,
        "status": status,
        "evidence": evidence or {},
        "triggered_by": triggered_by,
    }


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")[:10]).date()
    except ValueError:
        return None


def _within_days(filed_date: str | None, *, days: int) -> bool:
    parsed = _parse_date(filed_date)
    if parsed is None:
        return False
    return parsed >= date.today() - timedelta(days=days)


def _enterprise_value(row: dict, price: float | None) -> float | None:
    shares = row.get("sharesbas")
    if shares in (None, 0) or price is None:
        return None
    market_cap = shares * price
    debt = total_debt(row) or 0.0
    cash = row.get("cashneq") or 0.0
    ev = market_cap + debt - cash
    return ev if ev > 0 else None


def _owner_earnings_yield(row: dict, price: float | None) -> float | None:
    return safe_div(free_cash_flow(row), _enterprise_value(row, price))


def _fcf_yield(row: dict, price: float | None) -> float | None:
    return _owner_earnings_yield(row, price)


def _within_days_as_of(filed_date: str | None, *, as_of: date, days: int) -> bool:
    parsed = _parse_date(filed_date)
    if parsed is None:
        return False
    return parsed >= as_of - timedelta(days=days) and parsed <= as_of


def edgar_trigger_state_as_of(
    flags: list[dict],
    events: list[dict],
    as_of: date,
) -> dict[str, Any]:
    """Evaluate EDGAR trigger flags using only filings on or before as_of."""
    eligible_flags = [
        item
        for item in flags
        if (parsed := _parse_date(item.get("filed_date"))) is not None and parsed <= as_of
    ]
    eligible_events = [
        item
        for item in events
        if (parsed := _parse_date(item.get("filed_date"))) is not None and parsed <= as_of
    ]
    going_concern = any(item.get("flag_type") == "going_concern" for item in eligible_flags)
    nt_filing = any(item.get("flag_type") == "nt_filing" for item in eligible_flags)
    restatement = any(item.get("item_number") == "4.02" for item in eligible_events)
    auditor_change_12m = any(
        item.get("item_number") == "4.01"
        and _within_days_as_of(item.get("filed_date"), as_of=as_of, days=GATE_AUDITOR_CHANGE_LOOKBACK_DAYS)
        for item in eligible_events
    )
    return {
        "going_concern": going_concern,
        "nt_filing": nt_filing,
        "restatement": restatement,
        "auditor_change_12m": auditor_change_12m,
    }


def _edgar_trigger_state(flags: list[dict], events: list[dict]) -> dict[str, Any]:
    return edgar_trigger_state_as_of(flags, events, date.today())


def _quarterly_operational_recovery(repo: Repository, ticker: str) -> dict[str, Any]:
    """Detect margin or FCF improvement across the last four quarterly periods."""
    for dimension in ("MRQ", "ARQ"):
        narrow = collapse_narrow_fundamentals_rows(
            repo.fetch_fundamentals_rows([ticker], dimension=dimension),
            annual=False,
        )
        wide = pivot_fundamentals_rows(narrow, canonical_annual=False)
        if len(wide) < 2:
            continue
        wide.sort(key=lambda row: row.get("calendardate") or "", reverse=True)
        recent = wide[:4]
        if len(recent) < 2:
            continue

        gm_latest = _gross_margin(recent[0])
        gm_oldest = _gross_margin(recent[-1])
        om_latest = _operating_margin(recent[0])
        om_oldest = _operating_margin(recent[-1])
        fcf_latest = free_cash_flow(recent[0])
        fcf_oldest = free_cash_flow(recent[-1])

        margin_recovery = (
            (gm_latest is not None and gm_oldest is not None and gm_latest > gm_oldest + 0.005)
            or (om_latest is not None and om_oldest is not None and om_latest > om_oldest + 0.005)
        )
        fcf_recovery = (
            fcf_latest is not None
            and fcf_oldest is not None
            and fcf_latest > fcf_oldest
            and (fcf_latest > 0 or fcf_latest > fcf_oldest * 0.5)
        )
        return {
            "dimension": dimension,
            "periodsReviewed": len(recent),
            "marginRecovery": margin_recovery,
            "fcfRecovery": fcf_recovery,
            "operationalRecovery": margin_recovery or fcf_recovery,
            "grossMarginDelta": (gm_latest - gm_oldest) if gm_latest is not None and gm_oldest is not None else None,
            "fcfDelta": (fcf_latest - fcf_oldest) if fcf_latest is not None and fcf_oldest is not None else None,
        }

    return {
        "dimension": None,
        "periodsReviewed": 0,
        "marginRecovery": None,
        "fcfRecovery": None,
        "operationalRecovery": None,
    }


def assemble_gate_inputs(
    repo: Repository,
    ticker: str,
    *,
    prices_service: PricesService | None = None,
) -> dict[str, Any] | None:
    """Collect L1 scores, Phase 0 latent metrics, and supporting context for gate evaluation."""
    symbol = ticker.strip().upper()
    company = repo.get_company_by_ticker(symbol)
    if not company:
        return None

    resolved = resolve_financial_dimension("MRY", most_recent=False)
    rows = fetch_resolved_wide_rows(repo, [symbol], gte=None, resolved=resolved)
    row = rows[0] if rows else None
    if not row:
        return None

    if prices_service is None:
        prices_service = PricesService(repo)
    price_rows = repo.fetch_prices_batch([symbol], limit_per_ticker=1).get(symbol, [])
    price = price_rows[0]["close"] if price_rows else None

    annual_narrow = collapse_narrow_fundamentals_rows(
        repo.fetch_fundamentals_rows([symbol], dimension="ARY"),
        annual=True,
    )
    annual_rows = pivot_fundamentals_rows(annual_narrow, canonical_annual=True)
    annual_rows.sort(key=lambda item: item.get("calendardate") or "", reverse=True)

    metrics = compute_period_metrics(row, price=price)
    scores = repo.fetch_latest_company_scores([symbol], dimension="ARY").get(symbol) or {}
    latent = compute_latent_metrics(
        repo,
        symbol,
        row=row,
        price=price,
        sector=company.get("sector"),
    )
    narrative_snap = repo.fetch_latest_narrative_snapshots([symbol]).get(symbol) or {}
    narrative_states = narrative_snap.get("narrative_states") or []
    if isinstance(narrative_states, str):
        import json

        try:
            narrative_states = json.loads(narrative_states)
        except json.JSONDecodeError:
            narrative_states = []

    flags = repo.fetch_company_edgar_flags(symbol)
    events = repo.fetch_company_edgar_events(symbol, limit=50)
    edgar_triggers = _edgar_trigger_state(flags, events)
    operational = _quarterly_operational_recovery(repo, symbol)
    debt_schedule = repo.fetch_company_debt_maturities(symbol)
    debt_maturity_near_term = debt_maturity_near_term_wall(debt_schedule)

    fcf = free_cash_flow(row)
    fcf_yield = _fcf_yield(row, price)
    owner_earnings_yield = _owner_earnings_yield(row, price)
    fcf_positive_streak = 0
    for annual in annual_rows:
        annual_fcf = free_cash_flow(annual)
        if annual_fcf is not None and annual_fcf > 0:
            fcf_positive_streak += 1
        else:
            break

    return {
        "ticker": symbol,
        "company": company,
        "row": row,
        "price": price,
        "metrics": metrics,
        "scores": scores,
        "latent": latent,
        "annual_rows": annual_rows,
        "edgar_triggers": edgar_triggers,
        "operational_recovery": operational,
        "narrative_states": narrative_states if isinstance(narrative_states, list) else [],
        "margin_trends": {
            "gross_margin_3yr_delta": margin_trend_delta(annual_rows, 3, _gross_margin),
            "operating_margin_3yr_delta": margin_trend_delta(annual_rows, 3, _operating_margin),
        },
        "derived": {
            "fcf": fcf,
            "fcf_yield": fcf_yield,
            "owner_earnings_yield": owner_earnings_yield,
            "fcf_positive_streak": fcf_positive_streak,
            "interest_coverage": metrics.get("interest_coverage") or interest_coverage(row),
        },
        "debt_maturity_near_term": debt_maturity_near_term,
    }


def evaluate_solvency_runway(inputs: dict[str, Any]) -> dict[str, Any]:
    """Gate 1 — can the company fund operations for >=18 months without dilutive capital?"""
    scores = inputs.get("scores") or {}
    latent = inputs.get("latent") or {}
    derived = inputs.get("derived") or {}
    edgar = inputs.get("edgar_triggers") or {}

    survivability = scores.get("survivability")
    runway_months = latent.get("runway_months")
    coverage = derived.get("interest_coverage")
    fcf = derived.get("fcf")
    debt_maturity_near_term = inputs.get("debt_maturity_near_term")

    evidence = {
        "survivability": survivability,
        "survivabilityBucket": scores.get("survivability_bucket"),
        "runwayMonths": runway_months,
        "interestCoverage": coverage,
        "fcf": fcf,
        "currentRatio": (inputs.get("metrics") or {}).get("current_ratio"),
        "cashToDebt": (inputs.get("metrics") or {}).get("cash_to_debt"),
        "debtMaturityNearTerm": debt_maturity_near_term,
        "edgarTriggers": edgar,
        "watchlistFlags": [],
    }

    if edgar.get("going_concern"):
        evidence["watchlistFlags"].append("going_concern_opinion")
        return _gate_result(
            "solvency_runway",
            "fail",
            evidence=evidence,
            triggered_by="going_concern_opinion",
        )
    if edgar.get("nt_filing"):
        evidence["watchlistFlags"].append("nt_filing")
        return _gate_result(
            "solvency_runway",
            "fail",
            evidence=evidence,
            triggered_by="nt_filing",
        )

    pass_reasons: list[str] = []
    if runway_months is not None and runway_months > GATE_RUNWAY_PASS_MONTHS:
        pass_reasons.append("runway_above_18_months")
    if (
        coverage is not None
        and coverage >= GATE_INTEREST_COVERAGE_PASS
        and survivability is not None
        and survivability >= GATE_SURVIVABILITY_STRONG
    ):
        pass_reasons.append("refinancing_optionality_coverage")
    if (
        survivability is not None
        and survivability >= GATE_SURVIVABILITY_STRONG
        and fcf is not None
        and fcf > 0
    ):
        pass_reasons.append("survivability_strong_positive_fcf")

    if pass_reasons:
        return _gate_result(
            "solvency_runway",
            "pass",
            evidence=evidence,
            triggered_by=pass_reasons[0],
        )

    if runway_months is None and fcf is not None and fcf >= 0:
        evidence["runwayMonths"] = None
        evidence["runwayInterpretation"] = "positive_fcf_no_burn"
        if survivability is not None and survivability >= GATE_SURVIVABILITY_STRONG:
            return _gate_result(
                "solvency_runway",
                "pass",
                evidence=evidence,
                triggered_by="survivability_strong_positive_fcf",
            )

    watchlist_flags: list[str] = []
    if runway_months is not None and runway_months < GATE_RUNWAY_PASS_MONTHS:
        watchlist_flags.append("runway_below_18_months")
    if debt_maturity_near_term:
        watchlist_flags.append("near_term_debt_maturity_wall")
    if coverage is not None and coverage < GATE_INTEREST_COVERAGE_FAIL:
        watchlist_flags.append("interest_coverage_below_1x")
    evidence["watchlistFlags"] = watchlist_flags

    fail_conditions_met = 0
    fail_conditions_available = 0
    if runway_months is not None:
        fail_conditions_available += 1
        if runway_months < GATE_RUNWAY_PASS_MONTHS:
            fail_conditions_met += 1
    if debt_maturity_near_term is not None:
        fail_conditions_available += 1
        if debt_maturity_near_term:
            fail_conditions_met += 1
    if coverage is not None:
        fail_conditions_available += 1
        if coverage < GATE_INTEREST_COVERAGE_FAIL:
            fail_conditions_met += 1

    if fail_conditions_available >= 2 and fail_conditions_met == fail_conditions_available:
        trigger = watchlist_flags[0] if watchlist_flags else "solvency_stress_composite"
        return _gate_result("solvency_runway", "fail", evidence=evidence, triggered_by=trigger)

    if (
        survivability is None
        and runway_months is None
        and coverage is None
        and fcf is None
    ):
        return _gate_result("solvency_runway", "unknown", evidence=evidence, triggered_by="insufficient_solvency_data")

    if watchlist_flags:
        return _gate_result(
            "solvency_runway",
            "fail" if survivability is not None and survivability < 40 else "unknown",
            evidence=evidence,
            triggered_by=watchlist_flags[0],
        )

    return _gate_result("solvency_runway", "unknown", evidence=evidence, triggered_by="insufficient_solvency_data")


def evaluate_accounting_integrity(inputs: dict[str, Any]) -> dict[str, Any]:
    """Gate 2 — is the reported financial picture a reliable basis for valuation?"""
    scores = inputs.get("scores") or {}
    latent = inputs.get("latent") or {}
    edgar = inputs.get("edgar_triggers") or {}

    beneish_m = scores.get("beneish_m")
    sloan = latent.get("sloan_accruals")

    evidence = {
        "beneishM": beneish_m,
        "beneishThreshold": BENEISH_MANIPULATION_THRESHOLD,
        "sloanAccruals": sloan,
        "sloanThreshold": SLOAN_ACCRUALS_HIGH_THRESHOLD,
        "edgarTriggers": edgar,
    }

    hard_triggers = [
        ("going_concern_opinion", edgar.get("going_concern")),
        ("nt_filing", edgar.get("nt_filing")),
        ("restatement_item_4_02", edgar.get("restatement")),
        ("auditor_change_12m", edgar.get("auditor_change_12m")),
        ("beneish_manipulation_probable", beneish_m is not None and beneish_m > BENEISH_MANIPULATION_THRESHOLD),
    ]
    for trigger_name, active in hard_triggers:
        if active:
            return _gate_result(
                "accounting_integrity",
                "fail",
                evidence=evidence,
                triggered_by=trigger_name,
            )

    if beneish_m is None and sloan is None:
        return _gate_result(
            "accounting_integrity",
            "unknown",
            evidence=evidence,
            triggered_by="missing_beneish_and_sloan",
        )

    soft_failures: list[str] = []
    if sloan is not None and sloan >= SLOAN_ACCRUALS_HIGH_THRESHOLD:
        soft_failures.append("high_sloan_accruals")

    if beneish_m is None:
        if soft_failures:
            return _gate_result(
                "accounting_integrity",
                "fail",
                evidence=evidence,
                triggered_by=soft_failures[0],
            )
        return _gate_result(
            "accounting_integrity",
            "unknown",
            evidence=evidence,
            triggered_by="missing_beneish_m",
        )

    if sloan is None:
        if beneish_m <= BENEISH_MANIPULATION_THRESHOLD:
            return _gate_result(
                "accounting_integrity",
                "pass",
                evidence=evidence,
                triggered_by="beneish_below_threshold",
            )
        return _gate_result(
            "accounting_integrity",
            "fail",
            evidence=evidence,
            triggered_by="beneish_above_threshold",
        )

    if beneish_m <= BENEISH_MANIPULATION_THRESHOLD and sloan < SLOAN_ACCRUALS_HIGH_THRESHOLD:
        return _gate_result(
            "accounting_integrity",
            "pass",
            evidence=evidence,
            triggered_by="beneish_and_sloan_clean",
        )

    trigger = "high_sloan_accruals" if sloan >= SLOAN_ACCRUALS_HIGH_THRESHOLD else "beneish_above_threshold"
    return _gate_result("accounting_integrity", "fail", evidence=evidence, triggered_by=trigger)


def evaluate_secular_decline(inputs: dict[str, Any]) -> dict[str, Any]:
    """Gate 3 — is cheapness explained by terminal structural decline?"""
    latent = inputs.get("latent") or {}
    margin_trends = inputs.get("margin_trends") or {}
    narrative_states = inputs.get("narrative_states") or []
    operational = inputs.get("operational_recovery") or {}

    time_cheap_periods = latent.get("time_cheap_periods")
    time_cheap_classification = latent.get("time_cheap_classification")
    peer_declining = latent.get("peer_industry_declining")
    peer_count = (latent.get("raw") or {}).get("peer_industry_trend", {}).get("peer_count", 0)
    gross_margin_delta = margin_trends.get("gross_margin_3yr_delta")
    operating_margin_delta = margin_trends.get("operating_margin_3yr_delta")

    narrative_state_names = {
        item.get("state")
        for item in narrative_states
        if isinstance(item, dict) and item.get("state")
    }
    turnaround_signal = bool(narrative_state_names & TURNAROUND_NARRATIVE_STATES)

    evidence = {
        "timeCheapPeriods": time_cheap_periods,
        "timeCheapClassification": time_cheap_classification,
        "peerIndustryDeclining": peer_declining,
        "peerCount": peer_count,
        "grossMargin3yrDelta": gross_margin_delta,
        "operatingMargin3yrDelta": operating_margin_delta,
        "operationalRecovery": operational.get("operationalRecovery"),
        "turnaroundNarrativeStates": sorted(narrative_state_names & TURNAROUND_NARRATIVE_STATES),
    }

    if peer_declining is None and (peer_count or 0) < 2:
        return _gate_result(
            "secular_decline",
            "unknown",
            evidence=evidence,
            triggered_by="insufficient_peer_history",
        )

    structural_cheap = (
        time_cheap_periods is not None
        and time_cheap_periods >= TIME_CHEAP_STRUCTURAL_YEARS
    ) or time_cheap_classification == "structural"
    company_declining = (
        (gross_margin_delta is not None and gross_margin_delta < -0.02)
        or (operating_margin_delta is not None and operating_margin_delta < -0.02)
    )
    operational_recovery = operational.get("operationalRecovery") is True
    peer_stable_or_growing = peer_declining is False

    pass_reasons: list[str] = []
    if structural_cheap and peer_stable_or_growing:
        pass_reasons.append("company_specific_decline_peer_group_healthy")
    if peer_declining is True and turnaround_signal:
        pass_reasons.append("cyclical_peer_decline_with_turnaround_signal")
    if operational_recovery or (
        gross_margin_delta is not None
        and gross_margin_delta > 0.01
        and (operating_margin_delta is None or operating_margin_delta > -0.01)
    ):
        pass_reasons.append("operational_recovery_evidence")
    if not structural_cheap and not company_declining:
        pass_reasons.append("no_structural_cheapness_or_decline")

    if pass_reasons:
        return _gate_result(
            "secular_decline",
            "pass",
            evidence=evidence,
            triggered_by=pass_reasons[0],
        )

    if (
        structural_cheap
        and peer_declining is True
        and operational.get("operationalRecovery") is False
    ):
        return _gate_result(
            "secular_decline",
            "fail",
            evidence=evidence,
            triggered_by="structural_cheapness_with_peer_decline",
        )

    if time_cheap_periods is None and gross_margin_delta is None and operating_margin_delta is None:
        return _gate_result(
            "secular_decline",
            "unknown",
            evidence=evidence,
            triggered_by="insufficient_decline_data",
        )

    return _gate_result("secular_decline", "unknown", evidence=evidence, triggered_by="borderline_decline_profile")


def evaluate_margin_of_safety(inputs: dict[str, Any]) -> dict[str, Any]:
    """Gate 4 — is there a credible conservative valuation basis below current price?"""
    metrics = inputs.get("metrics") or {}
    latent = inputs.get("latent") or {}
    derived = inputs.get("derived") or {}
    margin_trends = inputs.get("margin_trends") or {}

    price_to_nav = latent.get("price_to_conservative_nav")
    nav_per_share = latent.get("conservative_nav_per_share")
    owner_yield = derived.get("owner_earnings_yield")
    fcf_yield = derived.get("fcf_yield")
    fcf_streak = derived.get("fcf_positive_streak") or 0
    pe = metrics.get("pe")
    pb = metrics.get("pb")
    earnings_yield = metrics.get("earnings_yield")

    evidence = {
        "priceToConservativeNav": price_to_nav,
        "conservativeNavPerShare": nav_per_share,
        "price": inputs.get("price"),
        "ownerEarningsYield": owner_yield,
        "fcfYield": fcf_yield,
        "fcfPositiveStreak": fcf_streak,
        "pe": pe,
        "pb": pb,
        "earningsYield": earnings_yield,
        "grossMargin3yrDelta": margin_trends.get("gross_margin_3yr_delta"),
    }

    nav_discount = price_to_nav is not None and 0 < price_to_nav < 1.0
    owner_yield_pass = owner_yield is not None and owner_yield >= GATE_OWNER_EARNINGS_YIELD_PASS
    fcf_yield_pass = (
        fcf_yield is not None
        and fcf_yield >= GATE_FCF_YIELD_PASS
        and (fcf_streak >= 1 or (margin_trends.get("gross_margin_3yr_delta") or 0) > 0)
    )

    pass_reasons: list[str] = []
    if nav_discount:
        pass_reasons.append("price_below_conservative_nav")
    if owner_yield_pass:
        pass_reasons.append("owner_earnings_yield_above_10pct")
    if fcf_yield_pass:
        pass_reasons.append("fcf_yield_with_positive_trend")

    if pass_reasons:
        return _gate_result(
            "margin_of_safety",
            "pass",
            evidence=evidence,
            triggered_by=pass_reasons[0],
        )

    multiples_cheap = (
        (pe is not None and 0 < pe < 12)
        or (pb is not None and 0 < pb < 1.0)
        or (earnings_yield is not None and earnings_yield > 0.08)
    )
    nav_above_price = price_to_nav is not None and price_to_nav > 1.0
    no_conservative_anchor = not (nav_discount or owner_yield_pass or fcf_yield_pass)

    if multiples_cheap and no_conservative_anchor and nav_above_price:
        return _gate_result(
            "margin_of_safety",
            "fail",
            evidence=evidence,
            triggered_by="multiples_only_cheapness_above_haircut_nav",
        )

    if price_to_nav is None and owner_yield is None and fcf_yield is None:
        return _gate_result(
            "margin_of_safety",
            "unknown",
            evidence=evidence,
            triggered_by="missing_valuation_anchors",
        )

    if nav_above_price and not multiples_cheap:
        return _gate_result(
            "margin_of_safety",
            "unknown",
            evidence=evidence,
            triggered_by="above_nav_without_clear_cheapness",
        )

    return _gate_result("margin_of_safety", "unknown", evidence=evidence, triggered_by="insufficient_margin_of_safety_data")


def evaluate_gate_stack(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """Evaluate all four gates in deterministic order."""
    return [
        evaluate_solvency_runway(inputs),
        evaluate_accounting_integrity(inputs),
        evaluate_secular_decline(inputs),
        evaluate_margin_of_safety(inputs),
    ]


def summarize_gate_stack(gates: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [item["gate"] for item in gates if item.get("status") == "fail"]
    unknown = [item["gate"] for item in gates if item.get("status") == "unknown"]
    passed = [item["gate"] for item in gates if item.get("status") == "pass"]
    no_hard_fails = len(failed) == 0
    fully_evaluated = len(unknown) == 0
    return {
        "passedGates": passed,
        "failedGates": failed,
        "unknownGates": unknown,
        "allPassed": no_hard_fails and fully_evaluated,
        "noHardFails": no_hard_fails,
        "fullyEvaluated": fully_evaluated,
        "investable": no_hard_fails,
        "skipPillars": len(failed) > 0,
    }


def evaluate_gates_for_ticker(
    repo: Repository,
    ticker: str,
    *,
    prices_service: PricesService | None = None,
) -> dict[str, Any] | None:
    """Load inputs and evaluate the full gate stack for one ticker."""
    inputs = assemble_gate_inputs(repo, ticker, prices_service=prices_service)
    if inputs is None:
        return None

    gates = evaluate_gate_stack(inputs)
    summary = summarize_gate_stack(gates)
    return {
        "ticker": inputs["ticker"],
        "gates": gates,
        "summary": summary,
    }


def gates_to_api(payload: dict[str, Any]) -> dict[str, Any]:
    """Preserve gate payload for API consumers (camelCase summary keys only)."""
    summary = payload.get("summary") or {}
    return {
        "ticker": payload.get("ticker"),
        "gates": payload.get("gates") or [],
        "summary": {
            "passedGates": summary.get("passedGates") or [],
            "failedGates": summary.get("failedGates") or [],
            "unknownGates": summary.get("unknownGates") or [],
            "allPassed": bool(summary.get("allPassed")),
            "noHardFails": bool(summary.get("noHardFails")),
            "fullyEvaluated": bool(summary.get("fullyEvaluated")),
            "investable": bool(summary.get("investable")),
            "skipPillars": bool(summary.get("skipPillars")),
        },
    }
