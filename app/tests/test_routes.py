from __future__ import annotations

from app.db import get_db
from app.repositories import Repository


def test_search_and_financial_routes_use_sqlite_cache(app, client):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "AAPL", "name": "Apple Inc", "cik": "0000320193"}])
        company = repo.get_company_by_ticker("AAPL")
        repo.upsert_fundamentals(
            [
                {
                    "company_id": company["id"],
                    "metric": "revenue",
                    "value": 1000.0,
                    "unit": "USD",
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                    "form": "10-K",
                    "accession": "1",
                    "source": "sec_companyfacts",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
                },
                {
                    "company_id": company["id"],
                    "metric": "eps",
                    "value": 5.0,
                    "unit": "USD/shares",
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                    "form": "10-K",
                    "accession": "1",
                    "source": "sec_companyfacts",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "EarningsPerShareDiluted",
                },
                {
                    "company_id": company["id"],
                    "metric": "sharesbas",
                    "value": 10.0,
                    "unit": "shares",
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                    "form": "10-K",
                    "accession": "1",
                    "source": "sec_companyfacts",
                    "taxonomy": "dei",
                    "xbrl_concept": "EntityCommonStockSharesOutstanding",
                },
            ]
        )
        repo.upsert_prices(
            "AAPL",
            [{"date": "2025-01-20", "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "volume": 100}],
            source="test",
        )

    search_response = client.get("/api/search?q=AAPL")
    assert search_response.status_code == 200
    assert search_response.get_json()[0]["ticker"] == "AAPL"

    financial_response = client.get("/api/ticker/financials?ticker=AAPL&mostRecent=true")
    assert financial_response.status_code == 200
    payload = financial_response.get_json()
    assert payload["metrics"]["AAPL"]["marketCap"] == 50.0
    assert payload["raw"]["datatable"]["data"][0][0] == "AAPL"


def test_news_feed_returns_unique_articles(app, client):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "AAPL", "name": "Apple Inc", "cik": "0000320193"}])
        company = repo.get_company_by_ticker("AAPL")
        primary_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/story-1",
                "url_hash": "hash-story-1",
                "title": "Apple launches product",
                "summary": "AAPL news summary",
                "source_domain": "example.com",
                "published_at": "2025-06-01T12:00:00Z",
                "fetched_at": "2025-06-01T12:05:00Z",
                "content_hash": "content-1",
                "raw_source": "test",
            }
        )
        repo.upsert_article(
            {
                "canonical_url": "https://example.com/story-1-dup",
                "url_hash": "hash-story-1-dup",
                "title": "Apple launches product duplicate",
                "summary": "duplicate",
                "source_domain": "example.com",
                "published_at": "2025-06-01T11:00:00Z",
                "fetched_at": "2025-06-01T11:05:00Z",
                "content_hash": "content-2",
                "duplicate_of_article_id": primary_id,
                "raw_source": "test",
            }
        )
        repo.link_entity_match(
            primary_id,
            {
                "company_id": company["id"],
                "match_type": "headline_ticker",
                "match_strategy": "headline_ticker",
                "confidence": 0.96,
                "extraction_stage": "enrichment",
            },
        )

    response = client.get("/api/news?limit=10")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total"] == 1
    assert len(payload["articles"]) == 1
    assert payload["articles"][0]["title"] == "Apple launches product"
    assert payload["articles"][0]["tickers"] == ["AAPL"]
    assert payload["articles"][0]["tickerMatches"][0]["matchStrategy"] == "headline_ticker"
    assert payload["articles"][0]["tickerMatches"][0]["confidence"] == 0.96


def test_news_feed_orders_by_published_at_desc(app, client):
    with app.app_context():
        repo = Repository(get_db())
        old_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/old-high-rank",
                "url_hash": "hash-old-rank",
                "title": "Older semiconductor supply chain report",
                "summary": "Legacy chip inventory analysis from June",
                "source_domain": "reuters.com",
                "published_at": "2025-06-01T12:00:00Z",
                "fetched_at": "2025-06-01T12:05:00Z",
                "content_hash": "content-old",
                "raw_source": "test",
            },
            skip_dedup=True,
        )
        repo.update_article_ranking(old_id, rank_score=99.0)
        new_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/new-low-rank",
                "url_hash": "hash-new-rank",
                "title": "Fresh cloud infrastructure earnings recap",
                "summary": "Latest hyperscaler quarterly results roundup",
                "source_domain": "bbc.co.uk",
                "published_at": "2025-06-10T12:00:00Z",
                "fetched_at": "2025-06-10T12:05:00Z",
                "content_hash": "content-new",
                "raw_source": "test",
            },
            skip_dedup=True,
        )
        repo.update_article_ranking(new_id, rank_score=1.0)

    response = client.get("/api/news?limit=10")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total"] == 2
    assert [article["title"] for article in payload["articles"]] == [
        "Fresh cloud infrastructure earnings recap",
        "Older semiconductor supply chain report",
    ]


def test_news_feed_search_returns_matching_articles(app, client):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_article(
            {
                "canonical_url": "https://example.com/nvidia-story",
                "url_hash": "hash-nvidia",
                "title": "NVIDIA beats earnings expectations",
                "summary": "Chip demand remains strong",
                "source_domain": "reuters.com",
                "published_at": "2025-06-01T12:00:00Z",
                "fetched_at": "2025-06-01T12:05:00Z",
                "content_hash": "content-nv",
                "raw_source": "test",
            }
        )
        repo.upsert_article(
            {
                "canonical_url": "https://example.com/apple-story",
                "url_hash": "hash-apple",
                "title": "Apple launches new hardware",
                "summary": "Consumer product refresh",
                "source_domain": "bbc.co.uk",
                "published_at": "2025-06-02T12:00:00Z",
                "fetched_at": "2025-06-02T12:05:00Z",
                "content_hash": "content-ap",
                "raw_source": "test",
            }
        )

    response = client.get("/api/news?limit=10&q=nvidia")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total"] == 1
    assert len(payload["articles"]) == 1
    assert "NVIDIA" in payload["articles"][0]["title"]


def test_preferences_round_trip(app, client):
    response = client.get("/api/preferences")
    assert response.status_code == 200
    assert response.get_json()["theme"] in {"dark", "light"}
    assert isinstance(response.get_json()["portfolio"], list)

    update_response = client.put(
        "/api/preferences",
        json={"theme": "light", "portfolio": ["AAPL", "MSFT"]},
    )
    assert update_response.status_code == 200
    payload = update_response.get_json()
    assert payload["theme"] == "light"
    assert payload["portfolio"] == ["AAPL", "MSFT"]

    reload_response = client.get("/api/preferences")
    assert reload_response.status_code == 200
    assert reload_response.get_json() == payload


def test_preferences_research_ui_round_trip(client):
    response = client.put(
        "/api/preferences",
        json={"researchColorMode": "historical", "researchHeatLegend": False},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["researchColorMode"] == "historical"
    assert payload["researchHeatLegend"] is False

    bad = client.put("/api/preferences", json={"researchColorMode": "invalid"})
    assert bad.status_code == 400


def test_preferences_research_pinned_round_trip(client):
    response = client.put(
        "/api/preferences",
        json={"researchPinnedTickers": ["aapl", "MSFT", "aapl"]},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["researchPinnedTickers"] == ["AAPL", "MSFT"]

    reload_response = client.get("/api/preferences")
    assert reload_response.status_code == 200
    assert reload_response.get_json()["researchPinnedTickers"] == ["AAPL", "MSFT"]

    too_many = client.put(
        "/api/preferences",
        json={"researchPinnedTickers": [f"T{i}" for i in range(25)]},
    )
    assert too_many.status_code == 400


def test_saved_screens_crud(client):
    screen_id = "test-screen-1"
    payload = {
        "name": "Deep Value",
        "universe": "sp500",
        "filterGroups": [
            {"op": "AND", "filters": [{"metric": "pb", "op": "lt", "value": 0.7}]},
        ],
        "sort": {"metric": "pb", "dir": "asc"},
        "limit": 50,
        "sourcePresetId": "deep_value",
    }

    create_response = client.put(f"/api/screens/{screen_id}", json=payload)
    assert create_response.status_code == 200
    created = create_response.get_json()
    assert created["id"] == screen_id
    assert created["name"] == "Deep Value"
    assert created["filterGroups"][0]["filters"][0]["metric"] == "pb"

    list_response = client.get("/api/screens")
    assert list_response.status_code == 200
    screens = list_response.get_json()["screens"]
    assert any(screen["id"] == screen_id for screen in screens)

    update_response = client.put(
        f"/api/screens/{screen_id}",
        json={**payload, "name": "Deep Value (updated)"},
    )
    assert update_response.status_code == 200
    assert update_response.get_json()["name"] == "Deep Value (updated)"

    delete_response = client.delete(f"/api/screens/{screen_id}")
    assert delete_response.status_code == 200
    assert delete_response.get_json()["deleted"] == screen_id

    missing = client.delete(f"/api/screens/{screen_id}")
    assert missing.status_code == 404


def test_saved_screens_validation(client):
    bad = client.put("/api/screens/bad-screen", json={"name": "No filters"})
    assert bad.status_code == 400

    empty_name = client.put(
        "/api/screens/bad-screen-2",
        json={
            "name": "  ",
            "filterGroups": [{"op": "AND", "filters": [{"metric": "pb", "op": "lt", "value": 1}]}],
        },
    )
    assert empty_name.status_code == 400
