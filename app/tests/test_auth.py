from __future__ import annotations

import pytest
from werkzeug.security import generate_password_hash

PASSWORD = "correct horse battery"


@pytest.fixture()
def protected_app(app):
    app.config["APP_PASSWORD_HASH"] = generate_password_hash(PASSWORD)
    return app


@pytest.fixture()
def guest(protected_app):
    return protected_app.test_client()


@pytest.fixture()
def owner(protected_app):
    client = protected_app.test_client()
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return client


def test_auth_disabled_by_default_keeps_local_behavior(client):
    assert client.get("/api/auth/session").get_json() == {
        "authRequired": False,
        "authenticated": True,
        "role": "owner",
    }
    assert client.put("/api/preferences", json={"theme": "dark"}).status_code == 200
    assert client.post("/api/auth/login", json={"password": "x"}).status_code == 400


def test_guest_can_read_but_not_write(guest):
    assert guest.get("/api/auth/session").get_json()["role"] == "guest"
    assert guest.get("/api/preferences").status_code == 200
    response = guest.put("/api/preferences", json={"theme": "dark"})
    assert response.status_code == 401
    assert response.get_json()["authRequired"] is True
    assert guest.put("/api/watchlists/demo", json={"tickers": ["AAPL"]}).status_code == 401
    assert guest.get("/api/watchlists/demo").status_code == 404


def test_owner_can_write(owner):
    session = owner.get("/api/auth/session").get_json()
    assert session == {"authRequired": True, "authenticated": True, "role": "owner"}
    assert owner.put("/api/preferences", json={"theme": "dark"}).status_code == 200


def test_wrong_password_is_rejected(guest):
    response = guest.post("/api/auth/login", json={"password": "nope"})
    assert response.status_code == 401
    assert guest.get("/api/auth/session").get_json()["authenticated"] is False


@pytest.mark.parametrize("body", [None, {}, {"password": ""}, {"password": 123}, ["pw"]])
def test_malformed_login_is_400(guest, body):
    assert guest.post("/api/auth/login", json=body).status_code == 400


def test_login_is_rate_limited(guest):
    for _ in range(5):
        assert guest.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    # Even the right password is refused while blocked.
    assert guest.post("/api/auth/login", json={"password": PASSWORD}).status_code == 429


def test_logout_drops_owner_rights(owner):
    assert owner.post("/api/auth/logout").status_code == 200
    assert owner.put("/api/preferences", json={"theme": "dark"}).status_code == 401


def test_session_cookie_flags(protected_app):
    client = protected_app.test_client()
    response = client.post("/api/auth/login", json={"password": PASSWORD})
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_owner_session_unlocks_admin_routes_from_any_host(protected_app):
    client = protected_app.test_client()
    host = {"Host": "stocks.example.com"}  # the test client scopes cookies to the request host
    assert client.post("/api/auth/login", json={"password": PASSWORD}, headers=host).status_code == 200
    response = client.get("/api/admin/default-feeds", headers=host)
    assert response.status_code == 200


def test_guest_admin_access_still_uses_admin_guard(guest):
    # Loopback-only guard still applies to guests (no ADMIN_API_KEY configured here).
    assert guest.get("/api/admin/default-feeds", environ_base={"REMOTE_ADDR": "203.0.113.9"}).status_code == 403
    assert guest.get("/api/admin/default-feeds").status_code == 200


def test_cross_site_login_is_rejected(guest):
    response = guest.post("/api/auth/login", json={"password": PASSWORD}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_secret_key_is_persisted_and_private(app, tmp_path):
    import os
    import stat

    key_file = tmp_path / ".flask_secret_key"
    assert key_file.exists()
    assert app.config["SECRET_KEY"] == key_file.read_text().strip()
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600


def test_public_freshness_is_readable_by_guests(guest):
    response = guest.get("/api/status/freshness")
    assert response.status_code == 200
    body = response.get_json()
    assert set(body) == {"freshness", "coverage"}
    assert set(body["coverage"]) == {"companiesMissingMetadata"}
