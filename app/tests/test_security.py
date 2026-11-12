from __future__ import annotations

import pytest
import requests

from app.services import safe_fetch
from app.services.news import parse_feed, strip_html
from app.services.safe_fetch import (
    ResponseTooLargeError,
    UnexpectedContentTypeError,
    UnsafeURLError,
    is_http_url,
    safe_get,
    validate_public_url,
)

EVIL = "https://evil.example"
LOCAL_UI = "http://localhost:3000"


# --- CORS and cross-site request rejection -------------------------------------------


def test_cors_only_allows_configured_origins(client):
    allowed = client.options("/api/preferences", headers={"Origin": LOCAL_UI, "Access-Control-Request-Method": "PUT"})
    assert allowed.headers.get("Access-Control-Allow-Origin") == LOCAL_UI
    denied = client.options("/api/preferences", headers={"Origin": EVIL, "Access-Control-Request-Method": "PUT"})
    assert "Access-Control-Allow-Origin" not in denied.headers


def test_cross_site_writes_are_rejected(client):
    response = client.put("/api/preferences", json={"theme": "dark"}, headers={"Origin": EVIL})
    assert response.status_code == 403
    assert client.put("/api/preferences", json={"theme": "dark"}, headers={"Origin": LOCAL_UI}).status_code == 200
    # Non-browser clients (no Origin header) are unaffected.
    assert client.put("/api/preferences", json={"theme": "light"}).status_code == 200


def test_cross_site_admin_requests_are_rejected_even_for_get(client):
    response = client.get("/api/admin/default-feeds", headers={"Origin": EVIL})
    assert response.status_code == 403
    assert client.get("/api/admin/default-feeds", headers={"Origin": LOCAL_UI}).status_code == 200


def test_opaque_null_origin_is_rejected(client):
    assert client.post("/api/admin/config", json={}, headers={"Origin": "null"}).status_code == 403


def test_admin_rejects_non_local_host_without_key(client):
    """DNS rebinding: attacker hostname resolving to 127.0.0.1 must not reach admin routes."""
    response = client.get("/api/admin/default-feeds", headers={"Host": "rebind.evil.example:5000"})
    assert response.status_code == 403
    assert client.get("/api/admin/default-feeds", headers={"Host": "localhost:5000"}).status_code == 200


def test_admin_host_check_skipped_when_key_configured(app):
    app.config["ADMIN_API_KEY"] = "s3cret"
    response = app.test_client().get(
        "/api/admin/default-feeds",
        headers={"Host": "api.example.com", "X-Api-Key": "s3cret"},
    )
    assert response.status_code == 200


# --- Input validation -----------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/news?limit=abc",
        "/api/news?offset=1.5",
        "/api/news/clusters?hours=soon",
        "/api/research/insiders/clusters?min_buy_value=nan",
    ],
)
def test_bad_numeric_params_return_400(client, path):
    assert client.get(path).status_code == 400


def test_out_of_range_numeric_params_are_clamped(client):
    assert client.get("/api/news/clusters?hours=99999999999999").status_code == 200
    assert client.get("/api/tickers/movers?limit=-1").status_code == 200


def test_watchlist_rejects_string_tickers_without_writing(client):
    response = client.put("/api/watchlists/demo", json={"tickers": "AAPL"})
    assert response.status_code == 400
    assert client.get("/api/watchlists/demo").status_code == 404


def test_watchlist_normalizes_tickers(client):
    response = client.put("/api/watchlists/demo", json={"tickers": [" msft ", "aapl", "MSFT", ""]})
    assert response.status_code == 200
    assert sorted(response.get_json()["tickers"]) == ["AAPL", "MSFT"]


@pytest.mark.parametrize("payload", [{"ticker": 5}, {"ticker": "<script>"}])
def test_add_watchlist_ticker_validates(client, payload):
    client.put("/api/watchlists/demo", json={"tickers": []})
    assert client.post("/api/watchlists/demo/tickers", json=payload).status_code == 400


def test_non_object_json_body_is_400(client):
    assert client.put("/api/preferences", json=["dark"]).status_code == 400
    assert client.put("/api/watchlists/demo", json=["AAPL"]).status_code == 400


# --- Outbound fetch guard (SSRF) ------------------------------------------------------


class _Resp:
    def __init__(self, status=200, headers=None, body=b"<html>ok</html>"):
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self._body = body
        self.encoding = "utf-8"

    def iter_content(self, chunk_size=1):
        yield self._body

    def close(self):
        pass


class _Session:
    def __init__(self, responses):
        self.responses = dict(responses)
        self.requested = []

    def get(self, url, **_kwargs):
        self.requested.append(url)
        return self.responses[url]


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "data:text/html,hi", "file:///etc/passwd", "ftp://example.com/x", "//example.com"],
)
def test_only_http_urls(url):
    assert not is_http_url(url)
    with pytest.raises(UnsafeURLError):
        validate_public_url(url)


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.1.2.3", "192.168.0.10", "172.16.5.4", "169.254.169.254", "::1", "fd00::1", "0.0.0.0"],
)
def test_internal_addresses_are_blocked(monkeypatch, address):
    monkeypatch.setattr(safe_fetch, "_resolve_host", lambda host: [address])
    with pytest.raises(UnsafeURLError):
        validate_public_url("http://internal.example/")


def test_literal_ip_urls_are_checked_without_dns():
    with pytest.raises(UnsafeURLError):
        validate_public_url("http://169.254.169.254/latest/meta-data/")


def test_redirect_to_internal_address_is_blocked(monkeypatch):
    monkeypatch.setattr(
        safe_fetch,
        "_resolve_host",
        lambda host: ["127.0.0.1"] if host == "internal.example" else ["93.184.216.34"],
    )
    session = _Session(
        {
            "https://news.example/story": _Resp(302, {"Location": "http://internal.example/api/admin/status"}),
        }
    )
    with pytest.raises(UnsafeURLError):
        safe_get(session, "https://news.example/story")
    assert session.requested == ["https://news.example/story"]


def test_redirects_are_followed_when_public():
    session = _Session(
        {
            "https://news.example/a": _Resp(301, {"Location": "/b"}),
            "https://news.example/b": _Resp(200),
        }
    )
    response = safe_get(session, "https://news.example/a", allowed_content_types=safe_fetch.HTML_CONTENT_TYPES)
    assert response.ok and response.text == "<html>ok</html>"
    assert response.url == "https://news.example/b"


def test_non_html_article_bodies_are_rejected():
    session = _Session({"https://news.example/a": _Resp(200, {"Content-Type": "application/json"}, b'{"key":"x"}')})
    with pytest.raises(UnexpectedContentTypeError):
        safe_get(session, "https://news.example/a", allowed_content_types=safe_fetch.HTML_CONTENT_TYPES)


def test_response_size_is_capped():
    session = _Session({"https://news.example/a": _Resp(200, body=b"x" * 2048)})
    with pytest.raises(ResponseTooLargeError):
        safe_get(session, "https://news.example/a", max_bytes=1024)


def test_safe_fetch_errors_are_request_exceptions():
    """Existing `except requests.RequestException` handlers keep working."""
    assert issubclass(UnsafeURLError, requests.RequestException)


# --- Feed sanitization ----------------------------------------------------------------


def test_parse_feed_drops_non_http_links_and_strips_title_markup():
    xml = """<rss><channel>
      <item><title>Safe &lt;b&gt;story&lt;/b&gt;</title><link>https://example.com/ok</link></item>
      <item><title>Bad link</title><link>javascript:alert(document.domain)</link></item>
      <item><title>&lt;img src=x onerror=alert(1)&gt;</title><link>https://example.com/img</link></item>
    </channel></rss>"""
    items = parse_feed(xml)
    assert [item["link"] for item in items] == ["https://example.com/ok"]
    assert items[0]["title"] == "Safe story"


def test_strip_html_removes_entity_encoded_markup():
    assert "<" not in strip_html("Hello &lt;img src=x onerror=alert(1)&gt; world")
    assert strip_html("AT&T beats estimates") == "AT&T beats estimates"
