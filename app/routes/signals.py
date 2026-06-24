from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..db import get_db
from ..repositories import Repository, utc_now_iso
from ..services.feature_flags import is_enabled
from ..services.research_queue import dismiss_research_queue_item
from ..services.signal_state import (
    apply_user_state_to_signals,
    get_morning_brief,
    get_signal_state,
    touch_last_visited,
    update_signal_item_state,
)
from ..services.signals import get_signals

signals_bp = Blueprint("signals", __name__, url_prefix="/api")


def _attach_meta(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return payload
    meta = dict(payload.get("meta") or {})
    meta.setdefault("source", "sqlite")
    meta["computedAt"] = utc_now_iso()
    payload["meta"] = meta
    return payload


def _signals_disabled():
    return jsonify({"error": "feature_disabled", "flag": "experimental_signals"}), 404


def _parse_list_query(param: str) -> list[str] | None:
    values = [item.strip().lower() for item in request.args.get(param, "").split(",") if item.strip()]
    return values or None


def _parse_signal_list_request():
    limit_raw = request.args.get("limit", 50)
    max_age_raw = request.args.get("max_age_days", 30)
    min_importance_raw = request.args.get("min_importance")
    try:
        limit = int(limit_raw)
        max_age_days = int(max_age_raw)
        min_importance = float(min_importance_raw) if min_importance_raw not in (None, "") else None
    except (TypeError, ValueError):
        return None, (jsonify({"error": "limit, max_age_days must be integers; min_importance must be numeric"}), 400)

    signal_types = _parse_list_query("signal_types")
    tickers = [
        item.strip().upper()
        for item in request.args.get("tickers", "").split(",")
        if item.strip()
    ] or None
    portfolio_only = request.args.get("portfolio_only", "false").strip().lower() in ("1", "true", "yes", "on")
    include_dismissed = request.args.get("include_dismissed", "false").strip().lower() in ("1", "true", "yes", "on")
    hide_read = request.args.get("hide_read", "true").strip().lower() in ("1", "true", "yes", "on")
    hide_snoozed = request.args.get("hide_snoozed", "true").strip().lower() in ("1", "true", "yes", "on")
    lens = request.args.get("lens", "").strip().lower() or None

    return {
        "limit": limit,
        "max_age_days": max_age_days,
        "min_importance": min_importance,
        "signal_types": signal_types,
        "tickers": tickers,
        "portfolio_only": portfolio_only,
        "include_dismissed": include_dismissed,
        "hide_read": hide_read,
        "hide_snoozed": hide_snoozed,
        "lens": lens,
    }, None


def _lens_signal_types(lens: str | None) -> list[str] | None:
    if not lens:
        return None
    presets = {
        "radar_insider": ["insider_cluster_buy", "new_insider_cluster"],
        "radar_rerating": ["rerating_candidate", "high_conviction", "narrative_divergence"],
        "radar_distress": ["going_concern_8k", "bankruptcy", "risk_flag"],
        "radar_activist": ["activist_13d"],
        "radar_unusual": ["unusual_volume"],
        "watch_alert": [
            "going_concern_8k",
            "guidance_cut",
            "earnings_miss",
            "activist_13d",
            "insider_cluster_buy",
        ],
    }
    return presets.get(lens)


def _fetch_signals_payload(repo: Repository, params: dict) -> dict:
    lens_types = _lens_signal_types(params.get("lens"))
    signal_types = params["signal_types"]
    if lens_types:
        signal_types = lens_types if not signal_types else [t for t in signal_types if t in lens_types]

    min_importance = params["min_importance"]
    if params.get("lens") == "watch_alert" and min_importance is None:
        min_importance = 0.55

    payload = get_signals(
        repo,
        limit=params["limit"],
        signal_types=signal_types,
        tickers=params["tickers"],
        portfolio_only=params["portfolio_only"] or params.get("lens") == "watch",
        include_dismissed=params["include_dismissed"],
        max_age_days=params["max_age_days"],
        min_importance=min_importance,
    )
    state = get_signal_state(repo)
    payload["items"] = apply_user_state_to_signals(
        payload.get("items") or [],
        state,
        hide_read=params["hide_read"],
        hide_snoozed=params["hide_snoozed"],
    )
    payload["userState"] = {"lastVisitedAt": state.get("lastVisitedAt")}
    return payload


@signals_bp.route("/signals", methods=["GET"])
def signals_list():
    repo = Repository(get_db())
    if not is_enabled("experimental_signals", repo):
        return _signals_disabled()

    params, error = _parse_signal_list_request()
    if error:
        return error
    if params.get("lens") == "watch":
        params["portfolio_only"] = True

    payload = _fetch_signals_payload(repo, params)
    return jsonify(_attach_meta(payload))


@signals_bp.route("/signals/morning-brief", methods=["GET"])
def signals_morning_brief():
    repo = Repository(get_db())
    if not is_enabled("experimental_signals", repo):
        return _signals_disabled()

    limit_raw = request.args.get("limit", 12)
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400

    base_params, error = _parse_signal_list_request()
    if error:
        return error
    base_params["limit"] = max(limit * 5, 120)
    base_params["hide_read"] = False
    base_params["hide_snoozed"] = False

    signals_payload = get_signals(
        repo,
        limit=base_params["limit"],
        portfolio_only=base_params["portfolio_only"],
        include_dismissed=base_params["include_dismissed"],
        max_age_days=base_params["max_age_days"],
        min_importance=base_params["min_importance"],
    )
    brief = get_morning_brief(repo, signals_payload, limit=limit)
    return jsonify(_attach_meta(brief))


@signals_bp.route("/signals/state", methods=["GET"])
def signals_state_get():
    repo = Repository(get_db())
    if not is_enabled("experimental_signals", repo):
        return _signals_disabled()
    return jsonify(_attach_meta(get_signal_state(repo)))


@signals_bp.route("/signals/state", methods=["PUT"])
def signals_state_put():
    repo = Repository(get_db())
    if not is_enabled("experimental_signals", repo):
        return _signals_disabled()

    body = request.get_json(silent=True) or {}
    if body.get("touchLastVisited"):
        state = touch_last_visited(repo)
        return jsonify(_attach_meta(state))

    dedup_key = body.get("dedupKey") or body.get("dedup_key")
    if not dedup_key:
        return jsonify({"error": "dedupKey is required"}), 400

    read = body.get("read")
    snooze_days = body.get("snoozeDays") if body.get("snoozeDays") is not None else body.get("snooze_days")
    clear_snooze = bool(body.get("clearSnooze") or body.get("clear_snooze"))

    result = update_signal_item_state(
        repo,
        dedup_key,
        read=read if "read" in body else None,
        snooze_days=int(snooze_days) if snooze_days is not None else None,
        clear_snooze=clear_snooze,
    )
    if result.get("error"):
        return jsonify(result), 400
    return jsonify(_attach_meta({"state": get_signal_state(repo), "updated": dedup_key}))


@signals_bp.route("/signals/dismiss", methods=["POST"])
def signals_dismiss():
    repo = Repository(get_db())
    if not is_enabled("experimental_signals", repo):
        return _signals_disabled()

    body = request.get_json(silent=True) or {}
    ticker = body.get("ticker")
    event_type = body.get("eventType") or body.get("event_type")
    event_date = body.get("eventDate") or body.get("event_date")
    dedup_key = body.get("dedupKey") or body.get("dedup_key")

    if ticker and event_type:
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

    if dedup_key:
        update_signal_item_state(repo, dedup_key, read=True)

    return jsonify(_attach_meta({"dismissed": True, "ticker": ticker, "dedupKey": dedup_key}))
