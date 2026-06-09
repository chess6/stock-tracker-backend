from __future__ import annotations

import functools

from flask import Blueprint, current_app, jsonify, request

from ..clients.sec import SecClient
from ..db import get_db
from ..repositories import Repository
from ..services.fundamentals import FundamentalsService
from ..services.insiders import InsidersService
from ..services.news import NewsService
from ..services.prices import PricesService


api_bp = Blueprint("api", __name__, url_prefix="/api")


def _check_admin_key():
    """Return an error response if ADMIN_API_KEY is configured and the request lacks it."""
    expected = current_app.config.get("ADMIN_API_KEY")
    if not expected:
        return None
    provided = (
        request.headers.get("X-Api-Key")
        or request.args.get("apiKey")
    )
    if provided == expected:
        return None
    return jsonify({"error": "Unauthorized — set X-Api-Key header or apiKey query param"}), 401


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
    source = "sqlite" if prices else "disabled"
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
    tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    payload = get_repo().list_unique_articles(
        limit=limit,
        offset=offset,
        q=q,
        category=category,
        source_domain=source_domain,
        tickers=tickers or None,
    )
    return jsonify(payload)


@api_bp.route("/macro/snapshot", methods=["GET"])
def macro_snapshot():
    from ..services.macro import MacroSnapshotService

    return jsonify(MacroSnapshotService(repo=get_repo()).snapshot())


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
            "sector": company.get("sector"),
            "industry": company.get("industry"),
            "prevClose": quote.get("prevClose"),
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
        }
    return jsonify({"quotes": quotes, "meta": {"source": source}})


@api_bp.route("/tickers/market-stats", methods=["GET"])
def tickers_market_stats():
    tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    if not tickers:
        return jsonify({"stats": {}})
    stats = get_prices_service().get_market_stats(tickers)
    return jsonify({"stats": stats, "meta": {"source": "sqlite"}})


@api_bp.route("/tickers/movers", methods=["GET"])
def tickers_movers():
    window = request.args.get("window", "d").lower()
    if window not in {"d", "w"}:
        window = "d"
    threshold = request.args.get("threshold", default=10.0, type=float)
    limit = request.args.get("limit", default=50, type=int)
    movers = get_prices_service().get_movers(window=window, threshold=threshold, limit=limit)
    return jsonify({"movers": movers, "meta": {"window": window, "threshold": threshold, "source": "sqlite"}})


@api_bp.route("/companies/industries", methods=["GET"])
def companies_industries():
    groups = get_repo().list_industry_groups()
    return jsonify({"industries": groups})


@api_bp.route("/companies/peers", methods=["GET"])
def companies_peers():
    industry = request.args.get("industry", "").strip()
    if not industry:
        return jsonify({"error": "industry query param required"}), 400
    sector = request.args.get("sector", "").strip() or None
    limit = request.args.get("limit", default=100, type=int)
    peers = get_repo().fetch_industry_peers(industry, sector=sector, limit=limit)
    return jsonify({"peers": peers, "industry": industry, "sector": sector})


@api_bp.route("/tickers/daily-change", methods=["GET"])
def tickers_daily_change():
    tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    if not tickers:
        return jsonify({"changes": {}})
    changes = get_prices_service().get_daily_changes(tickers)
    source = "sqlite" if any(item.get("todayClose") is not None for item in changes.values()) else "disabled"
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
    min_buy6m = request.args.get("min_buy6m", type=float)
    rows = get_insiders_service().buying_sums(tickers or None, min_buy6m=min_buy6m)
    source = "sec_edgar" if rows else "disabled"
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


# legacy URL: /sf2 keeps vendor-compatible datatable shape for existing clients.
# Planned alias: /ticker/<ticker>/insiders (same handler) once frontend fully migrates.
@api_bp.route("/ticker/<ticker>/sf2", methods=["GET"])
def ticker_sf2(ticker: str):
    payload = get_insiders_service().sf2_payload(ticker)
    if payload.get("datatable", {}).get("data"):
        return jsonify(payload)
    return jsonify({"error": "No insider data in cache. Run bootstrap or refresh insiders."}), 404


# -- user preferences (user-facing, no admin auth) -------------------------


@api_bp.route("/preferences", methods=["GET"])
def get_preferences():
    return jsonify(get_repo().get_user_preferences())


@api_bp.route("/preferences", methods=["PUT"])
def update_preferences():
    body = request.get_json(silent=True) or {}
    theme = body.get("theme")
    portfolio = body.get("portfolio")
    if theme is not None and theme not in {"dark", "light"}:
        return jsonify({"error": "theme must be 'dark' or 'light'"}), 400
    if portfolio is not None and not isinstance(portfolio, list):
        return jsonify({"error": "portfolio must be an array of tickers"}), 400
    normalized_portfolio = None
    if portfolio is not None:
        normalized_portfolio = [
            str(ticker).strip().upper()
            for ticker in portfolio
            if str(ticker).strip()
        ]
    try:
        payload = get_repo().update_user_preferences(
            theme=theme,
            portfolio=normalized_portfolio,
        )
    except Exception as exc:
        current_app.logger.exception("Failed to update preferences")
        return jsonify({"error": str(exc)}), 500
    return jsonify(payload)


# -- watchlists (user-facing, no admin auth) -------------------------------


@api_bp.route("/watchlists", methods=["GET"])
def list_watchlists():
    return jsonify({"watchlists": get_repo().list_watchlists()})


@api_bp.route("/watchlists/<name>", methods=["GET"])
def get_watchlist(name: str):
    wl = get_repo().get_watchlist(name)
    if not wl:
        return jsonify({"error": "Watchlist not found"}), 404
    return jsonify(wl)


@api_bp.route("/watchlists/<name>", methods=["PUT"])
def upsert_watchlist(name: str):
    body = request.get_json(silent=True) or {}
    repo = get_repo()
    wl_id = repo.upsert_watchlist(name, description=body.get("description"))
    if "tickers" in body:
        repo.set_watchlist_tickers(wl_id, body["tickers"])
    wl = repo.get_watchlist(name)
    return jsonify(wl)


@api_bp.route("/watchlists/<name>", methods=["DELETE"])
def delete_watchlist(name: str):
    repo = get_repo()
    wl = repo.get_watchlist(name)
    if not wl:
        return jsonify({"error": "Watchlist not found"}), 404
    repo.conn.execute("DELETE FROM watchlists WHERE id = ?", (wl["id"],))
    repo.conn.commit()
    return jsonify({"deleted": name})


@api_bp.route("/watchlists/<name>/tickers", methods=["POST"])
def add_watchlist_ticker(name: str):
    body = request.get_json(silent=True) or {}
    ticker = (body.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400
    repo = get_repo()
    wl = repo.get_watchlist(name)
    if not wl:
        return jsonify({"error": "Watchlist not found"}), 404
    repo.add_ticker_to_watchlist(wl["id"], ticker)
    return jsonify({"added": ticker, "watchlist": name})


@api_bp.route("/watchlists/<name>/tickers/<ticker>", methods=["DELETE"])
def remove_watchlist_ticker(name: str, ticker: str):
    repo = get_repo()
    wl = repo.get_watchlist(name)
    if not wl:
        return jsonify({"error": "Watchlist not found"}), 404
    repo.remove_ticker_from_watchlist(wl["id"], ticker.upper())
    return jsonify({"removed": ticker.upper(), "watchlist": name})


@api_bp.before_request
def _admin_auth_guard():
    if request.path.startswith("/api/admin/"):
        result = _check_admin_key()
        if result is not None:
            return result


@api_bp.route("/admin/status", methods=["GET"])
def admin_status():
    return jsonify(get_repo().status_snapshot())


@api_bp.route("/admin/job-runs", methods=["GET"])
def admin_job_runs():
    limit = min(max(int(request.args.get("limit", 25)), 1), 100)
    return jsonify({"runs": get_repo().list_job_runs(limit=limit)})


@api_bp.route("/admin/sync-companies", methods=["POST"])
def sync_companies():
    try:
        payload = get_fundamentals_service().refresh_company_tickers(current_app.config["SEC_COMPANY_TICKERS_URL"])
    except Exception as exc:
        current_app.logger.exception("Company sync failed")
        return jsonify({"error": str(exc)}), 500
    return jsonify(payload)


@api_bp.route("/admin/enrich-metadata", methods=["POST"])
def enrich_metadata():
    all_missing = request.args.get("all", "").lower() in {"1", "true", "yes"}
    tickers = request.json.get("tickers") if request.is_json else None
    if not tickers and not all_missing:
        tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    if not tickers and not all_missing:
        return jsonify({"error": "No tickers provided (or pass ?all=true for missing sector/industry)"}), 400
    try:
        payload = get_fundamentals_service().enrich_company_metadata(
            tickers,
            all_missing=all_missing,
        )
    except Exception as exc:
        current_app.logger.exception("Metadata enrichment failed")
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
    force_refresh = str(request.args.get("forceRefresh", "true")).lower() in {"true", "1", "yes"}
    extract_articles = str(request.args.get("extractArticles", "false")).lower() in {"true", "1", "yes"}
    feed_timeout_seconds = request.args.get("feedTimeoutSeconds", type=float)
    max_articles_per_feed = request.args.get("maxArticlesPerFeed", type=int)
    body = request.get_json(silent=True) or {}
    raw_extract = body.get("extract_articles")
    if raw_extract is None:
        raw_extract = body.get("extractArticles")
    if raw_extract is not None:
        extract_articles = str(raw_extract).lower() in {"true", "1", "yes"}
    if feed_timeout_seconds is None:
        raw_timeout = body.get("feed_timeout_seconds") or body.get("feedTimeoutSeconds")
        if raw_timeout is not None:
            feed_timeout_seconds = float(raw_timeout)
    if max_articles_per_feed is None:
        raw_max = body.get("max_articles_per_feed") or body.get("maxArticlesPerFeed")
        if raw_max is not None:
            max_articles_per_feed = int(raw_max)
    tickers_csv = request.args.get("tickers", "")
    if not tickers_csv:
        tickers_csv = body.get("tickers", "")
    tickers = [t.strip().upper() for t in tickers_csv.split(",") if t.strip()] if tickers_csv else None
    kwargs = {"force_refresh": force_refresh, "extract_articles": extract_articles}
    if feed_timeout_seconds is not None:
        kwargs["feed_timeout_seconds"] = feed_timeout_seconds
    if max_articles_per_feed is not None:
        kwargs["max_articles_per_feed"] = max_articles_per_feed
    if tickers:
        kwargs["tickers"] = tickers
    payload = get_news_service().ingest_default_feeds(**kwargs)
    repo = get_repo()
    pending = repo.conn.execute(
        """
        SELECT COUNT(*) FROM articles
        WHERE duplicate_of_article_id IS NULL
          AND COALESCE(pipeline_status, 'pending') IN ('pending', 'error')
        """
    ).fetchone()[0]
    if pending > 0:
        repo.enqueue_job("enrich_articles", {"limit": 50}, priority=55)
    return jsonify(payload)


def _coerce_bool(value, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "yes"}


@api_bp.route("/admin/enrich-articles/status", methods=["GET"])
def enrich_articles_status():
    repo = get_repo()
    counts = repo.get_pipeline_status_counts()
    counts["retag_candidates"] = repo.count_articles_for_retag(only_missing_enrichment=True)
    counts["retag_total"] = repo.count_articles_for_retag(only_missing_enrichment=False)
    counts["api_features"] = {"retag_endpoint": True, "pipeline_version": 2}
    return jsonify(counts)


@api_bp.route("/admin/retag-articles", methods=["POST"])
def retag_articles_admin():
    body = request.get_json(silent=True) or {}
    limit = request.args.get("limit", type=int) or body.get("limit") or 25
    offset = request.args.get("offset", type=int) or body.get("offset") or 0
    enable_embeddings = _coerce_bool(body.get("enable_embeddings"), default=False)
    enable_finbert = _coerce_bool(body.get("enable_finbert"), default=False)
    retag_all = _coerce_bool(request.args.get("retagAll"), default=True)
    if "retag_all" in body:
        retag_all = _coerce_bool(body.get("retag_all"), default=True)
    elif "retagAll" in body:
        retag_all = _coerce_bool(body.get("retagAll"), default=True)
    from ..services.article_pipeline import ArticlePipeline

    try:
        payload = ArticlePipeline(
            get_repo(),
            enable_embeddings=enable_embeddings,
            enable_finbert=enable_finbert,
        ).retag_batch(
            limit=int(limit),
            offset=int(offset),
            retag_all=bool(retag_all),
        )
    except Exception as exc:
        current_app.logger.exception("Article retag failed")
        return jsonify({"error": str(exc)}), 500
    return jsonify(payload)


@api_bp.route("/admin/enrich-articles", methods=["POST"])
def enrich_articles_admin():
    body = request.get_json(silent=True) or {}
    limit = request.args.get("limit", type=int) or body.get("limit") or 25
    enable_embeddings = _coerce_bool(body.get("enable_embeddings"), default=True)
    enable_finbert = _coerce_bool(body.get("enable_finbert"), default=True)
    force = _coerce_bool(request.args.get("force"), default=False) or _coerce_bool(body.get("force"))
    retag_only = (
        _coerce_bool(request.args.get("retagOnly"), default=False)
        or _coerce_bool(request.args.get("retag_only"), default=False)
        or _coerce_bool(body.get("retag_only"), default=False)
        or _coerce_bool(body.get("retagOnly"), default=False)
    )
    from ..services.article_pipeline import ArticlePipeline

    repo = get_repo()
    requeued = 0
    if force and not retag_only:
        requeued = repo.requeue_completed_articles(limit=max(int(limit) * 20, 500))

    try:
        pipeline = ArticlePipeline(
            repo,
            enable_embeddings=bool(enable_embeddings),
            enable_finbert=bool(enable_finbert),
        )
        if retag_only:
            offset = request.args.get("offset", type=int) or body.get("offset") or 0
            retag_all = _coerce_bool(request.args.get("retagAll"), default=True)
            if "retag_all" in body:
                retag_all = _coerce_bool(body.get("retag_all"), default=True)
            elif "retagAll" in body:
                retag_all = _coerce_bool(body.get("retagAll"), default=True)
            payload = pipeline.retag_batch(
                limit=int(limit),
                offset=int(offset),
                retag_all=bool(retag_all),
            )
        else:
            payload = pipeline.process_batch(limit=int(limit))
        if requeued:
            payload["requeued"] = requeued
    except Exception as exc:
        current_app.logger.exception("Article enrichment failed")
        return jsonify({"error": str(exc)}), 500
    return jsonify(payload)


@api_bp.route("/news/analytics/event-reactions", methods=["GET"])
def news_event_reaction_analytics():
    limit = min(max(int(request.args.get("limit", 20)), 1), 100)
    return jsonify({"events": get_repo().event_reaction_analytics(limit=limit)})


@api_bp.route("/admin/dedup-articles", methods=["POST"])
def dedup_articles():
    from ..services.article_enrichment import infer_topic_cluster, simple_sentiment

    repo = get_repo()
    body = request.get_json(silent=True) or {}
    enrich_limit = request.args.get("enrichLimit", type=int)
    if enrich_limit is None:
        raw_limit = body.get("enrich_limit") or body.get("enrichLimit")
        if raw_limit is not None:
            enrich_limit = int(raw_limit)
    if enrich_limit is None or enrich_limit <= 0:
        enrich_limit = 500
    dates_normalized = repo.normalize_published_dates()
    deduplication = repo.deduplicate_articles()
    enriched = 0
    rows = repo.conn.execute(
        """
        SELECT id, title, summary
        FROM articles
        WHERE sentiment_label IS NULL
        ORDER BY id DESC
        LIMIT ?
        """,
        (enrich_limit,),
    ).fetchall()
    for row in rows:
        text = " ".join(filter(None, [row["title"], row["summary"]]))
        label, score = simple_sentiment(text)
        topic = infer_topic_cluster(text)
        repo.conn.execute(
            "UPDATE articles SET sentiment_label = ?, sentiment_score = ?, topic_cluster_id = ? WHERE id = ?",
            (label, score, topic, row["id"]),
        )
        enriched += 1
    repo.conn.commit()
    return jsonify({
        "datesNormalized": dates_normalized,
        "deduplication": deduplication,
        "sentimentEnriched": enriched,
        "enrichLimit": enrich_limit,
    })


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


@api_bp.route("/admin/refresh-macro", methods=["POST"])
def refresh_macro():
    from ..services.macro import MACRO_TICKERS

    try:
        payload = get_prices_service().refresh_prices(MACRO_TICKERS)
    except Exception as exc:
        current_app.logger.exception("Macro price refresh failed")
        return jsonify({"error": str(exc)}), 500
    return jsonify(payload)


@api_bp.route("/admin/backfill-market-reactions", methods=["POST"])
def backfill_market_reactions_admin():
    from ..services.market_reaction import backfill_market_reactions
    from ..services.narrative import clear_narrative_cache

    body = request.get_json(silent=True) or {}
    ticker = request.args.get("ticker") or body.get("ticker")
    limit = request.args.get("limit", type=int) or body.get("limit") or 200
    limit = min(max(int(limit), 1), 1000)
    try:
        payload = backfill_market_reactions(get_repo(), ticker=ticker, limit=limit)
        clear_narrative_cache()
    except Exception as exc:
        current_app.logger.exception("Market reaction backfill failed")
        return jsonify({"error": str(exc)}), 500
    return jsonify(payload)


@api_bp.route("/admin/refresh-prices", methods=["POST"])
def refresh_prices():
    tickers = request.json.get("tickers") if request.is_json else None
    if not tickers:
        tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    if not tickers:
        return jsonify({"error": "No tickers provided"}), 400
    days = request.args.get("days", type=int)
    body = request.get_json(silent=True) or {}
    if days is None and body.get("days") is not None:
        days = int(body["days"])
    try:
        if days is not None and days > 0:
            payload = get_prices_service().refresh_prices(tickers, days=days)
        else:
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
    max_filings = request.args.get("maxFilingsPerCompany", type=int)
    body = request.get_json(silent=True) or {}
    if max_filings is None:
        raw_max = body.get("max_filings_per_company") or body.get("maxFilingsPerCompany")
        if raw_max is not None:
            max_filings = int(raw_max)
    try:
        if max_filings is not None and max_filings > 0:
            payload = get_insiders_service().refresh_insiders(
                tickers,
                max_filings_per_company=max_filings,
            )
        else:
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
