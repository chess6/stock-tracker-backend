"""Tests for Phase 6 base-rate validation harness."""

from __future__ import annotations

from datetime import date, timedelta

from app.db import get_db
from app.repositories import Repository
from app.services.base_rate_validation import validate_gate_base_rates


def _seed_snapshots(repo: Repository, *, composite: str = "deep_value") -> str:
    snap_date = (date.today() - timedelta(days=120)).isoformat()
    repo.upsert_company_rank_snapshots([
        {
            "ticker": "BRAA",
            "composite": composite,
            "snapshot_date": snap_date,
            "composite_score": 0.85,
            "rank_in_universe": 1,
            "factors": [],
        },
        {
            "ticker": "BRBB",
            "composite": composite,
            "snapshot_date": snap_date,
            "composite_score": 0.55,
            "rank_in_universe": 2,
            "factors": [],
        },
    ])
    return snap_date


def test_baserate_route_not_found_without_snapshots(app, client):
    response = client.get("/api/research/rank/baserate?composite=deep_value")
    assert response.status_code == 404


def test_validate_gate_base_rates_insufficient_history(app):
    with app.app_context():
        repo = Repository(get_db())
        payload, status, error = validate_gate_base_rates(repo, composite="deep_value")
        assert payload is None
        assert status == 404
        assert error == "insufficient_history"


def test_validate_gate_base_rates_with_snapshots(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_snapshots(repo)
        payload, status, error = validate_gate_base_rates(
            repo,
            composite="deep_value",
            snapshot_limit=4,
            forward_quarters=4,
        )
        assert error in (None, "insufficient_outcome_data")
        if error is None:
            assert status == 200
            assert "valueTrapHitRate" in payload
            assert "allGatesPassed" in payload["valueTrapHitRate"]
            assert payload["meta"]["composite"] == "deep_value"


def test_baserate_route_with_snapshots(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _seed_snapshots(repo)

    response = client.get("/api/research/rank/baserate?composite=deep_value&snapshot_limit=4")
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        payload = response.get_json()
        assert "valueTrapHitRate" in payload
        assert payload["meta"]["source"] == "sqlite"
