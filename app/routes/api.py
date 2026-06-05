from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..db import get_db
from ..repositories import Repository
from ..services.fundamentals import FundamentalsService
from ..services.nasdaq import NasdaqService
from ..services.news import NewsService
from ..services.sec import SecClient


api_bp = Blueprint("api", __name__, url_prefix="/api")


def get_repo() -> Repository:
    return Repository(get_db())


def get_sec_client() -> SecClient:
    return SecClient(
        user_agent=current_app.config["SEC_USER_AGENT"],
        base_url=current_app.config["SEC_BASE_URL"],
        timeout=current_app.config["REQUEST_TIMEOUT"],
    )


def get_fundamentals_service() -> FundamentalsService:
    return FundamentalsService(get_repo(), get_sec_client())


def get_news_service() -> NewsService:
    return NewsService(
        repo=get_repo(),
        timeout=current_app.config["REQUEST_TIMEOUT"],
        cache_ttl_seconds=current_app.config["NEWS_HTTP_TTL_SECONDS"],
    )


def get_nasdaq_service() -> NasdaqService:
    return NasdaqService(
        api_key=current_app.config.get("NASDAQ_API_KEY"),
        timeout=current_app.config["REQUEST_TIMEOUT"],
    )


@api_bp.route("/search", methods=["GET"])
def search_ticker():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    rows = get_repo().search_companies(query)
    return jsonify(rows)


@api_bp.route("/ticker/<ticker>/summary", methods=["GET"])
def ticker_summary(ticker: str):
    repo = get_repo()
    company = repo.get_company_by_ticker(ticker)
    prices = []
    nasdaq = get_nasdaq_service()
    if nasdaq.is_enabled():
        try:
            prices = nasdaq.fetch_price_history(ticker.upper())
        except Exception:
            prices = []
    meta = company or {"ticker": ticker.upper(), "name": ticker.upper()}
    return jsonify({"prices": prices, "meta": meta})


@api_bp.route("/ticker/<ticker>/intraday", methods=["GET"])
def ticker_intraday(ticker: str):
    company = get_repo().get_company_by_ticker(ticker)
    return jsonify({"intraday": [], "tickerMeta": company})


@api_bp.route("/ticker/<ticker>/news", methods=["GET"])
def ticker_news(ticker: str):
    return jsonify(get_repo().get_company_news(ticker))


@api_bp.route("/tickers/top", methods=["GET"])
def tickers_top():
    tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    if not tickers:
        return jsonify({"quotes": {}})
    repo = get_repo()
    quotes = {}
    companies = {ticker: repo.get_company_by_ticker(ticker) for ticker in tickers}
    nasdaq = get_nasdaq_service()
    nasdaq_quotes = {}
    if nasdaq.is_enabled():
        try:
            nasdaq_quotes = nasdaq.fetch_top_quotes(tickers)
        except Exception:
            nasdaq_quotes = {}
    for ticker in tickers:
        company = companies.get(ticker) or {}
        quote = nasdaq_quotes.get(ticker, {})
        quotes[ticker] = {
            "last": quote.get("last"),
            "tngoLast": quote.get("last"),
            "bidPrice": None,
            "askPrice": None,
            "timestamp": quote.get("timestamp"),
            "name": company.get("name") or ticker,
            "prevClose": quote.get("prevClose"),
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
        }
    return jsonify({"quotes": quotes})


@api_bp.route("/tickers/daily-change", methods=["GET"])
def tickers_daily_change():
    tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    if not tickers:
        return jsonify({"changes": {}})
    nasdaq = get_nasdaq_service()
    if not nasdaq.is_enabled():
        return jsonify({"changes": {ticker: {"prevClose": None, "todayClose": None} for ticker in tickers}, "meta": {"source": "disabled"}})
    changes = nasdaq.fetch_daily_change(tickers)
    return jsonify({"changes": changes, "meta": {"source": "nasdaq"}})


@api_bp.route("/ticker/financials", methods=["GET"])
def tickers_financials():
    tickers = [item.strip().upper() for item in request.args.get("ticker", "").split(",") if item.strip()]
    if not tickers:
        return jsonify({"error": "No tickers provided"}), 400
    gte = request.args.get("gte")
    dimension = request.args.get("dimension")
    most_recent = str(request.args.get("mostRecent", "")).lower() in {"true", "1", "yes"}
    payload = get_fundamentals_service().get_financials_payload(tickers, gte, dimension, most_recent)
    return jsonify(payload)


@api_bp.route("/insiders/buying-sums", methods=["GET"])
def insiders_buying_sums():
    tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    nasdaq = get_nasdaq_service()
    if not nasdaq.is_enabled():
        return jsonify({"rows": [], "meta": {"source": "disabled"}})
    rows = nasdaq.fetch_insider_buying(tickers or None)
    return jsonify({"rows": rows, "meta": {"source": "nasdaq"}})


@api_bp.route("/ticker/<ticker>/sf2", methods=["GET"])
def ticker_sf2(ticker: str):
    nasdaq = get_nasdaq_service()
    if not nasdaq.is_enabled():
        return jsonify({"error": "NASDAQ_API_KEY is not configured"}), 503
    return jsonify(nasdaq.fetch_sf2(ticker))


@api_bp.route("/admin/status", methods=["GET"])
def admin_status():
    return jsonify(get_repo().status_snapshot())


@api_bp.route("/admin/sync-companies", methods=["POST"])
def sync_companies():
    payload = get_fundamentals_service().refresh_company_tickers(current_app.config["SEC_COMPANY_TICKERS_URL"])
    return jsonify(payload)


@api_bp.route("/admin/refresh-fundamentals", methods=["POST"])
def refresh_fundamentals():
    tickers = request.json.get("tickers") if request.is_json else None
    if not tickers:
        tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    if not tickers:
        return jsonify({"error": "No tickers provided"}), 400
    payload = get_fundamentals_service().refresh_fundamentals(tickers)
    return jsonify(payload)


@api_bp.route("/admin/ingest-feed", methods=["POST"])
def ingest_feed():
    body = request.get_json(silent=True) or {}
    feed_url = body.get("feed_url")
    name = body.get("name")
    category = body.get("category", "general")
    if not feed_url or not name:
        return jsonify({"error": "feed_url and name are required"}), 400
    payload = get_news_service().ingest_feed(feed_url=feed_url, name=name, category=category)
    return jsonify(payload)


@api_bp.route("/admin/default-feeds", methods=["GET"])
def default_feeds():
    return jsonify({"feeds": get_news_service().default_feeds()})


@api_bp.route("/admin/ingest-default-feeds", methods=["POST"])
def ingest_default_feeds():
    payload = get_news_service().ingest_default_feeds()
    return jsonify(payload)


@api_bp.route("/admin/bootstrap", methods=["POST"])
def bootstrap():
    body = request.get_json(silent=True) or {}
    tickers = body.get("tickers")
    if not tickers:
        tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    if not tickers:
        tickers = ["AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "AMZN", "META", "TSLA"]

    fundamentals_service = get_fundamentals_service()
    news_service = get_news_service()

    companies = fundamentals_service.refresh_company_tickers(current_app.config["SEC_COMPANY_TICKERS_URL"])
    fundamentals = fundamentals_service.refresh_fundamentals(tickers)
    feeds = news_service.ingest_default_feeds()
    return jsonify(
        {
            "companies": companies,
            "fundamentals": fundamentals,
            "feeds": feeds,
            "tickers": tickers,
        }
    )
