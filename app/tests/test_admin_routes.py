from __future__ import annotations

from app.services.news import DEFAULT_FEEDS


def test_admin_universes_and_enqueue_sp500_insiders(app, client):
    list_response = client.get("/api/admin/universes")
    assert list_response.status_code == 200
    universes = list_response.get_json()["universes"]
    assert any(item["id"] == "sp500" for item in universes)

    detail_response = client.get("/api/admin/universes/sp500")
    assert detail_response.status_code == 200
    detail = detail_response.get_json()
    assert detail["id"] == "sp500"
    assert len(detail["tickers"]) >= 500
    assert "AAPL" in detail["tickers"]

    enqueue_response = client.post("/api/admin/enqueue-universe-insiders?universe=sp500")
    assert enqueue_response.status_code == 200
    payload = enqueue_response.get_json()
    assert payload["universe"] == "sp500"
    assert payload["totalTickers"] >= 500
    assert payload["chunks"] >= 5
    assert len(payload["jobs"]) == payload["chunks"]


def test_admin_status_and_default_feeds(app, client):
    status_response = client.get("/api/admin/status")
    assert status_response.status_code == 200
    payload = status_response.get_json()
    assert "counts" in payload
    assert "freshness" in payload
    assert "coverage" in payload
    assert "companyScoresUpdatedAt" in payload["freshness"]
    assert "companiesMissingMetadata" in payload["coverage"]
    assert "jobs" in payload
    assert "feeds" in payload

    feeds_response = client.get("/api/admin/default-feeds")
    assert feeds_response.status_code == 200
    feeds_payload = feeds_response.get_json()
    assert len(feeds_payload["feeds"]) == len(DEFAULT_FEEDS)
    assert any(feed["name"] == "Reddit r/stocks" for feed in feeds_payload["feeds"])
