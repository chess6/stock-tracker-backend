from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..db import get_db
from ..repositories import Repository, utc_now_iso
from ..services.metric_registry import registry_for_api
from ..services.prices import PricesService
from ..services.base_rate_validation import validate_gate_base_rates
from ..services.gate_engine import evaluate_gates_for_ticker, gates_to_api
from ..services.pillar_engine import evaluate_pillars_for_ticker, pillars_to_api
from ..services.research import ResearchService
from ..services.composite_ranking import get_rank_history, get_thesis_drift_history, run_composite_rank
from ..services.rank_validation import validate_composite_rank
from ..services.research_queue import (
    dismiss_research_queue_item,
    get_catalyst_feed,
    get_research_queue,
)
from ..services.thesis_engine import evaluate_thesis_for_ticker, thesis_to_api
from ..services.screening import run_composable_screen
from ..services.sector_stats import sector_stats_for_tickers
from ..services.feature_flags import is_enabled
from ..services.insider_analysis import build_insider_conviction_alerts
from ..services.narrative import build_narrative_divergence_alerts

research_bp = Blueprint("research", __name__, url_prefix="/api/research")


def _attach_research_meta(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return payload
    meta = dict(payload.get("meta") or {})
    meta.setdefault("source", "sqlite")
    meta["computedAt"] = utc_now_iso()
    payload["meta"] = meta
    return payload


def get_research_service() -> ResearchService:
    repo = Repository(get_db())
    return ResearchService(repo, PricesService(repo))


@research_bp.route("/metrics/registry", methods=["GET"])
def research_metrics_registry():
    return jsonify(_attach_research_meta({"metrics": registry_for_api()}))


@research_bp.route("/metrics/sector-stats", methods=["GET"])
def research_sector_stats():
    tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    sectors = [item.strip() for item in request.args.get("sectors", "").split(",") if item.strip()]
    metrics = [item.strip() for item in request.args.get("metrics", "").split(",") if item.strip()]
    repo = Repository(get_db())
    if tickers:
        payload = sector_stats_for_tickers(repo, tickers, metric_api_keys=metrics or None)
    else:
        from ..services.sector_stats import build_sector_stats

        payload = build_sector_stats(repo, sectors=sectors or None, metric_api_keys=metrics or None)
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/screen", methods=["POST"])
def research_screen():
    spec = request.get_json(silent=True) or {}
    repo = Repository(get_db())
    payload, status, error = run_composable_screen(repo, PricesService(repo), spec)
    if error:
        return jsonify({"error": error}), status
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/rank", methods=["GET", "POST"])
def research_rank():
    repo = Repository(get_db())

    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        tickers_raw = body.get("tickers") or []
        if isinstance(tickers_raw, str):
            tickers = [item.strip().upper() for item in tickers_raw.split(",") if item.strip()]
        else:
            tickers = [str(item).strip().upper() for item in tickers_raw if str(item).strip()]
        universe = body.get("universe", "sp500")
        composite = body.get("composite", "deep_value")
        limit_raw = body.get("limit", 50)
        weight_overrides = body.get("weight_overrides") or None
        if weight_overrides is not None and not isinstance(weight_overrides, dict):
            return jsonify({"error": "weight_overrides must be an object"}), 400
    else:
        tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
        universe = request.args.get("universe", "sp500")
        composite = request.args.get("composite", "deep_value")
        limit_raw = request.args.get("limit", 50)
        weight_overrides = None

    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400

    payload, status, error = run_composite_rank(
        repo,
        PricesService(repo),
        composite=composite,
        universe=universe if not tickers else None,
        tickers=tickers or None,
        limit=limit,
        weight_overrides=weight_overrides,
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/rank/validation", methods=["GET"])
def research_rank_validation():
    repo = Repository(get_db())
    composite = request.args.get("composite", "deep_value")
    horizons_raw = request.args.get("horizons", "30,90,180")
    try:
        horizons = tuple(int(item.strip()) for item in horizons_raw.split(",") if item.strip())
    except ValueError:
        return jsonify({"error": "horizons must be comma-separated integers"}), 400

    payload, status, error = validate_composite_rank(
        repo,
        composite=composite,
        horizons=horizons or (30, 90, 180),
    )
    if error == "insufficient_history" or error == "insufficient_price_history":
        return jsonify({"error": error, "composite": composite}), 404
    if error:
        return jsonify({"error": error}), status
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/rank/history/<ticker>", methods=["GET"])
def research_rank_history(ticker: str):
    repo = Repository(get_db())
    composite = request.args.get("composite", "deep_value")
    limit_raw = request.args.get("limit", 90)
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400

    payload, status, error = get_rank_history(
        repo,
        ticker=ticker,
        composite=composite,
        limit=limit,
    )
    if error == "not_found":
        return jsonify({"error": "not_found", "ticker": ticker.strip().upper()}), 404
    if error:
        return jsonify({"error": error}), status
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/rank/thesis-history/<ticker>", methods=["GET"])
def research_thesis_history(ticker: str):
    repo = Repository(get_db())
    composite = request.args.get("composite", "deep_value")
    limit_raw = request.args.get("limit", 90)
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400

    payload, status, error = get_thesis_drift_history(
        repo,
        ticker=ticker,
        composite=composite,
        limit=limit,
    )
    if error == "not_found":
        return jsonify({"error": "not_found", "ticker": ticker.strip().upper()}), 404
    if error:
        return jsonify({"error": error}), status
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/screener", methods=["GET"])
def research_screener():
    tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    if not tickers:
        return jsonify({"error": "No tickers provided"}), 400
    if len(tickers) > 100:
        return jsonify({"error": "Maximum 100 tickers per request"}), 400
    dimension = request.args.get("dimension", "MRY")
    payload = get_research_service().get_screener(tickers, dimension=dimension)
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/ticker/<ticker>", methods=["GET"])
def research_ticker(ticker: str):
    dimension = request.args.get("dimension", "MRY")
    gte = request.args.get("gte") or None
    payload = get_research_service().get_ticker_detail(ticker, dimension=dimension, gte=gte)
    if payload.get("error") == "not_found":
        return jsonify(payload), 404
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/insiders/clusters", methods=["GET"])
def research_insider_clusters():
    tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    limit = min(int(request.args.get("limit", 50)), 200)
    min_buy_value_raw = request.args.get("min_buy_value")
    min_buy_value = float(min_buy_value_raw) if min_buy_value_raw not in (None, "") else None
    payload = get_research_service().get_insider_clusters(
        tickers or None,
        limit=limit,
        min_buy_value=min_buy_value,
    )
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/insiders/<ticker>", methods=["GET"])
def research_insiders(ticker: str):
    payload = get_research_service().get_insider_detail(ticker)
    if payload.get("error") == "not_found":
        return jsonify(payload), 404
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/narrative/<ticker>", methods=["GET"])
def research_narrative(ticker: str):
    payload = get_research_service().get_narrative(ticker)
    if payload.get("error") == "not_found":
        return jsonify(payload), 404
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/gates/<ticker>", methods=["GET"])
def research_gates(ticker: str):
    repo = Repository(get_db())
    payload = evaluate_gates_for_ticker(repo, ticker, prices_service=PricesService(repo))
    if payload is None:
        return jsonify({"error": "not_found", "ticker": ticker.strip().upper()}), 404
    return jsonify(_attach_research_meta(gates_to_api(payload)))


@research_bp.route("/pillars/<ticker>", methods=["GET"])
def research_pillars(ticker: str):
    repo = Repository(get_db())
    payload = evaluate_pillars_for_ticker(repo, ticker, prices_service=PricesService(repo))
    if payload is None:
        return jsonify({"error": "not_found", "ticker": ticker.strip().upper()}), 404
    return jsonify(_attach_research_meta(pillars_to_api(payload)))


@research_bp.route("/thesis/<ticker>", methods=["GET"])
def research_thesis(ticker: str):
    repo = Repository(get_db())
    payload = evaluate_thesis_for_ticker(repo, ticker, prices_service=PricesService(repo))
    if payload is None:
        return jsonify({"error": "not_found", "ticker": ticker.strip().upper()}), 404
    return jsonify(_attach_research_meta(thesis_to_api(payload)))


@research_bp.route("/queue", methods=["GET"])
def research_queue_list():
    repo = Repository(get_db())
    if not is_enabled("experimental_research_queue", repo):
        return jsonify({"error": "feature_disabled", "flag": "experimental_research_queue"}), 404

    limit_raw = request.args.get("limit", 50)
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400

    event_types = [
        item.strip().lower()
        for item in request.args.get("event_types", "").split(",")
        if item.strip()
    ] or None
    dismissed_raw = request.args.get("dismissed", "false").strip().lower()
    dismissed = dismissed_raw in ("1", "true", "yes", "on")

    payload = get_research_queue(
        repo,
        limit=limit,
        event_types=event_types,
        dismissed=dismissed,
    )
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/catalysts", methods=["GET"])
def research_catalysts():
    repo = Repository(get_db())
    if not is_enabled("experimental_research_queue", repo):
        return jsonify({"error": "feature_disabled", "flag": "experimental_research_queue"}), 404

    limit_raw = request.args.get("limit", 50)
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400

    payload = get_catalyst_feed(repo, limit=limit)
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/queue/<ticker>/dismiss", methods=["POST"])
def research_queue_dismiss(ticker: str):
    repo = Repository(get_db())
    if not is_enabled("experimental_research_queue", repo):
        return jsonify({"error": "feature_disabled", "flag": "experimental_research_queue"}), 404

    body = request.get_json(silent=True) or {}
    event_type = body.get("event_type") or body.get("eventType")
    event_date = body.get("event_date") or body.get("eventDate")

    payload, status, error = dismiss_research_queue_item(
        repo,
        ticker,
        event_type=event_type,
        event_date=event_date,
    )
    if error == "not_found":
        return jsonify(payload), 404
    if error:
        return jsonify(payload), status
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/insider-alerts", methods=["GET"])
def research_insider_alerts():
    repo = Repository(get_db())
    if not is_enabled("experimental_insider_alerts", repo):
        return jsonify({"error": "feature_disabled", "flag": "experimental_insider_alerts"}), 404

    limit_raw = request.args.get("limit", 50)
    min_intensity_raw = request.args.get("min_intensity", "0.3")
    window_days_raw = request.args.get("window_days", "30")
    try:
        limit = int(limit_raw)
        min_intensity = float(min_intensity_raw)
        window_days = int(window_days_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "limit, min_intensity, and window_days must be numeric"}), 400

    payload = build_insider_conviction_alerts(
        repo,
        min_intensity=min_intensity,
        window_days=window_days,
        limit=limit,
    )
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/narrative-alerts", methods=["GET"])
def research_narrative_alerts():
    repo = Repository(get_db())
    if not is_enabled("experimental_narrative_alerts", repo):
        return jsonify({"error": "feature_disabled", "flag": "experimental_narrative_alerts"}), 404

    limit_raw = request.args.get("limit", 30)
    min_divergence_raw = request.args.get("min_divergence", "0.6")
    try:
        limit = int(limit_raw)
        min_divergence = float(min_divergence_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "limit and min_divergence must be numeric"}), 400

    payload = build_narrative_divergence_alerts(
        repo,
        min_divergence=min_divergence,
        limit=limit,
    )
    return jsonify(_attach_research_meta(payload))


@research_bp.route("/rank/baserate", methods=["GET"])
def research_rank_baserate():
    repo = Repository(get_db())
    composite = request.args.get("composite", "deep_value")
    snapshot_limit_raw = request.args.get("snapshot_limit", 12)
    forward_quarters_raw = request.args.get("forward_quarters", 8)
    try:
        snapshot_limit = int(snapshot_limit_raw)
        forward_quarters = int(forward_quarters_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "snapshot_limit and forward_quarters must be integers"}), 400

    payload, status, error = validate_gate_base_rates(
        repo,
        composite=composite,
        snapshot_limit=snapshot_limit,
        forward_quarters=forward_quarters,
    )
    if error == "insufficient_history" or error == "insufficient_outcome_data":
        return jsonify({"error": error, "composite": composite}), 404
    if error:
        return jsonify({"error": error}), status
    return jsonify(_attach_research_meta(payload))
