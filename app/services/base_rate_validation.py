"""Phase 6 — retrospective base-rate validation harness for the gate stack."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from ..repositories import Repository
from .gate_engine import evaluate_gate_stack, summarize_gate_stack
from .metric_primitives import TIME_CHEAP_STRUCTURAL_YEARS
from .scoring import altman_zone

logger = logging.getLogger("stock_tracker.base_rate_validation")

_DEFAULT_FORWARD_QUARTERS = 8
_DILUTION_THRESHOLD = 0.20


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _quarters_after(start: str, quarters: int) -> str:
    parsed = _parse_date(start)
    if parsed is None:
        return start
    return (parsed + timedelta(days=quarters * 91)).isoformat()


def _evaluate_gates_from_snapshot_row(
    repo: Repository,
    ticker: str,
    snapshot_date: str,
) -> dict[str, Any] | None:
    """
    Lightweight historical gate replay using scores/fundamentals on or before snapshot_date.
    Falls back to unknown gates when historical rows are missing.
    """
    from .gate_engine import (
        edgar_trigger_state_as_of,
        evaluate_accounting_integrity,
        evaluate_margin_of_safety,
        evaluate_secular_decline,
        evaluate_solvency_runway,
    )

    scores = repo.fetch_company_scores_on_or_before(ticker, snapshot_date, dimension="ARY")
    wide = repo.fetch_fundamentals_wide_on_or_before(ticker, snapshot_date, dimension="MRY")
    if not scores and not wide:
        return None

    price = repo.fetch_price_near_date(ticker, snapshot_date)
    latent = {}
    if wide:
        from .latent_metrics import compute_latent_metrics

        company = repo.get_company_by_ticker(ticker)
        latent = compute_latent_metrics(
            repo,
            ticker,
            row=wide,
            price=price,
            sector=(company or {}).get("sector"),
        )

    metrics = {}
    derived: dict[str, Any] = {}
    if wide:
        from .metrics_engine import compute_period_metrics
        from .metric_primitives import free_cash_flow, interest_coverage

        metrics = compute_period_metrics(wide, price=price)
        fcf = free_cash_flow(wide)
        derived = {
            "fcf": fcf,
            "fcf_yield": None,
            "owner_earnings_yield": None,
            "fcf_positive_streak": 0,
            "interest_coverage": metrics.get("interest_coverage") or interest_coverage(wide),
        }

    flags = repo.fetch_company_edgar_flags(ticker)
    events = repo.fetch_company_edgar_events(ticker, limit=50)
    as_of = _parse_date(snapshot_date)
    edgar_triggers = (
        edgar_trigger_state_as_of(flags, events, as_of)
        if as_of is not None
        else {
            "going_concern": False,
            "nt_filing": False,
            "restatement": False,
            "auditor_change_12m": False,
        }
    )

    inputs = {
        "ticker": ticker,
        "scores": scores or {},
        "latent": latent,
        "metrics": metrics,
        "derived": derived,
        "edgar_triggers": edgar_triggers,
        "operational_recovery": {"operationalRecovery": None},
        "margin_trends": {},
        "narrative_states": [],
        "price": price,
        "row": wide,
        "annual_rows": [],
    }

    gates = [
        evaluate_solvency_runway(inputs),
        evaluate_accounting_integrity(inputs),
        evaluate_secular_decline(inputs),
        evaluate_margin_of_safety(inputs),
    ]
    summary = summarize_gate_stack(gates)
    return {"gates": gates, "summary": summary}


def _shares_on_or_before(repo: Repository, ticker: str, as_of_date: str) -> float | None:
    row = repo.fetch_fundamentals_wide_on_or_before(ticker, as_of_date, dimension="MRY")
    if not row:
        return None
    shares = row.get("sharesbas")
    return float(shares) if shares not in (None, 0) else None


def _altman_on_or_before(repo: Repository, ticker: str, as_of_date: str) -> str | None:
    scores = repo.fetch_company_scores_on_or_before(ticker, as_of_date, dimension="ARY")
    if not scores:
        return None
    return altman_zone(scores.get("altmanZ"))


def _going_concern_after(repo: Repository, ticker: str, after_date: str) -> bool:
    flags = repo.fetch_company_edgar_flags(ticker)
    cutoff = _parse_date(after_date)
    if cutoff is None:
        return False
    for flag in flags:
        if flag.get("flag_type") != "going_concern":
            continue
        filed = _parse_date(flag.get("filed_date") or flag.get("as_of_date"))
        if filed and filed > cutoff:
            return True
    return False


def _sustained_cheapness(
    repo: Repository,
    ticker: str,
    *,
    snapshot_date: str,
    forward_date: str,
) -> bool:
    from .latent_metrics import compute_latent_metrics

    company = repo.get_company_by_ticker(ticker)
    wide = repo.fetch_fundamentals_wide_on_or_before(ticker, forward_date, dimension="MRY")
    if not wide:
        return False
    price = repo.fetch_price_near_date(ticker, forward_date)
    latent = compute_latent_metrics(
        repo,
        ticker,
        row=wide,
        price=price,
        sector=(company or {}).get("sector"),
    )
    periods = latent.get("time_cheap_periods")
    classification = latent.get("time_cheap_classification")
    return (
        (periods is not None and periods >= TIME_CHEAP_STRUCTURAL_YEARS)
        or classification == "structural"
    )


def _measure_forward_outcomes(
    repo: Repository,
    ticker: str,
    *,
    snapshot_date: str,
    forward_quarters: int,
) -> dict[str, Any]:
    forward_date = _quarters_after(snapshot_date, forward_quarters)
    shares_start = _shares_on_or_before(repo, ticker, snapshot_date)
    shares_end = _shares_on_or_before(repo, ticker, forward_date)
    dilution = None
    serial_dilution = False
    if shares_start and shares_end and shares_start > 0:
        dilution = (shares_end - shares_start) / shares_start
        serial_dilution = dilution >= _DILUTION_THRESHOLD

    altman_start = _altman_on_or_before(repo, ticker, snapshot_date)
    altman_end = _altman_on_or_before(repo, ticker, forward_date)
    altman_deterioration = (
        altman_start in {"safe", "grey"}
        and altman_end == "distress"
    )

    going_concern = _going_concern_after(repo, ticker, snapshot_date)
    sustained_cheap = _sustained_cheapness(
        repo,
        ticker,
        snapshot_date=snapshot_date,
        forward_date=forward_date,
    )

    value_trap_hit = any([
        serial_dilution,
        altman_deterioration,
        going_concern,
        sustained_cheap,
    ])

    return {
        "forwardDate": forward_date,
        "serialDilution": serial_dilution,
        "dilutionPct": round(dilution, 4) if dilution is not None else None,
        "altmanDeterioration": altman_deterioration,
        "goingConcernFiling": going_concern,
        "sustainedCheapness": sustained_cheap,
        "valueTrapHit": value_trap_hit,
    }


def validate_gate_base_rates(
    repo: Repository,
    *,
    composite: str = "deep_value",
    snapshot_limit: int = 12,
    forward_quarters: int = _DEFAULT_FORWARD_QUARTERS,
    max_tickers_per_snapshot: int = 80,
) -> tuple[dict[str, Any] | None, int, str | None]:
    """
    Retrospective harness: of names passing all gates at snapshot T,
    what fraction hit adverse outcomes by T+forward_quarters?
    """
    composite_key = (composite or "deep_value").strip().lower()
    snapshot_dates = repo.fetch_distinct_rank_snapshot_dates(
        composite=composite_key,
        limit=snapshot_limit,
    )
    if not snapshot_dates:
        return None, 404, "insufficient_history"

    all_pass_outcomes: list[dict[str, Any]] = []
    gate1_fail_outcomes: list[dict[str, Any]] = []
    per_snapshot: list[dict[str, Any]] = []

    for snap_date in snapshot_dates:
        rows = repo.fetch_rank_snapshot_rows(composite=composite_key, snapshot_date=snap_date)
        tickers = [row["ticker"] for row in rows[:max_tickers_per_snapshot]]
        if not tickers:
            continue

        passed: list[str] = []
        gate1_failed: list[str] = []
        for ticker in tickers:
            gate_payload = _evaluate_gates_from_snapshot_row(repo, ticker, snap_date)
            if not gate_payload:
                continue
            summary = gate_payload["summary"]
            if summary.get("allPassed"):
                passed.append(ticker)
            failed = summary.get("failedGates") or []
            if "solvency_runway" in failed:
                gate1_failed.append(ticker)

        snap_pass_hits = 0
        for ticker in passed:
            outcome = _measure_forward_outcomes(
                repo,
                ticker,
                snapshot_date=snap_date,
                forward_quarters=forward_quarters,
            )
            outcome["ticker"] = ticker
            outcome["snapshotDate"] = snap_date
            outcome["gateProfile"] = "all_passed"
            all_pass_outcomes.append(outcome)
            if outcome["valueTrapHit"]:
                snap_pass_hits += 1

        snap_gate1_hits = 0
        for ticker in gate1_failed:
            outcome = _measure_forward_outcomes(
                repo,
                ticker,
                snapshot_date=snap_date,
                forward_quarters=forward_quarters,
            )
            outcome["ticker"] = ticker
            outcome["snapshotDate"] = snap_date
            outcome["gateProfile"] = "gate1_failed"
            gate1_fail_outcomes.append(outcome)
            if outcome["valueTrapHit"]:
                snap_gate1_hits += 1

        per_snapshot.append({
            "snapshotDate": snap_date,
            "evaluated": len(tickers),
            "allPassedCount": len(passed),
            "gate1FailedCount": len(gate1_failed),
            "valueTrapHitRateAllPassed": round(snap_pass_hits / len(passed), 4) if passed else None,
            "valueTrapHitRateGate1Failed": round(snap_gate1_hits / len(gate1_failed), 4) if gate1_failed else None,
        })

    if not all_pass_outcomes and not gate1_fail_outcomes:
        return None, 404, "insufficient_outcome_data"

    all_pass_hit_rate = (
        sum(1 for item in all_pass_outcomes if item["valueTrapHit"]) / len(all_pass_outcomes)
        if all_pass_outcomes
        else None
    )
    gate1_fail_hit_rate = (
        sum(1 for item in gate1_fail_outcomes if item["valueTrapHit"]) / len(gate1_fail_outcomes)
        if gate1_fail_outcomes
        else None
    )

    outcome_breakdown = {
        "serialDilution": sum(1 for item in all_pass_outcomes if item.get("serialDilution")),
        "altmanDeterioration": sum(1 for item in all_pass_outcomes if item.get("altmanDeterioration")),
        "goingConcernFiling": sum(1 for item in all_pass_outcomes if item.get("goingConcernFiling")),
        "sustainedCheapness": sum(1 for item in all_pass_outcomes if item.get("sustainedCheapness")),
    }

    logger.info(
        "validate_gate_base_rates composite=%s snapshots=%d all_pass=%d hit_rate=%s",
        composite_key,
        len(snapshot_dates),
        len(all_pass_outcomes),
        all_pass_hit_rate,
    )

    return {
        "meta": {
            "composite": composite_key,
            "snapshotDates": snapshot_dates,
            "forwardQuarters": forward_quarters,
            "evaluatedAllPassed": len(all_pass_outcomes),
            "evaluatedGate1Failed": len(gate1_fail_outcomes),
        },
        "valueTrapHitRate": {
            "allGatesPassed": round(all_pass_hit_rate, 4) if all_pass_hit_rate is not None else None,
            "gate1Failed": round(gate1_fail_hit_rate, 4) if gate1_fail_hit_rate is not None else None,
        },
        "outcomeBreakdown": outcome_breakdown,
        "perSnapshot": per_snapshot,
        "samples": {
            "allPassed": all_pass_outcomes[:25],
            "gate1Failed": gate1_fail_outcomes[:25],
        },
    }, 200, None
