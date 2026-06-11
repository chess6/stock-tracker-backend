"""Phase 2 — eight independent pillar dimension profiles (never summed across pillars)."""

from __future__ import annotations

import logging
import math
from typing import Any, Callable

from ..repositories import Repository
from .composite_ranking import (
    _factor_fcf_quality,
    _factor_insider_conviction,
    _factor_margin_stabilization,
    _factor_sentiment_divergence,
    _factor_survivability,
    _factor_valuation_dislocation,
)
from .gate_engine import assemble_gate_inputs, evaluate_gate_stack, summarize_gate_stack
from .metric_primitives import GATE_FCF_YIELD_PASS, GATE_OWNER_EARNINGS_YIELD_PASS, safe_div
from .prices import PricesService
from .scoring import altman_zone, _gross_margin
from .screening import build_research_candidates
from .sector_stats import build_sector_stats

logger = logging.getLogger("stock_tracker.pillar_engine")

_FACTOR_FN = Callable[[dict, dict[str, Any]], dict[str, Any] | None]

_SECTOR_PERCENTILE_METRICS = frozenset({
    "pe",
    "pb",
    "grossMargin",
    "fcfMargin",
    "de",
    "roe",
    "earningsYield",
})

PILLAR_KEYS = (
    "valuation",
    "survivability",
    "business_durability",
    "capital_quality",
    "insider_conviction",
    "fundamental_trends",
    "turnaround_evidence",
    "narrative_divergence",
)

_PILLAR_LABELS = {
    "valuation": "Valuation",
    "survivability": "Survivability",
    "business_durability": "Business Durability",
    "capital_quality": "Capital Quality",
    "insider_conviction": "Insider Conviction",
    "fundamental_trends": "Fundamental Trends",
    "turnaround_evidence": "Turnaround Evidence",
    "narrative_divergence": "Narrative Divergence",
}

_PILLAR_DATA_CLASSES: dict[str, list[str]] = {
    "valuation": ["accounting", "market"],
    "survivability": ["accounting", "transaction"],
    "business_durability": ["accounting", "operational"],
    "capital_quality": ["accounting", "transaction", "governance"],
    "insider_conviction": ["transaction", "governance"],
    "fundamental_trends": ["accounting", "operational"],
    "turnaround_evidence": ["accounting", "operational", "governance"],
    "narrative_divergence": ["text"],
}


def _finite(value: float | None) -> bool:
    return value is not None and value == value and abs(value) != float("inf")


def _factor_conservative_nav_discount(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    latent = candidate.get("latent") or {}
    p_to_nav = latent.get("price_to_conservative_nav")
    if p_to_nav is None or not _finite(float(p_to_nav)) or float(p_to_nav) <= 0:
        return None
    # Below 1.0 NAV is bullish; map 1.5 → 0, 0.5 → 1
    normalized = max(0.0, min(1.0, (1.5 - float(p_to_nav)) / 1.0))
    return {
        "normalized": normalized,
        "raw": {
            "priceToConservativeNav": float(p_to_nav),
            "conservativeNavPerShare": latent.get("conservative_nav_per_share"),
            "price": candidate.get("price"),
        },
    }


def _factor_owner_earnings_yield(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    derived = candidate.get("derived") or {}
    owner_yield = derived.get("owner_earnings_yield")
    if owner_yield is None or not _finite(float(owner_yield)):
        return None
    normalized = max(0.0, min(1.0, float(owner_yield) / max(GATE_OWNER_EARNINGS_YIELD_PASS * 1.5, 0.01)))
    return {"normalized": normalized, "raw": {"ownerEarningsYield": float(owner_yield)}}


def _factor_absolute_cheapness(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    derived = candidate.get("derived") or {}
    metrics = candidate.get("metrics") or {}
    fcf_yield = derived.get("fcf_yield")
    ebitda_ev = metrics.get("ebitda_ev")
    ranks: list[float] = []
    raw: dict[str, float | None] = {
        "fcfYield": float(fcf_yield) if _finite(fcf_yield) else None,
        "ebitdaEv": float(ebitda_ev) if _finite(ebitda_ev) else None,
    }
    if _finite(fcf_yield):
        ranks.append(max(0.0, min(1.0, float(fcf_yield) / max(GATE_FCF_YIELD_PASS * 1.5, 0.01))))
    if _finite(ebitda_ev) and float(ebitda_ev) > 0:
        ranks.append(max(0.0, min(1.0, (12.0 - float(ebitda_ev)) / 10.0)))
    if not ranks:
        return None
    return {"normalized": sum(ranks) / len(ranks), "raw": raw}


def _factor_runway_months(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    latent = candidate.get("latent") or {}
    runway = latent.get("runway_months")
    if runway is None or not _finite(float(runway)):
        return None
    normalized = max(0.0, min(1.0, float(runway) / 36.0))
    return {"normalized": normalized, "raw": {"runwayMonths": float(runway)}}


def _factor_altman_zone_score(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    altman = (candidate.get("scores") or {}).get("altmanZ")
    zone = altman_zone(altman)
    if zone is None:
        return None
    zone_scores = {"safe": 0.9, "grey": 0.5, "distress": 0.1}
    return {"normalized": zone_scores[zone], "raw": {"altmanZ": altman, "altmanZone": zone}}


def _factor_interest_coverage(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    coverage = (candidate.get("metrics") or {}).get("interest_coverage")
    if coverage is None or not _finite(float(coverage)):
        return None
    normalized = max(0.0, min(1.0, float(coverage) / 5.0))
    return {"normalized": normalized, "raw": {"interestCoverage": float(coverage)}}


def _factor_debt_maturity_risk(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    near_term = (candidate.get("edgar") or {}).get("debt_maturity_near_term")
    if near_term is None:
        return None
    normalized = 0.2 if near_term else 0.8
    return {"normalized": normalized, "raw": {"debtMaturityNearTerm": near_term}}


def _factor_peer_industry_trend(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    latent = candidate.get("latent") or {}
    declining = latent.get("peer_industry_declining")
    if declining is None:
        return None
    normalized = 0.25 if declining else 0.75
    return {
        "normalized": normalized,
        "raw": {
            "peerIndustryDeclining": declining,
            "peerRevenueCagr3yr": latent.get("peer_industry_revenue_cagr_3yr"),
            "peerMarginDelta3yr": latent.get("peer_industry_margin_delta_3yr"),
        },
    }


def _factor_gross_margin_stability(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    stability = (candidate.get("derived") or {}).get("gross_margin_stability")
    if stability is None or not _finite(float(stability)):
        return None
    normalized = max(0.0, min(1.0, 1.0 - float(stability) * 4.0))
    return {"normalized": normalized, "raw": {"grossMarginStability": float(stability)}}


def _factor_revenue_trajectory(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    trend = (candidate.get("derived") or {}).get("revenue_trajectory")
    if trend is None or not _finite(float(trend)):
        return None
    normalized = max(0.0, min(1.0, (float(trend) + 0.15) / 0.30))
    return {"normalized": normalized, "raw": {"revenueTrajectory": float(trend)}}


def _factor_capital_return_quality(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    latent = candidate.get("latent") or {}
    score = latent.get("capital_allocation_score")
    if score is None or not _finite(float(score)):
        return None
    normalized = max(0.0, min(1.0, float(score) / 100.0))
    cap_raw = (latent.get("raw") or {}).get("capital_allocation") or {}
    return {"normalized": normalized, "raw": cap_raw}


def _factor_dilution_trend(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    dilution = (candidate.get("derived") or {}).get("dilution_rate")
    if dilution is None or not _finite(float(dilution)):
        return None
    normalized = max(0.0, min(1.0, 0.5 - float(dilution) * 2.0))
    return {"normalized": normalized, "raw": {"shareDilutionRate": float(dilution)}}


def _factor_buyback_effectiveness(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    cap_raw = (candidate.get("latent") or {}).get("raw", {}).get("capital_allocation") or {}
    pct = cap_raw.get("buyback_at_discount_pct")
    if pct is None or not _finite(float(pct)):
        return None
    return {"normalized": max(0.0, min(1.0, float(pct))), "raw": {"buybackAtDiscountPct": float(pct)}}


def _factor_dilution_discipline(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    cap_raw = (candidate.get("latent") or {}).get("raw", {}).get("capital_allocation") or {}
    rate = cap_raw.get("dilution_rate_3yr")
    if rate is None or not _finite(float(rate)):
        return None
    normalized = max(0.0, min(1.0, 0.5 - float(rate) * 2.0))
    return {"normalized": normalized, "raw": {"dilutionRate3yr": float(rate)}}


def _factor_dividend_coverage(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    cap_raw = (candidate.get("latent") or {}).get("raw", {}).get("capital_allocation") or {}
    coverage = cap_raw.get("dividend_fcf_coverage")
    if coverage is None or not _finite(float(coverage)):
        return None
    normalized = max(0.0, min(1.0, min(float(coverage), 2.0) / 2.0))
    return {"normalized": normalized, "raw": {"dividendFcfCoverage": float(coverage)}}


def _factor_insider_ownership_level(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    ownership = (candidate.get("edgar") or {}).get("insider_ownership_pct")
    if ownership is None or not _finite(float(ownership)):
        return None
    normalized = max(0.0, min(1.0, float(ownership) / 15.0))
    return {"normalized": normalized, "raw": {"insiderOwnershipPct": float(ownership)}}


def _factor_capital_raises_vs_value(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    cap_raw = (candidate.get("latent") or {}).get("raw", {}).get("capital_allocation") or {}
    delta = cap_raw.get("equity_raises_vs_retained_earnings")
    if delta is None or not _finite(float(delta)):
        return None
    normalized = max(0.0, min(1.0, 0.5 + math.tanh(float(delta) / 1_000_000_000.0) * 0.5))
    return {"normalized": normalized, "raw": {"equityRaisesVsRetainedEarnings": float(delta)}}


def _factor_cluster_signal(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    cluster = candidate.get("insider_cluster") or {}
    intensity = cluster.get("intensity_score")
    buy_count = cluster.get("buy_count")
    if intensity is None and not buy_count:
        return None
    normalized = float(intensity) if intensity is not None else min(1.0, (buy_count or 0) / 5.0)
    return {
        "normalized": max(0.0, min(1.0, normalized)),
        "raw": {
            "intensityScore": intensity,
            "buyCount": buy_count,
            "totalBuyValue": cluster.get("total_buy_value"),
            "uniqueBuyers": cluster.get("unique_buyers"),
        },
    }


def _factor_buy_sell_balance(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    insider = candidate.get("insider") or {}
    ratio = insider.get("buy_sell_ratio")
    if ratio is None or not _finite(float(ratio)):
        buy6m = insider.get("buy6m") or 0
        sell6m = insider.get("sell6m") or 0
        if buy6m <= 0 and sell6m <= 0:
            return None
        ratio = safe_div(buy6m, sell6m) or (2.0 if buy6m > 0 else 0.0)
    normalized = max(0.0, min(1.0, float(ratio) / 3.0))
    return {"normalized": normalized, "raw": {"buySellRatio": float(ratio)}}


def _factor_activist_presence(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    activist = (candidate.get("edgar") or {}).get("activist_filing")
    if not activist:
        return None
    return {"normalized": 0.85, "raw": activist}


def _factor_earnings_quality(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    sloan = (candidate.get("latent") or {}).get("sloan_accruals")
    if sloan is None or not _finite(float(sloan)):
        return None
    normalized = max(0.0, min(1.0, 0.5 - float(sloan) * 5.0))
    return {"normalized": normalized, "raw": {"sloanAccruals": float(sloan)}}


def _factor_operating_leverage_inflection(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    derived = candidate.get("derived") or {}
    gm = derived.get("gross_margin_trend")
    om = derived.get("operating_margin_trend")
    if gm is None and om is None:
        return None
    gm_val = float(gm) if gm is not None else 0.0
    om_val = float(om) if om is not None else 0.0
    inflection = om_val - gm_val
    normalized = max(0.0, min(1.0, (inflection + 0.1) / 0.2))
    return {
        "normalized": normalized,
        "raw": {"grossMarginTrend": gm, "operatingMarginTrend": om, "inflection": inflection},
    }


def _factor_altman_improvement(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    delta = (candidate.get("derived") or {}).get("altman_delta")
    if delta is None or not _finite(float(delta)):
        return None
    normalized = max(0.0, min(1.0, (float(delta) + 0.5) / 1.0))
    return {"normalized": normalized, "raw": {"altmanDelta": float(delta)}}


def _factor_de_improvement(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    delta = (candidate.get("derived") or {}).get("de_delta")
    if delta is None or not _finite(float(delta)):
        return None
    normalized = max(0.0, min(1.0, 0.5 - float(delta)))
    return {"normalized": normalized, "raw": {"deDelta": float(delta)}}


def _factor_margin_recovery_burst(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    operational = candidate.get("operational_recovery") or {}
    if not operational.get("marginRecovery"):
        return None
    delta = operational.get("grossMarginDelta")
    normalized = max(0.0, min(1.0, 0.55 + (float(delta or 0) * 2.0)))
    return {"normalized": normalized, "raw": operational}


def _factor_fcf_stabilization(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    operational = candidate.get("operational_recovery") or {}
    if not operational.get("fcfRecovery"):
        return None
    return {"normalized": 0.75, "raw": operational}


def _factor_restructuring_signal(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    events = (candidate.get("edgar") or {}).get("restructuring_events") or []
    if not events:
        return None
    return {"normalized": 0.7, "raw": {"restructuringEvents": events[:3]}}


def _factor_divergence_signal(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    narrative = candidate.get("narrative") or {}
    signal = narrative.get("divergence_signal")
    if not signal:
        return None
    signal_scores = {
        "rerating_candidate": 0.85,
        "high_conviction": 0.8,
        "neutral": 0.5,
        "risk_flag": 0.2,
    }
    return {
        "normalized": signal_scores.get(signal, 0.5),
        "raw": {"divergenceSignal": signal},
    }


def _factor_price_sentiment_disconnect(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    narrative = candidate.get("narrative") or {}
    score = narrative.get("divergence_score")
    sentiment = narrative.get("sentiment_90d")
    if score is None and sentiment is None:
        return None
    normalized = float(score) if score is not None else max(0.0, min(1.0, 0.5 - float(sentiment or 0)))
    return {
        "normalized": max(0.0, min(1.0, normalized)),
        "raw": {"divergenceScore": score, "sentiment90d": sentiment},
    }


def _factor_narrative_states(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    states = candidate.get("narrative_states") or []
    if not states:
        return None
    positive = sum(1 for item in states if isinstance(item, dict) and item.get("state") in {
        "turnaround_optimism", "cyclical_recovery", "restructuring",
    })
    negative = sum(1 for item in states if isinstance(item, dict) and item.get("state") in {
        "bankruptcy_fear", "distress_narrative",
    })
    normalized = max(0.0, min(1.0, 0.5 + (positive - negative) * 0.15))
    return {"normalized": normalized, "raw": {"narrativeStates": states[:5]}}


_FACTOR_IMPL: dict[str, _FACTOR_FN] = {
    "_factor_conservative_nav_discount": _factor_conservative_nav_discount,
    "_factor_owner_earnings_yield": _factor_owner_earnings_yield,
    "_factor_valuation_dislocation": _factor_valuation_dislocation,
    "_factor_absolute_cheapness": _factor_absolute_cheapness,
    "_factor_survivability": _factor_survivability,
    "_factor_runway_months": _factor_runway_months,
    "_factor_altman_zone_score": _factor_altman_zone_score,
    "_factor_interest_coverage": _factor_interest_coverage,
    "_factor_debt_maturity_risk": _factor_debt_maturity_risk,
    "_factor_peer_industry_trend": _factor_peer_industry_trend,
    "_factor_gross_margin_stability": _factor_gross_margin_stability,
    "_factor_revenue_trajectory": _factor_revenue_trajectory,
    "_factor_capital_return_quality": _factor_capital_return_quality,
    "_factor_dilution_trend": _factor_dilution_trend,
    "_factor_buyback_effectiveness": _factor_buyback_effectiveness,
    "_factor_dilution_discipline": _factor_dilution_discipline,
    "_factor_dividend_coverage": _factor_dividend_coverage,
    "_factor_insider_ownership_level": _factor_insider_ownership_level,
    "_factor_capital_raises_vs_value": _factor_capital_raises_vs_value,
    "_factor_insider_conviction": _factor_insider_conviction,
    "_factor_cluster_signal": _factor_cluster_signal,
    "_factor_buy_sell_balance": _factor_buy_sell_balance,
    "_factor_activist_presence": _factor_activist_presence,
    "_factor_margin_stabilization": _factor_margin_stabilization,
    "_factor_fcf_quality": _factor_fcf_quality,
    "_factor_earnings_quality": _factor_earnings_quality,
    "_factor_operating_leverage_inflection": _factor_operating_leverage_inflection,
    "_factor_altman_improvement": _factor_altman_improvement,
    "_factor_de_improvement": _factor_de_improvement,
    "_factor_margin_recovery_burst": _factor_margin_recovery_burst,
    "_factor_fcf_stabilization": _factor_fcf_stabilization,
    "_factor_restructuring_signal": _factor_restructuring_signal,
    "_factor_sentiment_divergence": _factor_sentiment_divergence,
    "_factor_divergence_signal": _factor_divergence_signal,
    "_factor_price_sentiment_disconnect": _factor_price_sentiment_disconnect,
    "_factor_narrative_states": _factor_narrative_states,
}

_PILLAR_DEFINITIONS: dict[str, dict[str, Any]] = {
    "valuation": {
        "factors": [
            ("conservative_nav_discount", 0.35, "_factor_conservative_nav_discount"),
            ("owner_earnings_yield", 0.30, "_factor_owner_earnings_yield"),
            ("valuation_dislocation", 0.20, "_factor_valuation_dislocation"),
            ("absolute_cheapness", 0.15, "_factor_absolute_cheapness"),
        ],
    },
    "survivability": {
        "factors": [
            ("survivability_score", 0.30, "_factor_survivability"),
            ("runway_months", 0.25, "_factor_runway_months"),
            ("altman_zone", 0.20, "_factor_altman_zone_score"),
            ("interest_coverage", 0.15, "_factor_interest_coverage"),
            ("debt_maturity_risk", 0.10, "_factor_debt_maturity_risk"),
        ],
    },
    "business_durability": {
        "factors": [
            ("peer_industry_trend", 0.25, "_factor_peer_industry_trend"),
            ("gross_margin_stability", 0.20, "_factor_gross_margin_stability"),
            ("revenue_trajectory", 0.20, "_factor_revenue_trajectory"),
            ("capital_return_quality", 0.20, "_factor_capital_return_quality"),
            ("dilution_trend", 0.15, "_factor_dilution_trend"),
        ],
    },
    "capital_quality": {
        "factors": [
            ("buyback_effectiveness", 0.25, "_factor_buyback_effectiveness"),
            ("dilution_discipline", 0.25, "_factor_dilution_discipline"),
            ("dividend_coverage", 0.20, "_factor_dividend_coverage"),
            ("insider_ownership_level", 0.15, "_factor_insider_ownership_level"),
            ("capital_raises_vs_value_created", 0.15, "_factor_capital_raises_vs_value"),
        ],
    },
    "insider_conviction": {
        "factors": [
            ("insider_conviction", 0.30, "_factor_insider_conviction"),
            ("cluster_signal", 0.25, "_factor_cluster_signal"),
            ("buy_sell_balance", 0.20, "_factor_buy_sell_balance"),
            ("ownership_level", 0.15, "_factor_insider_ownership_level"),
            ("activist_presence", 0.10, "_factor_activist_presence"),
        ],
    },
    "fundamental_trends": {
        "factors": [
            ("margin_stabilization", 0.25, "_factor_margin_stabilization"),
            ("fcf_quality", 0.25, "_factor_fcf_quality"),
            ("earnings_quality", 0.20, "_factor_earnings_quality"),
            ("revenue_trajectory", 0.15, "_factor_revenue_trajectory"),
            ("operating_leverage_inflection", 0.15, "_factor_operating_leverage_inflection"),
        ],
    },
    "turnaround_evidence": {
        "factors": [
            ("altman_improvement", 0.25, "_factor_altman_improvement"),
            ("de_improvement", 0.20, "_factor_de_improvement"),
            ("margin_recovery_burst", 0.20, "_factor_margin_recovery_burst"),
            ("fcf_stabilization", 0.20, "_factor_fcf_stabilization"),
            ("restructuring_signal", 0.15, "_factor_restructuring_signal"),
        ],
    },
    "narrative_divergence": {
        "factors": [
            ("sentiment_divergence", 0.30, "_factor_sentiment_divergence"),
            ("divergence_signal", 0.25, "_factor_divergence_signal"),
            ("price_sentiment_disconnect", 0.25, "_factor_price_sentiment_disconnect"),
            ("narrative_states", 0.20, "_factor_narrative_states"),
        ],
    },
}


def _gross_margin_stability(annual_rows: list[dict]) -> float | None:
    margins: list[float] = []
    for row in annual_rows[:5]:
        gm = _gross_margin(row)
        if gm is not None:
            margins.append(gm)
    if len(margins) < 2:
        return None
    return max(margins) - min(margins)


def _revenue_trajectory(annual_rows: list[dict]) -> float | None:
    if len(annual_rows) < 2:
        return None
    current = annual_rows[0].get("revenue")
    prior = annual_rows[1].get("revenue")
    if current in (None, 0) or prior in (None, 0):
        return None
    return (float(current) / float(prior)) - 1.0


def assemble_pillar_candidate(
    repo: Repository,
    ticker: str,
    *,
    gate_inputs: dict[str, Any] | None = None,
    prices_service: PricesService | None = None,
) -> dict[str, Any] | None:
    """Merge research candidate snapshot with gate-engine context for pillar scoring."""
    symbol = ticker.strip().upper()
    if prices_service is None:
        prices_service = PricesService(repo)

    candidates = build_research_candidates(repo, prices_service, [symbol])
    candidate = candidates.get(symbol)
    if not candidate:
        return None

    if gate_inputs is None:
        gate_inputs = assemble_gate_inputs(repo, symbol, prices_service=prices_service) or {}

    company = repo.get_company_by_ticker(symbol)
    company_id = (company or {}).get("id")
    clusters = repo.fetch_insider_clusters_for_company(company_id, limit=1) if company_id else []
    cluster = clusters[0] if clusters else {}
    activist = repo.fetch_company_activist_filings(symbol, limit=1)
    ownership_row = repo.fetch_latest_insider_ownership(symbol)
    ownership_pct = (ownership_row or {}).get("ownership_pct")
    events = repo.fetch_company_edgar_events(symbol, limit=20)
    restructuring = [
        item for item in events
        if item.get("item_number") in {"2.05", "8.01"} or item.get("event_type") == "restructuring"
    ]
    debt_schedule = repo.fetch_company_debt_maturities(symbol)
    near_term_debt = None
    if debt_schedule:
        near_term_debt = any((item.get("maturity_year") or 9999) <= 2 for item in debt_schedule)

    narrative_snap = repo.fetch_latest_narrative_snapshots([symbol]).get(symbol) or {}
    annual_rows = gate_inputs.get("annual_rows") or []

    candidate.update({
        "latent": gate_inputs.get("latent") or {},
        "derived": {
            **(candidate.get("derived") or {}),
            "owner_earnings_yield": (gate_inputs.get("derived") or {}).get("owner_earnings_yield"),
            "gross_margin_stability": _gross_margin_stability(annual_rows),
            "revenue_trajectory": _revenue_trajectory(annual_rows),
            "operating_margin_trend": (gate_inputs.get("margin_trends") or {}).get("operating_margin_3yr_delta"),
            "altman_delta": _altman_delta(annual_rows, gate_inputs.get("scores") or {}),
            "de_delta": _de_delta(annual_rows),
        },
        "operational_recovery": gate_inputs.get("operational_recovery") or {},
        "narrative_states": gate_inputs.get("narrative_states") or [],
        "narrative": {
            **(candidate.get("narrative") or {}),
            "sentiment_90d": narrative_snap.get("sentiment_90d"),
        },
        "insider_cluster": cluster,
        "edgar": {
            "insider_ownership_pct": ownership_pct,
            "activist_filing": activist[0] if activist else None,
            "restructuring_events": restructuring,
            "debt_maturity_near_term": near_term_debt,
        },
    })
    return candidate


def _altman_delta(annual_rows: list[dict], scores: dict) -> float | None:
    current = scores.get("altmanZ")
    if current is None or len(annual_rows) < 2:
        return None
    prior_scores = annual_rows[1]
    prior_z = prior_scores.get("altman_z") or prior_scores.get("altmanZ")
    if prior_z is None:
        return None
    return float(current) - float(prior_z)


def _de_delta(annual_rows: list[dict]) -> float | None:
    if len(annual_rows) < 2:
        return None
    from .metric_primitives import debt_equity

    current = debt_equity(annual_rows[0])
    prior = debt_equity(annual_rows[1])
    if current is None or prior is None:
        return None
    return float(current) - float(prior)


def _pillar_tier(score: float | None, *, null_ratio: float) -> str:
    if null_ratio > 0.5:
        return "unknown"
    if score is None:
        return "unknown"
    if score >= 0.7:
        return "strong"
    if score >= 0.45:
        return "moderate"
    return "weak"


def _score_pillar(
    pillar_key: str,
    candidate: dict,
    sector_stats: dict[str, Any],
) -> dict[str, Any]:
    preset = _PILLAR_DEFINITIONS[pillar_key]
    factors_out: list[dict[str, Any]] = []
    normalized_values: list[float] = []

    for key, weight, impl_name in preset["factors"]:
        impl = _FACTOR_IMPL.get(impl_name)
        if not impl:
            continue
        result = impl(candidate, sector_stats)
        if not result or result.get("normalized") is None:
            continue
        normalized = float(result["normalized"])
        contribution = normalized * float(weight)
        normalized_values.append(normalized)
        factors_out.append({
            "key": key,
            "weight": float(weight),
            "normalized": round(normalized, 4),
            "contribution": round(contribution, 4),
            "raw": result.get("raw"),
        })

    total_factors = len(preset["factors"])
    null_ratio = 1.0 - (len(factors_out) / total_factors) if total_factors else 1.0
    pillar_score = (sum(normalized_values) / len(normalized_values)) if normalized_values else None
    tier = _pillar_tier(pillar_score, null_ratio=null_ratio)
    evidence_coverage = round(len(factors_out) / total_factors, 4) if total_factors else 0.0

    return {
        "pillar": pillar_key,
        "label": _PILLAR_LABELS[pillar_key],
        "score": round(pillar_score, 4) if pillar_score is not None else None,
        "tier": tier,
        "factors": factors_out,
        "factorsPresent": len(factors_out),
        "factorsTotal": total_factors,
        "evidenceCoverage": evidence_coverage,
        "dataClasses": _PILLAR_DATA_CLASSES.get(pillar_key, []),
    }


def evaluate_pillars(
    candidate: dict,
    *,
    sector_stats: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate all eight pillar profiles independently — no cross-pillar roll-up."""
    stats = sector_stats or candidate.get("_sector_stats") or {"bySector": {}}
    return [_score_pillar(key, candidate, stats) for key in PILLAR_KEYS]


def evaluate_pillars_for_ticker(
    repo: Repository,
    ticker: str,
    *,
    prices_service: PricesService | None = None,
    gate_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Evaluate pillar dashboard for one ticker; skipped when any gate fails."""
    symbol = ticker.strip().upper()
    if prices_service is None:
        prices_service = PricesService(repo)

    if gate_payload is None:
        gate_inputs = assemble_gate_inputs(repo, symbol, prices_service=prices_service)
        if gate_inputs is None:
            return None
        gates = evaluate_gate_stack(gate_inputs)
        summary = summarize_gate_stack(gates)
        gate_payload = {"ticker": symbol, "gates": gates, "summary": summary}
    else:
        gate_inputs = assemble_gate_inputs(repo, symbol, prices_service=prices_service)
        if gate_inputs is None:
            return None

    summary = gate_payload.get("summary") or {}
    if summary.get("skipPillars"):
        return {
            "ticker": symbol,
            "skipped": True,
            "skipReason": "gate_failure",
            "failedGates": summary.get("failedGates") or [],
            "gates": gate_payload.get("gates") or [],
            "pillars": [],
        }

    candidate = assemble_pillar_candidate(repo, symbol, gate_inputs=gate_inputs, prices_service=prices_service)
    if not candidate:
        return None

    sector_stats = build_sector_stats(
        repo,
        sectors=[candidate.get("sector")] if candidate.get("sector") else None,
        metric_api_keys=sorted(_SECTOR_PERCENTILE_METRICS),
    )
    candidate["_sector_stats"] = sector_stats
    pillars = evaluate_pillars(candidate, sector_stats=sector_stats)

    return {
        "ticker": symbol,
        "skipped": False,
        "gates": gate_payload.get("gates") or [],
        "pillars": pillars,
    }


def pillars_to_api(payload: dict[str, Any]) -> dict[str, Any]:
    """API shape for pillar dashboard consumers."""
    pillars = []
    for pillar in payload.get("pillars") or []:
        pillars.append({
            "pillar": pillar.get("pillar"),
            "label": pillar.get("label"),
            "score": pillar.get("score"),
            "tier": pillar.get("tier"),
            "factors": pillar.get("factors") or [],
            "factorsPresent": pillar.get("factorsPresent"),
            "factorsTotal": pillar.get("factorsTotal"),
            "evidenceCoverage": pillar.get("evidenceCoverage"),
            "dataClasses": pillar.get("dataClasses") or [],
        })
    return {
        "ticker": payload.get("ticker"),
        "skipped": bool(payload.get("skipped")),
        "skipReason": payload.get("skipReason"),
        "failedGates": payload.get("failedGates") or [],
        "gates": payload.get("gates") or [],
        "pillars": pillars,
    }
