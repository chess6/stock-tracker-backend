from __future__ import annotations


def test_company_tags_get_put_round_trip(client):
    empty = client.get("/api/company-tags")
    assert empty.status_code == 200
    assert empty.get_json()["tickerTags"] == {}
    assert empty.get_json()["source"] == "sqlite"

    put_full = client.put(
        "/api/company-tags",
        json={
            "tickerTags": {
                "aapl": ["Deep Value", "deep value", "Core"],
                "msft": ["Tech"],
            },
        },
    )
    assert put_full.status_code == 200
    payload = put_full.get_json()
    assert payload["tickerTags"] == {
        "AAPL": ["Deep Value", "Core"],
        "MSFT": ["Tech"],
    }

    reload = client.get("/api/company-tags")
    assert reload.get_json()["tickerTags"] == payload["tickerTags"]


def test_company_tags_single_ticker_update(client):
    client.put(
        "/api/company-tags",
        json={"tickerTags": {"AAPL": ["Core"]}},
    )

    update = client.put(
        "/api/company-tags",
        json={"ticker": "AAPL", "tags": ["Watchlist", "Value"]},
    )
    assert update.status_code == 200
    assert update.get_json()["tags"] == ["Watchlist", "Value"]
    assert update.get_json()["tickerTags"]["AAPL"] == ["Watchlist", "Value"]

    clear = client.put(
        "/api/company-tags",
        json={"ticker": "AAPL", "tags": []},
    )
    assert clear.status_code == 200
    assert "AAPL" not in clear.get_json()["tickerTags"]


def test_company_tags_put_validation(client):
    bad = client.put("/api/company-tags", json={"tickerTags": ["invalid"]})
    assert bad.status_code == 400

    missing = client.put("/api/company-tags", json={})
    assert missing.status_code == 400
