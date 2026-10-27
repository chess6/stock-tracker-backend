from __future__ import annotations

import pytest


@pytest.fixture()
def no_key_app(app):
    app.config["ADMIN_API_KEY"] = None
    return app


@pytest.fixture()
def keyed_app(app):
    app.config["ADMIN_API_KEY"] = "s3cret"
    return app


def _get(app, *, remote_addr="127.0.0.1", headers=None, query=""):
    return app.test_client().get(
        f"/api/admin/default-feeds{query}",
        headers=headers or {},
        environ_base={"REMOTE_ADDR": remote_addr},
    )


@pytest.mark.parametrize("addr", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_no_key_allows_loopback(no_key_app, addr):
    assert _get(no_key_app, remote_addr=addr).status_code == 200


def test_no_key_allows_local_dev_proxy(no_key_app):
    response = _get(no_key_app, headers={"X-Forwarded-For": "127.0.0.1"})
    assert response.status_code == 200


def test_no_key_rejects_remote_client(no_key_app):
    assert _get(no_key_app, remote_addr="192.168.1.20").status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Forwarded-For": "203.0.113.7"},
        {"X-Forwarded-For": "127.0.0.1, 203.0.113.7"},
        {"X-Real-Ip": "203.0.113.7"},
        {"Forwarded": "for=203.0.113.7"},
    ],
)
def test_no_key_rejects_proxied_remote_client(no_key_app, headers):
    assert _get(no_key_app, headers=headers).status_code == 403


def test_non_admin_routes_unaffected(no_key_app):
    response = no_key_app.test_client().get(
        "/api/preferences", environ_base={"REMOTE_ADDR": "192.168.1.20"}
    )
    assert response.status_code != 403


def test_key_required_even_from_loopback(keyed_app):
    assert _get(keyed_app).status_code == 401
    assert _get(keyed_app, headers={"X-Api-Key": "wrong"}).status_code == 401


def test_key_accepted_from_remote(keyed_app):
    assert _get(keyed_app, remote_addr="203.0.113.7", headers={"X-Api-Key": "s3cret"}).status_code == 200
    assert _get(keyed_app, remote_addr="203.0.113.7", query="?apiKey=s3cret").status_code == 200
