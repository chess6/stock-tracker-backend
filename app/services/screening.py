"""Composable universe screening — filter specs over fundamentals, scores, insiders."""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..repositories import Repository
from .fundamentals import (
    build_company_metrics,
    collapse_narrow_fundamentals_rows,
    fetch_resolved_wide_rows,
    pivot_fundamentals_rows,
    resolve_financial_dimension,
)
from .metric_primitives import free_cash_flow, safe_div, total_debt
from .metric_registry import METRIC_REGISTRY, api_key_for, canonical_key
from .metrics_engine import compute_period_metrics
from .prices import PricesService
from .scoring import _gross_margin, margin_trend_delta, share_dilution_rate
from .sector_stats import sector_stats_for_tickers
from .ticker_universes import get_universe_tickers

logger = logging.getLogger("stock_tracker.screening")

VALID_OPS = frozenset({"lt", "lte", "gt", "gte", "eq", "in", "percentile_lt", "percentile_gt"})
MAX_FILTERS = 20
MAX_LIMIT = 200
DEFAULT_LIMIT = 100
MAX_UNIVERSE_TICKERS = 600

_PERCENTILE_KEYS = {
    20: "p20",
    40: "p40",
    60: "p60",
    80: "p80",
    95: "p95",
}

_SCORE_FIELD_ALIASES = {
    "piotroski": "piotroskiF",
    "piotroski_f": "piotroskiF",
    "altman_z": "altmanZ",
    "beneish_m": "beneishM",
    "survivability_score": "survivability",
    "composite_score": "survivability",
}

_DERIVED_FIELDS = frozenset({"gross_margin_trend", "fcf_yield", "ev_fcf", "dilution_rate", "market_cap"})
_INSIDER_FIELDS = frozenset({"buy6m", "buy3m", "cluster_count"})


def _finite(value: float | None) -> bool:
    return value is not None and value == value and abs(value) != float("inf")


def _validate_spec(spec: dict) -> tuple[dict | None, str | None]:
    if not isinstance(spec, dict):
        return None, "Request body must be a JSON object"

    filters = spec.get("filters") or []
    if not isinstance(filters, list):
        return None, "filters must be an array"
    if len(filters) > MAX_FILTERS:
        return None, f"Maximum {MAX_FILTERS} filters per request"

    normalized_filters: list[dict] = []
    for idx, raw in enumerate(filters):
        if not isinstance(raw, dict):
            return None, f"filters[{idx}] must be an object"
        metric = str(raw.get("metric") or raw.get("field") or "").strip()
        op = str(raw.get("op") or "").strip().lower()
        if not metric:
            return None, f"filters[{idx}].metric is required"
        if op not in VALID_OPS:
            return None, f"filters[{idx}].op must be one of: {', '.join(sorted(VALID_OPS))}"
        if "value" not in raw:
            return None, f"filters[{idx}].value is required"
        if op == "in":
            if not isinstance(raw["value"], list) or not raw["value"]:
                return None, f"filters[{idx}].value must be a non-empty array for op=in"
        normalized_filters.append({"metric": metric, "op": op, "value": raw["value"]})

    tickers = spec.get("tickers")
    universe = (spec.get("universe") or "").strip().lower() or None
    if tickers is not None:
        if not isinstance(tickers, list) or not tickers:
            return None, "tickers must be a non-empty array when provided"
        tickers = [str(t).strip().upper() for t in tickers if str(t).strip()]
        if not tickers:
            return None, "tickers must contain at least one symbol"
    elif not universe:
        return None, "universe or tickers is required"

    sort = spec.get("sort") or {}
    if sort and not isinstance(sort, dict):
        return None, "sort must be an object"
    sort_metric = str(sort.get("metric") or "").strip() or None
    sort_dir = str(sort.get("dir") or "desc").strip().lower()
    if sort_dir not in {"asc", "desc"}:
        return None, "sort.dir must be asc or desc"

    limit = spec.get("limit", DEFAULT_LIMIT)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return None, "limit must be an integer"
    if limit < 1 or limit > MAX_LIMIT:
        return None, f"limit must be between 1 and {MAX_LIMIT}"

    return {
        "filters": normalized_filters,
        "tickers": tickers,
        "universe": universe,
        "sort": {"metric": sort_metric, "dir": sort_dir} if sort_metric else None,
        "limit": limit,
    }, None


def _resolve_registry_api_key(metric: str) -> str | None:
    key = metric.strip()
    if key in METRIC_REGISTRY:
        return METRIC_REGISTRY[key].get("api_key")
    if canonical_key(key):
        return key
    for canonical, meta in METRIC_REGISTRY.items():
        if meta.get("api_key") == key:
            return key
    return api_key_for(key)


def _field_kind(metric: str) -> tuple[str, str]:
    """Return (kind, resolved_key) where kind is score|metric|derived|insider."""
    key = metric.strip()
    if key in _SCORE_FIELD_ALIASES:
        return "score", _SCORE_FIELD_ALIASES[key]
    if key in _DERIVED_FIELDS:
        return "derived", key
    if key in _INSIDER_FIELDS:
        return "insider", key
    canonical = key if key in METRIC_REGISTRY else canonical_key(key)
    if canonical and METRIC_REGISTRY.get(canonical, {}).get("category") == "score":
        return "score", METRIC_REGISTRY[canonical]["api_key"]
    api_key = _resolve_registry_api_key(key)
    if api_key:
        return "metric", api_key
    if key in {"piotroskiF", "altmanZ", "beneishM", "survivability"}:
        return "score", key
    return "unknown", key


def _enterprise_value(row: dict, price: float | None) -> float | None:
    shares = row.get("sharesbas")
    if shares in (None, 0) or price is None:
        return None
    market_cap = shares * price
    debt = total_debt(row) or 0
    cash = row.get("cashneq") or 0
    ev = market_cap + debt - cash
    return ev if ev > 0 else None


def _derived_values(row: dict, price: float | None, annual_rows: list[dict], prior_annual: dict | None) -> dict[str, float | None]:
    canonical = compute_period_metrics(row, price=price)
    fcf = free_cash_flow(row)
    ev = _enterprise_value(row, price)
    return {
        "gross_margin_trend": margin_trend_delta(annual_rows, 3, _gross_margin),
        "fcf_yield": safe_div(fcf, ev),
        "ev_fcf": safe_div(ev, fcf) if fcf not in (None, 0) else None,
        "dilution_rate": share_dilution_rate(row, prior_annual),
        "market_cap": canonical.get("market_cap"),
    }


def _read_field(candidate: dict, metric: str) -> float | None:
    kind, resolved = _field_kind(metric)
    if kind == "metric":
        return candidate.get("metrics", {}).get(resolved)
    if kind == "score":
        scores = candidate.get("scores") or {}
        return scores.get(resolved)
    if kind == "derived":
        return candidate.get("derived", {}).get(resolved)
    if kind == "insider":
        return candidate.get("insider", {}).get(resolved)
    return None


def _compare(op: str, actual: float | None, expected: Any) -> bool:
    if actual is None or not _finite(float(actual)):
        return False
    actual_f = float(actual)
    if op == "lt":
        return actual_f < float(expected)
    if op == "lte":
        return actual_f <= float(expected)
    if op == "gt":
        return actual_f > float(expected)
    if op == "gte":
        return actual_f >= float(expected)
    if op == "eq":
        return actual_f == float(expected)
    if op == "in":
        try:
            allowed = {float(v) for v in expected}
        except (TypeError, ValueError):
            return False
        return actual_f in allowed
    return False


def _sector_threshold(breakpoints: dict, percentile: float) -> float | None:
    pct = float(percentile)
    if pct in _PERCENTILE_KEYS:
        return breakpoints.get(_PERCENTILE_KEYS[pct])
    if pct <= 0:
        return breakpoints.get("min")
    if pct >= 95:
        return breakpoints.get("p95") or breakpoints.get("max")
    return None


def _evaluate_filter(
    candidate: dict,
    flt: dict,
    *,
    sector_stats: dict[str, Any],
) -> dict:
    metric = flt["metric"]
    op = flt["op"]
    expected = flt["value"]
    actual = _read_field(candidate, metric)
    passed = False
    threshold = None

    if op in {"percentile_lt", "percentile_gt"}:
        kind, resolved = _field_kind(metric)
        api_key = resolved if kind == "metric" else None
        if kind == "score":
            api_key = resolved
        elif kind == "derived" and metric == "gross_margin_trend":
            api_key = "grossMargin"
        sector = candidate.get("sector")
        breakpoints = (
            (sector_stats.get("bySector") or {}).get(sector or "", {}).get(api_key or "")
            if api_key
            else None
        )
        if breakpoints and actual is not None and _finite(float(actual)):
            threshold = _sector_threshold(breakpoints, float(expected))
            if threshold is not None:
                if op == "percentile_lt":
                    passed = float(actual) < float(threshold)
                else:
                    passed = float(actual) > float(threshold)
    else:
        passed = _compare(op, actual, expected)

    margin = None
    if actual is not None and _finite(float(actual)) and op in {"lt", "lte", "gt", "gte"}:
        try:
            margin = float(actual) - float(expected)
        except (TypeError, ValueError):
            margin = None

    return {
        "metric": metric,
        "op": op,
        "value": expected,
        "actual": actual,
        "passed": passed,
        "margin": margin,
        "sectorThreshold": threshold,
    }


def _build_candidates(
    repo: Repository,
    prices_service: PricesService,
    tickers: list[str],
) -> dict[str, dict]:
    if not tickers:
        return {}

    resolved = resolve_financial_dimension("MRY", most_recent=False)
    wide_rows = fetch_resolved_wide_rows(repo, tickers, gte=None, resolved=resolved)

    all_annual = pivot_fundamentals_rows(
        collapse_narrow_fundamentals_rows(
            repo.fetch_fundamentals_rows(tickers, dimension="ARY"),
            annual=True,
        ),
        canonical_annual=True,
    )
    annual_by_ticker: dict[str, list[dict]] = {}
    for row in all_annual:
        annual_by_ticker.setdefault(row["ticker"], []).append(row)
    for ticker_rows in annual_by_ticker.values():
        ticker_rows.sort(key=lambda r: r.get("calendardate") or "", reverse=True)

    prices_batch = repo.fetch_prices_batch(tickers, limit_per_ticker=1)
    scores_by_ticker = repo.fetch_latest_company_scores(tickers, dimension="ARY")
    insider_rows = {row["ticker"]: row for row in repo.fetch_insider_buying_sums(tickers)}
    cluster_counts = repo.fetch_insider_cluster_counts(tickers)

    candidates: dict[str, dict] = {}
    for row in wide_rows:
        ticker = row["ticker"]
        price_rows = prices_batch.get(ticker, [])
        price = price_rows[0]["close"] if price_rows else None
        metrics = build_company_metrics(row, price=price)
        annual_rows = annual_by_ticker.get(ticker, [])
        prior = annual_rows[1] if len(annual_rows) > 1 else None
        company = repo.get_company_by_ticker(ticker)
        insider = insider_rows.get(ticker, {})

        candidates[ticker] = {
            "ticker": ticker,
            "companyName": row.get("company_name") or (company or {}).get("name"),
            "sector": (company or {}).get("sector"),
            "industry": (company or {}).get("industry"),
            "periodEnd": row.get("calendardate"),
            "metrics": metrics,
            "scores": scores_by_ticker.get(ticker),
            "derived": _derived_values(row, price, annual_rows, prior),
            "insider": {
                "buy6m": insider.get("buy6m"),
                "buy3m": insider.get("buy3m"),
                "cluster_count": cluster_counts.get(ticker, 0),
            },
            "price": price,
        }

    return candidates


def build_research_candidates(
    repo: Repository,
    prices_service: PricesService,
    tickers: list[str],
) -> dict[str, dict]:
    """Batch fundamentals/scores snapshot used by screening and composite ranking."""
    return _build_candidates(repo, prices_service, tickers)


def run_composable_screen(
    repo: Repository,
    prices_service: PricesService,
    spec: dict,
) -> tuple[dict | None, int, str | None]:
    """
    Execute a composable screen. Returns (payload, http_status, error_message).
    """
    normalized, err = _validate_spec(spec)
    if err:
        return None, 400, err

    tickers = normalized["tickers"]
    if not tickers:
        tickers = get_universe_tickers(normalized["universe"] or "")
        if not tickers:
            return None, 400, f"Unknown or empty universe: {normalized['universe']}"
    tickers = tickers[:MAX_UNIVERSE_TICKERS]

    candidates = _build_candidates(repo, prices_service, tickers)
    if not candidates:
        return {
            "meta": {
                "universe": normalized["universe"],
                "universeSize": len(tickers),
                "evaluated": 0,
                "matched": 0,
                "limit": normalized["limit"],
            },
            "spec": normalized,
            "results": [],
        }, 200, None

    metric_api_keys = []
    for flt in normalized["filters"]:
        kind, resolved = _field_kind(flt["metric"])
        if kind == "metric":
            metric_api_keys.append(resolved)
    sector_stats = (
        sector_stats_for_tickers(repo, list(candidates.keys()), metric_api_keys=metric_api_keys or None)
        if any(f["op"] in {"percentile_lt", "percentile_gt"} for f in normalized["filters"])
        else {"bySector": {}}
    )

    matched: list[dict] = []
    for ticker, candidate in candidates.items():
        evidence = [_evaluate_filter(candidate, flt, sector_stats=sector_stats) for flt in normalized["filters"]]
        if normalized["filters"] and not all(item["passed"] for item in evidence):
            continue
        matched.append(
            {
                "ticker": ticker,
                "companyName": candidate.get("companyName"),
                "sector": candidate.get("sector"),
                "industry": candidate.get("industry"),
                "periodEnd": candidate.get("periodEnd"),
                "price": candidate.get("price"),
                "metrics": candidate.get("metrics"),
                "scores": candidate.get("scores"),
                "derived": candidate.get("derived"),
                "insider": candidate.get("insider"),
                "filterEvidence": evidence,
                "filtersPassed": sum(1 for item in evidence if item["passed"]),
                "filtersTotal": len(evidence),
            }
        )

    sort_spec = normalized.get("sort")
    if sort_spec and sort_spec.get("metric"):
        reverse = sort_spec["dir"] != "asc"

        def sort_key(row: dict) -> float:
            val = _read_field(
                {
                    "metrics": row.get("metrics"),
                    "scores": row.get("scores"),
                    "derived": row.get("derived"),
                    "insider": row.get("insider"),
                },
                sort_spec["metric"],
            )
            if val is None or not _finite(float(val)):
                return float("-inf") if reverse else float("inf")
            return float(val)

        matched.sort(key=sort_key, reverse=reverse)

    limit = normalized["limit"]
    results = matched[:limit]

    logger.info(
        "composable_screen universe=%s evaluated=%d matched=%d returned=%d",
        normalized.get("universe"),
        len(candidates),
        len(matched),
        len(results),
    )

    return {
        "meta": {
            "universe": normalized.get("universe"),
            "universeSize": len(tickers),
            "evaluated": len(candidates),
            "matched": len(matched),
            "returned": len(results),
            "limit": limit,
        },
        "spec": normalized,
        "results": results,
    }, 200, None
