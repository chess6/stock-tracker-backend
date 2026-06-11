"""Phase 3 — disconfirmation-first, template-driven investment thesis synthesis."""

from __future__ import annotations

import logging
from typing import Any, Literal

from ..repositories import Repository
from .gate_engine import assemble_gate_inputs, evaluate_gate_stack, summarize_gate_stack
from .pillar_engine import evaluate_pillars_for_ticker
from .prices import PricesService

logger = logging.getLogger("stock_tracker.thesis_engine")

Polarity = Literal["bull", "bear"]

SIGNAL_CLASSES = (
    "accounting",
    "market",
    "transaction",
    "operational",
    "governance",
    "text",
)

_FACTOR_SIGNAL_CLASS: dict[str, str] = {
    "conservative_nav_discount": "accounting",
    "owner_earnings_yield": "accounting",
    "valuation_dislocation": "market",
    "absolute_cheapness": "market",
    "survivability_score": "accounting",
    "runway_months": "accounting",
    "altman_zone": "accounting",
    "interest_coverage": "accounting",
    "peer_industry_trend": "operational",
    "gross_margin_stability": "operational",
    "revenue_trajectory": "operational",
    "capital_return_quality": "operational",
    "dilution_trend": "operational",
    "buyback_effectiveness": "operational",
    "insider_conviction": "transaction",
    "cluster_signal": "transaction",
    "buy_sell_balance": "transaction",
    "activist_presence": "governance",
    "sentiment_divergence": "text",
    "divergence_signal": "text",
    "narrative_states": "text",
    "earnings_quality": "accounting",
    "margin_stabilization": "operational",
    "fcf_quality": "operational",
}

_TEMPLATES: dict[tuple[str, str, str], str] = {
    ("survivability", "runway_months", "bear"): (
        "Cash runway {runwayMonths:.1f} months at current quarterly burn — "
        "operations may require dilutive capital before fundamentals recover."
    ),
    ("survivability", "interest_coverage", "bear"): (
        "Interest coverage {interestCoverage:.2f}x — operating income does not fully cover interest; "
        "requires EBIT improvement or refinancing."
    ),
    ("survivability", "survivability_score", "bear"): (
        "Survivability score {survivability:.0f}/100 ({survivabilityBucket}) — balance-sheet stress dominates the profile."
    ),
    ("valuation", "conservative_nav_discount", "bull"): (
        "Price trades at {priceToConservativeNav:.2f}x conservative haircut NAV "
        "(${conservativeNavPerShare:.2f}/share) — liquidation-style floor supports the case."
    ),
    ("valuation", "owner_earnings_yield", "bull"): (
        "Owner earnings yield {ownerEarningsYield:.1%} on enterprise value exceeds conservative hurdle."
    ),
    ("insider_conviction", "insider_conviction", "bull"): (
        "Insider open-market purchases ${buy6m:,.0f} (6m) suggest insiders view distress as manageable."
    ),
    ("insider_conviction", "cluster_signal", "bull"): (
        "Cluster buying: {buyCount} purchases, intensity {intensityScore:.2f} — multi-insider alignment."
    ),
    ("fundamental_trends", "margin_stabilization", "bull"): (
        "Gross margin trend {gross_margin_trend:+.1%} over 3yr — underlying economics stabilizing."
    ),
    ("fundamental_trends", "earnings_quality", "bear"): (
        "Sloan accruals {sloanAccruals:.3f} — earnings outrunning cash; revision risk to valuation denominator."
    ),
    ("business_durability", "peer_industry_trend", "bear"): (
        "Peer industry revenue/margin trajectory declining — cheapness may reflect secular, not company-specific, impairment."
    ),
    ("business_durability", "dilution_trend", "bear"): (
        "Share dilution rate {shareDilutionRate:.1%} — equity base expanding faster than per-share value."
    ),
    ("narrative_divergence", "sentiment_divergence", "bear"): (
        "Narrative divergence score {divergence_score:.2f} ({divergence_signal}) — text signals remain weak corroboration."
    ),
    ("turnaround_evidence", "margin_recovery_burst", "bull"): (
        "Recent margin recovery burst (Δ gross margin {grossMarginDelta:+.1%}) — operational change underway."
    ),
}


def _format_template(template: str, raw: dict[str, Any]) -> str:
    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return "—"

    safe = _SafeDict({k: v for k, v in raw.items() if v is not None})
    try:
        return template.format_map(safe)
    except (TypeError, ValueError, KeyError):
        return template


def _statement_from_factor(
    pillar: str,
    factor: dict[str, Any],
    *,
    polarity: Polarity,
) -> dict[str, Any] | None:
    key = factor.get("key")
    raw = factor.get("raw") or {}
    if not raw:
        return None
    template_key = (pillar, key, polarity)
    template = _TEMPLATES.get(template_key)
    if not template:
        normalized = factor.get("normalized")
        if normalized is None:
            return None
        direction = "supports" if polarity == "bull" else "weakens"
        template = f"Factor {key} ({normalized:.0%} normalized) {direction} the {polarity} case."
    text = _format_template(template, raw)
    return {
        "pillar": pillar,
        "factorKey": key,
        "polarity": polarity,
        "text": text,
        "raw": raw,
        "signalClass": _FACTOR_SIGNAL_CLASS.get(key, "operational"),
    }


def _collect_factor_statements(
    pillars: list[dict[str, Any]],
    *,
    polarity: Polarity,
    limit: int = 6,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for pillar in pillars:
        pillar_key = pillar.get("pillar") or ""
        for factor in pillar.get("factors") or []:
            normalized = factor.get("normalized")
            if normalized is None:
                continue
            is_bullish = float(normalized) >= 0.55
            is_bearish = float(normalized) <= 0.45
            if polarity == "bull" and not is_bullish:
                continue
            if polarity == "bear" and not is_bearish:
                continue
            stmt = _statement_from_factor(pillar_key, factor, polarity=polarity)
            if stmt:
                magnitude = abs(float(normalized) - 0.5)
                scored.append((magnitude, stmt))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[:limit]]


def _build_pre_mortem(
    *,
    gate_payload: dict[str, Any],
    gate_inputs: dict[str, Any],
    disqualified: bool,
) -> dict[str, Any]:
    latent = gate_inputs.get("latent") or {}
    summary = gate_payload.get("summary") or {}
    failed = summary.get("failedGates") or []
    statements: list[dict[str, Any]] = []

    time_cheap = latent.get("time_cheap_periods")
    classification = latent.get("time_cheap_classification")
    if time_cheap is not None and time_cheap >= 3:
        statements.append({
            "source": "time_cheap",
            "text": (
                f"Cheap for {time_cheap} consecutive annual periods ({classification or 'unknown'} classification) — "
                "base rate favors value trap over mean reversion."
            ),
            "raw": {"timeCheapPeriods": time_cheap, "timeCheapClassification": classification},
        })

    for gate in gate_payload.get("gates") or []:
        if gate.get("status") != "fail":
            continue
        evidence = gate.get("evidence") or {}
        statements.append({
            "source": gate.get("gate"),
            "text": (
                f"Gate failure: {gate.get('gate')} ({gate.get('triggered_by')}) — "
                "non-compensatory disqualifier; pillar strength cannot offset."
            ),
            "raw": evidence,
        })

    if not statements and not disqualified:
        statements.append({
            "source": "base_rate",
            "text": (
                "Even passing names face the deep-value base rate: most cheap stocks stay cheap or dilute. "
                "Treat any bull case as a rebuttal, not a premise."
            ),
            "raw": {},
        })

    headline = "Disqualified — gate failure dominates." if disqualified else "Pre-mortem: how you lose money here."
    if failed and not disqualified:
        headline = f"Watchlist: {len(failed)} gate concern(s) without full disqualification."

    return {"headline": headline, "statements": statements}


def _build_valuation_assessment(gate_inputs: dict[str, Any]) -> dict[str, Any]:
    latent = gate_inputs.get("latent") or {}
    derived = gate_inputs.get("derived") or {}
    metrics = gate_inputs.get("metrics") or {}
    anchors = {
        "conservativeNavPerShare": latent.get("conservative_nav_per_share"),
        "priceToConservativeNav": latent.get("price_to_conservative_nav"),
        "ownerEarningsYield": derived.get("owner_earnings_yield"),
        "fcfYield": derived.get("fcf_yield"),
        "pe": metrics.get("pe"),
        "pb": metrics.get("pb"),
        "price": gate_inputs.get("price"),
    }
    assumptions: list[str] = []
    p_to_nav = latent.get("price_to_conservative_nav")
    if p_to_nav is not None and p_to_nav < 1.0:
        assumptions.append("Haircut NAV remains recoverable; no hidden liability write-downs beyond modeled haircuts.")
    elif p_to_nav is not None and p_to_nav > 1.0:
        assumptions.append("Accounting book and haircut NAV may overstate recoverable asset value at current price.")
    if derived.get("owner_earnings_yield") is not None:
        assumptions.append("Normalized owner earnings are sustainable, not cyclically inflated.")
    return {
        "anchors": anchors,
        "assumptions": assumptions,
        "summary": (
            "Valuation rests on conservative anchors where present; trailing multiples alone are insufficient."
        ),
    }


def _build_catalyst_watchlist(
    gate_inputs: dict[str, Any],
    *,
    pillars: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    watchlist: list[dict[str, Any]] = []
    operational = gate_inputs.get("operational_recovery") or {}
    edgar = gate_inputs.get("edgar_triggers") or {}

    if operational.get("marginRecovery"):
        watchlist.append({
            "type": "margin_recovery_burst",
            "direction": "bullish",
            "dataBasis": "observed",
            "horizon": "developing",
            "evidence": operational,
        })
    if operational.get("fcfRecovery"):
        watchlist.append({
            "type": "fcf_inflection",
            "direction": "bullish",
            "dataBasis": "observed",
            "horizon": "developing",
            "evidence": operational,
        })

    activist = (gate_inputs.get("edgar") or {}).get("activist_filing")
    if activist:
        watchlist.append({
            "type": "activist_13d",
            "direction": "bullish",
            "dataBasis": "observed",
            "horizon": "near",
            "evidence": activist,
        })

    has_governance_catalyst = any(item["type"] == "activist_13d" for item in watchlist)
    turnaround_pillar = next((p for p in pillars if p.get("pillar") == "turnaround_evidence"), None)
    turnaround_score = (turnaround_pillar or {}).get("score")
    if turnaround_score is not None and float(turnaround_score) > 0.6 and not has_governance_catalyst:
        watchlist.append({
            "type": "missing_governance_catalyst",
            "direction": "risk",
            "dataBasis": "inferred",
            "horizon": "unknown",
            "evidence": {
                "message": (
                    "Value realization may require a governance catalyst (activism, asset sale, management change) "
                    "that this engine cannot observe. Known blind spot."
                ),
            },
        })

    if edgar.get("restatement"):
        watchlist.append({
            "type": "restatement_risk",
            "direction": "risk",
            "dataBasis": "observed",
            "horizon": "near",
            "evidence": {"restatement": True},
        })

    return watchlist


def _build_disconfirming_conditions(
    gate_payload: dict[str, Any],
    pillars: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []

    for gate in gate_payload.get("gates") or []:
        if gate.get("status") not in {"unknown", "pass"}:
            continue
        evidence = gate.get("evidence") or {}
        if gate.get("gate") == "solvency_runway" and evidence.get("runwayMonths") is not None:
            conditions.append({
                "text": (
                    f"Thesis is weakened materially if quarterly cash runway falls below 12 months "
                    f"(currently {evidence['runwayMonths']:.1f} months)."
                ),
                "factorKey": "runway_months",
                "raw": {"runwayMonths": evidence["runwayMonths"], "threshold": 12},
            })
        if gate.get("gate") == "margin_of_safety" and evidence.get("priceToConservativeNav") is not None:
            conditions.append({
                "text": (
                    "Thesis is weakened materially if price exceeds 1.0x conservative haircut NAV "
                    f"(currently {evidence['priceToConservativeNav']:.2f}x)."
                ),
                "factorKey": "conservative_nav_discount",
                "raw": {"priceToConservativeNav": evidence["priceToConservativeNav"], "threshold": 1.0},
            })

    borderline: list[tuple[float, dict[str, Any]]] = []
    for pillar in pillars:
        for factor in pillar.get("factors") or []:
            normalized = factor.get("normalized")
            if normalized is None:
                continue
            distance = abs(float(normalized) - 0.5)
            if distance <= 0.12:
                borderline.append((distance, {"pillar": pillar.get("pillar"), **factor}))

    borderline.sort(key=lambda item: item[0])
    for _, factor in borderline[:3]:
        key = factor.get("key")
        raw = factor.get("raw") or {}
        conditions.append({
            "text": f"Thesis is weakened materially if {key} flips polarity (currently borderline at {factor.get('normalized'):.0%}).",
            "factorKey": key,
            "raw": raw,
        })

    if len(conditions) < 2:
        conditions.append({
            "text": "Thesis is weakened materially if insider open-market buying ceases for two consecutive quarters.",
            "factorKey": "insider_conviction",
            "raw": {},
        })
        conditions.append({
            "text": "Thesis is weakened materially if Altman Z deteriorates into distress zone on the next annual filing.",
            "factorKey": "altman_zone",
            "raw": {},
        })

    return conditions[:5]


def _compute_evidence_coverage(pillars: list[dict[str, Any]]) -> dict[str, Any]:
    if not pillars:
        return {"overall": 0.0, "perPillar": {}}
    per_pillar = {
        pillar["pillar"]: pillar.get("evidenceCoverage") or 0.0
        for pillar in pillars
    }
    overall = sum(per_pillar.values()) / len(per_pillar) if per_pillar else 0.0
    return {"overall": round(overall, 4), "perPillar": per_pillar}


def _compute_signal_independence(
    bear_statements: list[dict[str, Any]],
    bull_statements: list[dict[str, Any]],
) -> dict[str, Any]:
    classes: set[str] = set()
    for stmt in bear_statements + bull_statements:
        cls = stmt.get("signalClass")
        if cls and cls != "text":
            classes.add(cls)

    count = len(classes)
    if count >= 3:
        label = "corroborated"
        description = "Three or more orthogonal data classes contribute — genuinely multi-source profile."
    elif count == 2:
        label = "suggestive"
        description = "Two orthogonal classes — suggestive but not fully corroborated."
    else:
        label = "single_class"
        description = "Single-class bet — correlated distress signals must not be double-counted."

    text_only = all(
        stmt.get("signalClass") == "text"
        for stmt in bull_statements
    ) and bool(bull_statements)

    return {
        "orthogonalClassCount": count,
        "orthogonalClasses": sorted(classes),
        "label": label,
        "description": description,
        "textOnlyBull": text_only,
        "warning": (
            "Narrative/text signals alone never constitute corroboration."
            if text_only
            else None
        ),
    }


def _pair_bull_rebuttals(
    bear_statements: list[dict[str, Any]],
    bull_statements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rebuttals: list[dict[str, Any]] = []
    used_bull: set[int] = set()
    for bear in bear_statements:
        bear_class = bear.get("signalClass")
        match_idx = None
        for idx, bull in enumerate(bull_statements):
            if idx in used_bull:
                continue
            if bull.get("signalClass") == bear_class or bull.get("pillar") == bear.get("pillar"):
                match_idx = idx
                break
        if match_idx is not None:
            used_bull.add(match_idx)
            rebuttals.append({
                "addressesBear": bear.get("text"),
                "rebuttal": bull_statements[match_idx].get("text"),
                "factorKey": bull_statements[match_idx].get("factorKey"),
                "raw": bull_statements[match_idx].get("raw"),
            })
    for idx, bull in enumerate(bull_statements):
        if idx not in used_bull:
            rebuttals.append({
                "addressesBear": None,
                "rebuttal": bull.get("text"),
                "factorKey": bull.get("factorKey"),
                "raw": bull.get("raw"),
            })
    return rebuttals


def build_thesis(
    *,
    ticker: str,
    gate_payload: dict[str, Any],
    gate_inputs: dict[str, Any],
    pillar_payload: dict[str, Any],
) -> dict[str, Any]:
    """Assemble ordered thesis sections from gates and pillar profiles."""
    summary = gate_payload.get("summary") or {}
    disqualified = bool(summary.get("skipPillars"))
    pillars = pillar_payload.get("pillars") or []

    pre_mortem = _build_pre_mortem(
        gate_payload=gate_payload,
        gate_inputs=gate_inputs,
        disqualified=disqualified,
    )

    if disqualified:
        return {
            "ticker": ticker,
            "disqualified": True,
            "disqualificationNotice": {
                "failedGates": summary.get("failedGates") or [],
                "gates": gate_payload.get("gates") or [],
                "dataToRevisit": [
                    gate.get("triggered_by")
                    for gate in gate_payload.get("gates") or []
                    if gate.get("status") == "fail"
                ],
            },
            "sections": {
                "preMortem": pre_mortem,
                "bearCase": _collect_factor_statements(pillars, polarity="bear", limit=4) if pillars else [],
                "bullCase": [],
                "valuationAssessment": _build_valuation_assessment(gate_inputs),
                "catalystWatchlist": _build_catalyst_watchlist(gate_inputs, pillars=pillars),
                "disconfirmingConditions": _build_disconfirming_conditions(gate_payload, pillars),
                "evidenceCoverage": _compute_evidence_coverage(pillars),
                "signalIndependence": _compute_signal_independence([], []),
            },
            "pillars": pillars,
            "gates": gate_payload.get("gates") or [],
        }

    bear_case = _collect_factor_statements(pillars, polarity="bear", limit=6)
    bull_raw = _collect_factor_statements(pillars, polarity="bull", limit=6)
    bull_case = _pair_bull_rebuttals(bear_case, bull_raw)

    return {
        "ticker": ticker,
        "disqualified": False,
        "sections": {
            "preMortem": pre_mortem,
            "bearCase": bear_case,
            "bullCase": bull_case,
            "valuationAssessment": _build_valuation_assessment(gate_inputs),
            "catalystWatchlist": _build_catalyst_watchlist(gate_inputs, pillars=pillars),
            "disconfirmingConditions": _build_disconfirming_conditions(gate_payload, pillars),
            "evidenceCoverage": _compute_evidence_coverage(pillars),
            "signalIndependence": _compute_signal_independence(bear_case, bull_raw),
        },
        "pillars": pillars,
        "gates": gate_payload.get("gates") or [],
    }


def evaluate_thesis_for_ticker(
    repo: Repository,
    ticker: str,
    *,
    prices_service: PricesService | None = None,
) -> dict[str, Any] | None:
    """Load gates, pillars, and assemble full thesis for one ticker."""
    symbol = ticker.strip().upper()
    if prices_service is None:
        prices_service = PricesService(repo)

    gate_inputs = assemble_gate_inputs(repo, symbol, prices_service=prices_service)
    if gate_inputs is None:
        return None

    gates = evaluate_gate_stack(gate_inputs)
    summary = summarize_gate_stack(gates)
    gate_payload = {"ticker": symbol, "gates": gates, "summary": summary}

    activist = repo.fetch_company_activist_filings(symbol, limit=1)
    gate_inputs["edgar"] = {"activist_filing": activist[0] if activist else None}

    pillar_payload = evaluate_pillars_for_ticker(
        repo,
        symbol,
        prices_service=prices_service,
        gate_payload=gate_payload,
    )
    if pillar_payload is None:
        return None

    return build_thesis(
        ticker=symbol,
        gate_payload=gate_payload,
        gate_inputs=gate_inputs,
        pillar_payload=pillar_payload,
    )


def thesis_to_api(payload: dict[str, Any]) -> dict[str, Any]:
    """Preserve thesis JSON contract for API consumers."""
    sections = payload.get("sections") or {}
    return {
        "ticker": payload.get("ticker"),
        "disqualified": bool(payload.get("disqualified")),
        "disqualificationNotice": payload.get("disqualificationNotice"),
        "sections": {
            "preMortem": sections.get("preMortem"),
            "bearCase": sections.get("bearCase") or [],
            "bullCase": sections.get("bullCase") or [],
            "valuationAssessment": sections.get("valuationAssessment"),
            "catalystWatchlist": sections.get("catalystWatchlist") or [],
            "disconfirmingConditions": sections.get("disconfirmingConditions") or [],
            "evidenceCoverage": sections.get("evidenceCoverage"),
            "signalIndependence": sections.get("signalIndependence"),
        },
        "pillars": payload.get("pillars") or [],
        "gates": payload.get("gates") or [],
        "summary": summarize_gate_stack(payload.get("gates") or []),
    }
