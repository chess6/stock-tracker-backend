from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..clients.sec import SecClient
from ..db import get_db
from ..repositories import Repository
from ..services.fundamentals import FundamentalsService
from ..services.insiders import InsidersService
from ..services.nasdaq import NasdaqService
from ..services.news import NewsService
from ..services.prices import PricesService


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


def get_prices_service() -> PricesService:
    return PricesService(get_repo())


def get_insiders_service() -> InsidersService:
    return InsidersService(get_repo(), get_sec_client())


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
    prices = get_prices_service().get_price_history(ticker.upper())
    source = "sqlite"
    if not prices:
        nasdaq = get_nasdaq_service()
        if nasdaq.is_enabled():
            try:
                prices = nasdaq.fetch_price_history(ticker.upper())
                source = "nasdaq"
            except Exception:
                prices = []
    meta = company or {"ticker": ticker.upper(), "name": ticker.upper()}
    return jsonify({"prices": prices, "meta": meta, "source": source})


@api_bp.route("/ticker/<ticker>/intraday", methods=["GET"])
def ticker_intraday(ticker: str):
    company = get_repo().get_company_by_ticker(ticker)
    return jsonify({"intraday": [], "tickerMeta": company})


@api_bp.route("/news", methods=["GET"])
def news_feed():
    limit = min(max(int(request.args.get("limit", 50)), 1), 200)
    offset = max(int(request.args.get("offset", 0)), 0)
    q = request.args.get("q", "").strip() or None
    category = request.args.get("category", "").strip() or None
    source_domain = request.args.get("sourceDomain", "").strip() or None
    payload = get_repo().list_unique_articles(
        limit=limit,
        offset=offset,
        q=q,
        category=category,
        source_domain=source_domain,
    )
    return jsonify(payload)


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
    price_quotes = get_prices_service().get_quotes(tickers)
    source = "sqlite" if any(price_quotes.get(t, {}).get("last") is not None for t in tickers) else "disabled"
    if source == "disabled":
        nasdaq = get_nasdaq_service()
        if nasdaq.is_enabled():
            try:
                price_quotes = nasdaq.fetch_top_quotes(tickers)
                source = "nasdaq"
            except Exception:
                price_quotes = {}
    for ticker in tickers:
        company = companies.get(ticker) or {}
        quote = price_quotes.get(ticker, {})
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
    return jsonify({"quotes": quotes, "meta": {"source": source}})


@api_bp.route("/tickers/daily-change", methods=["GET"])
def tickers_daily_change():
    tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    if not tickers:
        return jsonify({"changes": {}})
    changes = get_prices_service().get_daily_changes(tickers)
    source = "sqlite"
    if not any(item.get("todayClose") is not None for item in changes.values()):
        nasdaq = get_nasdaq_service()
        if nasdaq.is_enabled():
            try:
                changes = nasdaq.fetch_daily_change(tickers)
                source = "nasdaq"
            except Exception:
                pass
    if source == "sqlite" and not any(item.get("todayClose") is not None for item in changes.values()):
        source = "disabled"
    return jsonify({"changes": changes, "meta": {"source": source}})


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
    rows = get_insiders_service().buying_sums(tickers or None)
    source = "sec_edgar" if rows else "disabled"
    if not rows:
        nasdaq = get_nasdaq_service()
        if nasdaq.is_enabled():
            try:
                rows = nasdaq.fetch_insider_buying(tickers or None)
                source = "nasdaq"
            except Exception:
                rows = []
    if tickers:
        by_ticker = {row["ticker"]: row for row in rows if row.get("ticker")}
        rows = [
            by_ticker.get(
                ticker,
                {
                    "ticker": ticker,
                    "company": None,
                    "buy6m": 0.0,
                    "buy3m": 0.0,
                    "buy1m": 0.0,
                    "owners6m": 0,
                },
            )
            for ticker in tickers
        ]
    return jsonify({"rows": rows, "meta": {"source": source}})


@api_bp.route("/ticker/<ticker>/sf2", methods=["GET"])
def ticker_sf2(ticker: str):
    payload = get_insiders_service().sf2_payload(ticker)
    if payload.get("datatable", {}).get("data"):
        return jsonify(payload)
    nasdaq = get_nasdaq_service()
    if nasdaq.is_enabled():
        try:
            return jsonify(nasdaq.fetch_sf2(ticker))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 502
    return jsonify({"error": "No insider data in cache. Run bootstrap or refresh insiders."}), 404


@api_bp.route("/admin/status", methods=["GET"])
def admin_status():
    return jsonify(get_repo().status_snapshot())


@api_bp.route("/admin/sync-companies", methods=["POST"])
def sync_companies():
    try:
        payload = get_fundamentals_service().refresh_company_tickers(current_app.config["SEC_COMPANY_TICKERS_URL"])
    except Exception as exc:
        current_app.logger.exception("Company sync failed")
        return jsonify({"error": str(exc)}), 500
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
    prices_service = get_prices_service()
    insiders_service = get_insiders_service()

    try:
        companies = fundamentals_service.refresh_company_tickers(current_app.config["SEC_COMPANY_TICKERS_URL"])
        fundamentals = fundamentals_service.refresh_fundamentals(tickers)
        feeds = news_service.ingest_default_feeds(extract_articles=False, max_articles_per_feed=25)
        prices = prices_service.refresh_prices(tickers)
        insiders = insiders_service.refresh_insiders(tickers)
    except Exception as exc:
        current_app.logger.exception("Bootstrap failed")
        return jsonify({"error": str(exc), "tickers": tickers}), 500

    return jsonify(
        {
            "companies": companies,
            "fundamentals": fundamentals,
            "feeds": feeds,
            "prices": prices,
            "insiders": insiders,
            "tickers": tickers,
        }
    )


@api_bp.route("/admin/refresh-prices", methods=["POST"])
def refresh_prices():
    tickers = request.json.get("tickers") if request.is_json else None
    if not tickers:
        tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    if not tickers:
        return jsonify({"error": "No tickers provided"}), 400
    try:
        payload = get_prices_service().refresh_prices(tickers)
    except Exception as exc:
        current_app.logger.exception("Price refresh failed")
        return jsonify({"error": str(exc)}), 500
    return jsonify(payload)


@api_bp.route("/admin/refresh-insiders", methods=["POST"])
def refresh_insiders():
    tickers = request.json.get("tickers") if request.is_json else None
    if not tickers:
        tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    if not tickers:
        return jsonify({"error": "No tickers provided"}), 400
    try:
        payload = get_insiders_service().refresh_insiders(tickers)
    except Exception as exc:
        current_app.logger.exception("Insider refresh failed")
        return jsonify({"error": str(exc)}), 500
    return jsonify(payload)


@api_bp.route("/admin/enqueue-job", methods=["POST"])
def enqueue_job():
    body = request.get_json(silent=True) or {}
    job_type = body.get("job_type") or request.args.get("job_type")
    if not job_type:
        return jsonify({"error": "job_type is required"}), 400
    payload = body.get("payload") or {}
    if not payload and request.args.get("tickers"):
        payload["tickers"] = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    job_id = get_repo().enqueue_job(job_type, payload)
    return jsonify({"jobId": job_id, "jobType": job_type, "payload": payload})
