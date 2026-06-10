"""Explainable composite opportunity scores — sector-percentile factor framework."""

from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any, Callable

from ..repositories import Repository
from .prices import PricesService
from .screening import build_research_candidates
from .sector_stats import build_sector_stats
from .ticker_universes import get_universe_tickers

logger = logging.getLogger("stock_tracker.composite_ranking")

MAX_LIMIT = 200
DEFAULT_LIMIT = 50
MAX_UNIVERSE_TICKERS = 600

_FACTOR_FN = Callable[[dict, dict[str, Any]], dict[str, Any] | None]

_COMPOSITE_PRESETS: dict[str, dict[str, Any]] = {
    "deep_value": {
        "label": "Deep Value Opportunity",
        "factors": [
            ("valuation_dislocation", 0.25, "_factor_valuation_dislocation"),
            ("survivability", 0.20, "_factor_survivability"),
            ("insider_conviction", 0.15, "_factor_insider_conviction"),
            ("sentiment_divergence", 0.15, "_factor_sentiment_divergence"),
            ("margin_stabilization", 0.15, "_factor_margin_stabilization"),
            ("fcf_quality", 0.10, "_factor_fcf_quality"),
        ],
    },
    "turnaround": {
        "label": "Turnaround Strength",
        "factors": [
            ("gross_margin_recovery", 0.25, "_factor_margin_stabilization"),
            ("altman_improvement", 0.20, "_factor_altman"),
            ("insider_buying", 0.20, "_factor_insider_conviction"),
            ("fcf_stabilization", 0.20, "_factor_fcf_quality"),
            ("survivability", 0.15, "_factor_survivability"),
        ],
    },
    "rerating_candidate": {
        "label": "Rerating Candidate",
        "factors": [
            ("sentiment_divergence", 0.30, "_factor_sentiment_divergence"),
            ("insider_conviction", 0.25, "_factor_insider_conviction"),
            ("gross_margin_recovery", 0.20, "_factor_margin_stabilization"),
            ("survivability", 0.15, "_factor_survivability"),
            ("altman_improvement", 0.10, "_factor_altman"),
        ],
    },
}

_SECTOR_PERCENTILE_METRICS = frozenset({
    "pe",
    "pb",
    "grossMargin",
    "fcfMargin",
    "de",
    "roe",
    "earningsYield",
})


def _finite(value: float | None) -> bool:
    return value is not None and value == value and abs(value) != float("inf")


def known_composites() -> list[str]:
    return sorted(_COMPOSITE_PRESETS)


def approximate_sector_percentile(
    value: float | None,
    breakpoints: dict[str, Any] | None,
    *,
    invert: bool = False,
) -> float | None:
    """Map a raw value to ~0–1 rank using sector breakpoint anchors."""
    if not breakpoints or breakpoints.get("count", 0) < 2 or not _finite(value):
        return None
    anchors: list[tuple[float, float]] = []
    for pct_key, pct in (
        ("min", 0.0),
        ("p20", 0.2),
        ("p40", 0.4),
        ("p60", 0.6),
        ("p80", 0.8),
        ("p95", 0.95),
        ("max", 1.0),
    ):
        raw = breakpoints.get(pct_key)
        if raw is not None and _finite(float(raw)):
            anchors.append((float(pct), float(raw)))
    if len(anchors) < 2:
        return None

    val = float(value)
    if val <= anchors[0][1]:
        rank = anchors[0][0]
    elif val >= anchors[-1][1]:
        rank = anchors[-1][0]
    else:
        rank = anchors[-1][0]
        for idx in range(len(anchors) - 1):
            pct_lo, val_lo = anchors[idx]
            pct_hi, val_hi = anchors[idx + 1]
            if val_lo <= val <= val_hi:
                if val_hi == val_lo:
                    rank = (pct_lo + pct_hi) / 2
                else:
                    frac = (val - val_lo) / (val_hi - val_lo)
                    rank = pct_lo + frac * (pct_hi - pct_lo)
                break

    rank = max(0.0, min(1.0, rank))
    return 1.0 - rank if invert else rank


def _sector_breakpoints(sector_stats: dict[str, Any], sector: str | None, metric_key: str) -> dict | None:
    return (sector_stats.get("bySector") or {}).get(sector or "", {}).get(metric_key)


def _avg_percentiles(
    candidate: dict,
    sector_stats: dict[str, Any],
    specs: list[tuple[str, bool]],
) -> tuple[float | None, dict[str, float | None]]:
    sector = candidate.get("sector")
    metrics = candidate.get("metrics") or {}
    raw: dict[str, float | None] = {}
    ranks: list[float] = []
    for metric_key, invert in specs:
        val = metrics.get(metric_key)
        raw[metric_key] = float(val) if _finite(val) else None
        pct = approximate_sector_percentile(
            val,
            _sector_breakpoints(sector_stats, sector, metric_key),
            invert=invert,
        )
        if pct is not None:
            ranks.append(pct)
    if not ranks:
        return None, raw
    return sum(ranks) / len(ranks), raw


def _factor_valuation_dislocation(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    normalized, raw = _avg_percentiles(
        candidate,
        sector_stats,
        [("pe", True), ("pb", True), ("earningsYield", False)],
    )
    if normalized is None:
        return None
    return {"normalized": normalized, "raw": raw}


def _factor_survivability(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    score = (candidate.get("scores") or {}).get("survivability")
    if not _finite(score):
        return None
    normalized = max(0.0, min(1.0, float(score) / 100.0))
    return {"normalized": normalized, "raw": {"survivability": float(score)}}


def _factor_insider_conviction(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    buy6m = (candidate.get("insider") or {}).get("buy6m")
    if buy6m is None or not _finite(float(buy6m)) or float(buy6m) <= 0:
        return None
    # Log-scaled 0–1 proxy until sector insider distributions exist.
    normalized = max(0.0, min(1.0, math.log1p(float(buy6m)) / math.log1p(5_000_000.0)))
    return {"normalized": normalized, "raw": {"buy6m": float(buy6m)}}


def _factor_margin_stabilization(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    trend = (candidate.get("derived") or {}).get("gross_margin_trend")
    if trend is None or not _finite(float(trend)):
        return None
    # Positive trend → higher score; map roughly [-0.15, +0.15] to [0, 1].
    normalized = max(0.0, min(1.0, (float(trend) + 0.15) / 0.30))
    return {"normalized": normalized, "raw": {"gross_margin_trend": float(trend)}}


def _factor_fcf_quality(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    derived = candidate.get("derived") or {}
    metrics = candidate.get("metrics") or {}
    fcf_yield = derived.get("fcf_yield")
    fcf_margin = metrics.get("fcfMargin")
    ranks: list[float] = []
    raw: dict[str, float | None] = {
        "fcf_yield": float(fcf_yield) if _finite(fcf_yield) else None,
        "fcf_margin": float(fcf_margin) if _finite(fcf_margin) else None,
    }
    sector = candidate.get("sector")
    if _finite(fcf_yield):
        pct = approximate_sector_percentile(
            fcf_yield,
            _sector_breakpoints(sector_stats, sector, "fcfMargin"),
            invert=False,
        )
        if pct is not None:
            ranks.append(pct)
    if _finite(fcf_margin):
        pct = approximate_sector_percentile(
            fcf_margin,
            _sector_breakpoints(sector_stats, sector, "fcfMargin"),
            invert=False,
        )
        if pct is not None:
            ranks.append(pct)
    if not ranks:
        if _finite(fcf_yield) and float(fcf_yield) > 0:
            return {"normalized": max(0.0, min(1.0, float(fcf_yield) * 5)), "raw": raw}
        return None
    return {"normalized": sum(ranks) / len(ranks), "raw": raw}


def _factor_sentiment_divergence(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    narrative = candidate.get("narrative") or {}
    score = narrative.get("divergence_score")
    signal = narrative.get("divergence_signal")
    if score is None and not signal:
        return None
    normalized = max(0.0, min(1.0, float(score))) if score is not None else 0.5
    if signal in ("rerating_candidate", "high_conviction"):
        normalized = max(normalized, 0.75)
    elif signal == "risk_flag":
        normalized = min(normalized, 0.25)
    return {
        "normalized": normalized,
        "raw": {"divergence_score": score, "divergence_signal": signal},
    }


def _factor_altman(candidate: dict, sector_stats: dict[str, Any]) -> dict[str, Any] | None:
    altman = (candidate.get("scores") or {}).get("altmanZ")
    if not _finite(altman):
        return None
    # Map Altman Z roughly: <1.8 distress → 0, >3.0 strong → 1
    normalized = max(0.0, min(1.0, (float(altman) - 1.8) / 1.2))
    return {"normalized": normalized, "raw": {"altmanZ": float(altman)}}


_FACTOR_IMPL: dict[str, _FACTOR_FN] = {
    "_factor_valuation_dislocation": _factor_valuation_dislocation,
    "_factor_survivability": _factor_survivability,
    "_factor_insider_conviction": _factor_insider_conviction,
    "_factor_margin_stabilization": _factor_margin_stabilization,
    "_factor_fcf_quality": _factor_fcf_quality,
    "_factor_sentiment_divergence": _factor_sentiment_divergence,
    "_factor_altman": _factor_altman,
}


def _score_candidate(
    candidate: dict,
    preset: dict[str, Any],
    sector_stats: dict[str, Any],
) -> dict[str, Any] | None:
    factors_out: list[dict[str, Any]] = []
    weighted_sum = 0.0
    weight_total = 0.0

    for key, weight, impl_name in preset["factors"]:
        impl = _FACTOR_IMPL.get(impl_name)
        if not impl:
            continue
        result = impl(candidate, sector_stats)
        if not result or result.get("normalized") is None:
            continue
        normalized = float(result["normalized"])
        contribution = normalized * float(weight)
        weighted_sum += contribution
        weight_total += float(weight)
        factors_out.append({
            "key": key,
            "weight": float(weight),
            "normalized": round(normalized, 4),
            "contribution": round(contribution, 4),
            "raw": result.get("raw"),
        })

    if not factors_out or weight_total <= 0:
        return None

    composite_score = weighted_sum / weight_total
    return {
        "compositeScore": round(composite_score, 4),
        "factors": factors_out,
        "factorsPresent": len(factors_out),
        "factorsTotal": len(preset["factors"]),
    }


def run_composite_rank(
    repo: Repository,
    prices_service: PricesService,
    *,
    composite: str,
    universe: str | None = None,
    tickers: list[str] | None = None,
    limit: int | None = DEFAULT_LIMIT,
) -> tuple[dict | None, int, str | None]:
    composite_key = (composite or "").strip().lower()
    preset = _COMPOSITE_PRESETS.get(composite_key)
    if not preset:
        known = ", ".join(known_composites())
        return None, 400, f"Unknown composite: {composite}. Known: {known}"

    if limit is not None:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return None, 400, "limit must be an integer"
        if limit < 1 or limit > MAX_LIMIT:
            return None, 400, f"limit must be between 1 and {MAX_LIMIT}"

    symbol_list = tickers
    if not symbol_list:
        universe_key = (universe or "sp500").strip().lower()
        symbol_list = get_universe_tickers(universe_key)
        if not symbol_list:
            return None, 400, f"Unknown or empty universe: {universe}"
    else:
        universe_key = None

    symbol_list = [str(t).strip().upper() for t in symbol_list if str(t).strip()][:MAX_UNIVERSE_TICKERS]
    candidates = build_research_candidates(repo, prices_service, symbol_list)
    narrative_snapshots = repo.fetch_latest_narrative_snapshots(list(candidates.keys()))
    for ticker, candidate in candidates.items():
        snap = narrative_snapshots.get(ticker)
        if snap:
            candidate["narrative"] = {
                "divergence_score": snap.get("divergence_score"),
                "divergence_signal": snap.get("divergence_signal"),
            }
    if not candidates:
        return {
            "meta": {
                "composite": composite_key,
                "label": preset["label"],
                "universe": universe_key,
                "universeSize": len(symbol_list),
                "evaluated": 0,
                "returned": 0,
                "limit": limit if limit is not None else len(symbol_list),
            },
            "results": [],
        }, 200, None

    sector_stats = build_sector_stats(
        repo,
        sectors=sorted({c.get("sector") for c in candidates.values() if c.get("sector")}),
        metric_api_keys=sorted(_SECTOR_PERCENTILE_METRICS),
    )

    ranked: list[dict[str, Any]] = []
    for ticker, candidate in candidates.items():
        scored = _score_candidate(candidate, preset, sector_stats)
        if not scored:
            continue
        ranked.append({
            "ticker": ticker,
            "companyName": candidate.get("companyName"),
            "sector": candidate.get("sector"),
            "industry": candidate.get("industry"),
            "compositeScore": scored["compositeScore"],
            "factors": scored["factors"],
            "factorsPresent": scored["factorsPresent"],
            "factorsTotal": scored["factorsTotal"],
        })

    ranked.sort(key=lambda row: (-row["compositeScore"], row["ticker"]))
    slice_end = len(ranked) if limit is None else limit
    for idx, row in enumerate(ranked[:slice_end], start=1):
        row["rank"] = idx
    results = ranked[:slice_end]

    return {
        "meta": {
            "composite": composite_key,
            "label": preset["label"],
            "universe": universe_key,
            "universeSize": len(symbol_list),
            "evaluated": len(candidates),
            "scored": len(ranked),
            "returned": len(results),
            "limit": limit if limit is not None else len(results),
        },
        "results": results,
    }, 200, None


def snapshot_composite_ranks(
    repo: Repository,
    prices_service: PricesService,
    *,
    composites: list[str] | None = None,
    universe: str = "sp500",
    snapshot_date: str | None = None,
) -> dict[str, Any]:
    """Persist today's composite ranks for nightly history tracking."""
    snapshot_day = snapshot_date or date.today().isoformat()
    composite_keys = composites or known_composites()
    written = 0
    per_composite: dict[str, dict[str, int]] = {}

    for composite_key in composite_keys:
        payload, status, error = run_composite_rank(
            repo,
            prices_service,
            composite=composite_key,
            universe=universe,
            limit=None,
        )
        if error or not payload:
            per_composite[composite_key] = {"scored": 0, "written": 0, "error": error or "empty"}
            continue

        records = [
            {
                "ticker": row["ticker"],
                "composite": composite_key,
                "snapshot_date": snapshot_day,
                "composite_score": row["compositeScore"],
                "rank_in_universe": row.get("rank"),
                "factors": row.get("factors"),
            }
            for row in payload.get("results") or []
        ]
        count = repo.upsert_company_rank_snapshots(records)
        written += count
        per_composite[composite_key] = {
            "scored": payload["meta"].get("scored", 0),
            "written": count,
        }
        logger.info(
            "snapshot_composite_ranks composite=%s universe=%s written=%d scored=%d",
            composite_key,
            universe,
            count,
            payload["meta"].get("scored", 0),
        )

    return {
        "snapshotDate": snapshot_day,
        "universe": universe,
        "written": written,
        "composites": per_composite,
    }


def get_rank_history(
    repo: Repository,
    *,
    ticker: str,
    composite: str,
    limit: int = 90,
) -> tuple[dict | None, int, str | None]:
    composite_key = (composite or "").strip().lower()
    if composite_key not in _COMPOSITE_PRESETS:
        known = ", ".join(known_composites())
        return None, 400, f"Unknown composite: {composite}. Known: {known}"

    symbol = (ticker or "").strip().upper()
    if not symbol:
        return None, 400, "ticker is required"

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return None, 400, "limit must be an integer"
    if limit < 1 or limit > 365:
        return None, 400, "limit must be between 1 and 365"

    company = repo.get_company_by_ticker(symbol)
    if not company:
        return None, 404, "not_found"

    history = repo.fetch_company_rank_history(symbol, composite=composite_key, limit=limit)
    preset = _COMPOSITE_PRESETS[composite_key]
    return {
        "meta": {
            "ticker": symbol,
            "composite": composite_key,
            "label": preset["label"],
            "returned": len(history),
            "limit": limit,
        },
        "history": history,
    }, 200, None
