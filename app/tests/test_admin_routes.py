from __future__ import annotations

from app.services.news import DEFAULT_FEEDS


def test_admin_status_and_default_feeds(app, client):
    status_response = client.get("/api/admin/status")
    assert status_response.status_code == 200
    payload = status_response.get_json()
    assert "counts" in payload
    assert "freshness" in payload

    feeds_response = client.get("/api/admin/default-feeds")
    assert feeds_response.status_code == 200
    feeds_payload = feeds_response.get_json()
    assert len(feeds_payload["feeds"]) == len(DEFAULT_FEEDS)
    assert any(feed["name"] == "Reddit r/stocks" for feed in feeds_payload["feeds"])
