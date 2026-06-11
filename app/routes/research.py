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
from ..services.composite_ranking import get_rank_history, run_composite_rank
from ..services.rank_validation import validate_composite_rank
from ..services.thesis_engine import evaluate_thesis_for_ticker, thesis_to_api
from ..services.screening import run_composable_screen
from ..services.sector_stats import sector_stats_for_tickers

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


@research_bp.route("/rank", methods=["GET"])
def research_rank():
    repo = Repository(get_db())
    tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    universe = request.args.get("universe", "sp500")
    composite = request.args.get("composite", "deep_value")
    limit_raw = request.args.get("limit", 50)
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
