from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..db import get_db
from ..repositories import Repository
from ..services.prices import PricesService
from ..services.research import ResearchService

research_bp = Blueprint("research", __name__, url_prefix="/api/research")


def get_research_service() -> ResearchService:
    repo = Repository(get_db())
    return ResearchService(repo, PricesService(repo))


@research_bp.route("/screener", methods=["GET"])
def research_screener():
    tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    if not tickers:
        return jsonify({"error": "No tickers provided"}), 400
    if len(tickers) > 100:
        return jsonify({"error": "Maximum 100 tickers per request"}), 400
    dimension = request.args.get("dimension", "MRY")
    payload = get_research_service().get_screener(tickers, dimension=dimension)
    return jsonify(payload)


@research_bp.route("/ticker/<ticker>", methods=["GET"])
def research_ticker(ticker: str):
    dimension = request.args.get("dimension", "MRY")
    gte = request.args.get("gte") or None
    payload = get_research_service().get_ticker_detail(ticker, dimension=dimension, gte=gte)
    if payload.get("error") == "not_found":
        return jsonify(payload), 404
    return jsonify(payload)
