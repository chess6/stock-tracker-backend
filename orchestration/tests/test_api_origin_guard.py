from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orchestration import api


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(api, "init_db", lambda: None)
    return TestClient(api.app, base_url="http://127.0.0.1:5001")


def test_health_from_localhost(client):
    assert client.get("/health").status_code == 200


def test_rebinding_host_is_rejected(client):
    assert client.get("/health", headers={"Host": "rebind.evil.example:5001"}).status_code == 403


def test_cross_site_approval_is_rejected(client):
    response = client.post("/approvals/1/approve", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_allowed_origin_passes_the_guard(monkeypatch):
    monkeypatch.setattr(api, "init_db", lambda: None)
    # The handler itself needs a database; only the guard's decision matters here.
    client = TestClient(api.app, base_url="http://127.0.0.1:5001", raise_server_exceptions=False)
    response = client.post("/approvals/999999/reject", headers={"Origin": "http://localhost:3000"})
    guard_messages = {"Cross-site request rejected", "Host not allowed"}
    assert not (response.status_code == 403 and response.json().get("detail") in guard_messages)
