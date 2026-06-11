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
VALID_GROUP_OPS = frozenset({"AND", "OR"})
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
_NARRATIVE_FIELDS = frozenset({"divergence_score", "divergence_signal"})
_NARRATIVE_STRING_FIELDS = frozenset({"divergence_signal"})


def _finite(value: float | None) -> bool:
    return value is not None and value == value and abs(value) != float("inf")


def _normalize_filter(raw: dict, path: str) -> tuple[dict | None, str | None]:
    if not isinstance(raw, dict):
        return None, f"{path} must be an object"
    metric = str(raw.get("metric") or raw.get("field") or "").strip()
    op = str(raw.get("op") or "").strip().lower()
    if not metric:
        return None, f"{path}.metric is required"
    if op not in VALID_OPS:
        return None, f"{path}.op must be one of: {', '.join(sorted(VALID_OPS))}"
    if "value" not in raw:
        return None, f"{path}.value is required"
    if op == "in":
        if not isinstance(raw["value"], list) or not raw["value"]:
            return None, f"{path}.value must be a non-empty array for op=in"
    return {"metric": metric, "op": op, "value": raw["value"]}, None


def _validate_spec(spec: dict) -> tuple[dict | None, str | None]:
    if not isinstance(spec, dict):
        return None, "Request body must be a JSON object"

    filters = spec.get("filters") or []
    if not isinstance(filters, list):
        return None, "filters must be an array"

    filter_groups_raw = spec.get("filter_groups")
    if filter_groups_raw is not None and not isinstance(filter_groups_raw, list):
        return None, "filter_groups must be an array"

    normalized_groups: list[dict] = []
    total_filters = 0

    if filter_groups_raw:
        for g_idx, group in enumerate(filter_groups_raw):
            if not isinstance(group, dict):
                return None, f"filter_groups[{g_idx}] must be an object"
            group_op = str(group.get("op") or "AND").strip().upper()
            if group_op not in VALID_GROUP_OPS:
                return None, f"filter_groups[{g_idx}].op must be AND or OR"
            group_filters_raw = group.get("filters") or []
            if not isinstance(group_filters_raw, list):
                return None, f"filter_groups[{g_idx}].filters must be an array"
            group_filters: list[dict] = []
            for f_idx, raw in enumerate(group_filters_raw):
                normalized, err = _normalize_filter(raw, f"filter_groups[{g_idx}].filters[{f_idx}]")
                if err:
                    return None, err
                group_filters.append(normalized)
            normalized_groups.append({"op": group_op, "filters": group_filters})
            total_filters += len(group_filters)

    normalized_filters: list[dict] = []
    for idx, raw in enumerate(filters):
        normalized, err = _normalize_filter(raw, f"filters[{idx}]")
        if err:
            return None, err
        normalized_filters.append(normalized)
    total_filters += len(normalized_filters)

    if normalized_filters:
        normalized_groups.append({"op": "AND", "filters": normalized_filters})

    if total_filters > MAX_FILTERS:
        return None, f"Maximum {MAX_FILTERS} filters per request"

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

    flat_filters = [flt for group in normalized_groups for flt in group["filters"]]

    return {
        "filters": flat_filters,
        "filter_groups": normalized_groups,
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
    """Return (kind, resolved_key) where kind is score|metric|derived|insider|narrative."""
    key = metric.strip()
    if key in _SCORE_FIELD_ALIASES:
        return "score", _SCORE_FIELD_ALIASES[key]
    if key in _DERIVED_FIELDS:
        return "derived", key
    if key in _INSIDER_FIELDS:
        return "insider", key
    if key in _NARRATIVE_FIELDS:
        return "narrative", key
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
    if kind == "narrative":
        return candidate.get("narrative", {}).get(resolved)
    return None


def _compare_string(op: str, actual: str | None, expected: Any) -> bool:
    if actual is None or not str(actual).strip():
        return False
    actual_s = str(actual).strip()
    if op == "eq":
        return actual_s == str(expected).strip()
    if op == "in":
        if not isinstance(expected, list):
            return False
        allowed = {str(v).strip() for v in expected}
        return actual_s in allowed
    return False


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
    kind, resolved = _field_kind(metric)

    if kind == "narrative" and resolved in _NARRATIVE_STRING_FIELDS:
        passed = _compare_string(op, actual, expected)
    elif op in {"percentile_lt", "percentile_gt"}:
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
    if actual is not None and op in {"lt", "lte", "gt", "gte"}:
        try:
            actual_f = float(actual)
        except (TypeError, ValueError):
            actual_f = None
        if actual_f is not None and _finite(actual_f):
            try:
                margin = actual_f - float(expected)
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


def _group_passed(evidence: list[dict], group_op: str) -> bool:
    if not evidence:
        return True
    if group_op == "OR":
        return any(item["passed"] for item in evidence)
    return all(item["passed"] for item in evidence)


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

    narrative_snapshots = repo.fetch_latest_narrative_snapshots(list(candidates.keys()))
    for ticker, candidate in candidates.items():
        snap = narrative_snapshots.get(ticker)
        candidate["narrative"] = {
            "divergence_score": snap.get("divergence_score") if snap else None,
            "divergence_signal": snap.get("divergence_signal") if snap else None,
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

    filter_groups = normalized["filter_groups"]
    all_filters = normalized["filters"]

    metric_api_keys = []
    for flt in all_filters:
        kind, resolved = _field_kind(flt["metric"])
        if kind == "metric":
            metric_api_keys.append(resolved)
    sector_stats = (
        sector_stats_for_tickers(repo, list(candidates.keys()), metric_api_keys=metric_api_keys or None)
        if any(f["op"] in {"percentile_lt", "percentile_gt"} for f in all_filters)
        else {"bySector": {}}
    )

    matched: list[dict] = []
    for ticker, candidate in candidates.items():
        evidence: list[dict] = []
        groups_passed = True
        for group in filter_groups:
            group_evidence = [
                _evaluate_filter(candidate, flt, sector_stats=sector_stats) for flt in group["filters"]
            ]
            evidence.extend(group_evidence)
            if group["filters"] and not _group_passed(group_evidence, group["op"]):
                groups_passed = False
        if filter_groups and not groups_passed:
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
                "narrative": candidate.get("narrative"),
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
                    "narrative": row.get("narrative"),
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
