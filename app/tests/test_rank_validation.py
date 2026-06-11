"""Composite rank validation — GET /api/research/rank/validation."""

from __future__ import annotations

from datetime import date, timedelta

from app.db import get_db
from app.repositories import Repository
from app.services.rank_validation import validate_composite_rank


def _seed_rank_snapshots(repo: Repository, *, composite: str = "deep_value") -> str:
    snap_date = (date.today() - timedelta(days=60)).isoformat()
    repo.upsert_company_rank_snapshots([
        {
            "ticker": "AAA",
            "composite": composite,
            "snapshot_date": snap_date,
            "composite_score": 0.9,
            "rank_in_universe": 1,
            "factors": [],
        },
        {
            "ticker": "ZZZ",
            "composite": composite,
            "snapshot_date": snap_date,
            "composite_score": 0.1,
            "rank_in_universe": 20,
            "factors": [],
        },
    ])
    return snap_date


def test_rank_validation_route_not_found_without_snapshots(app, client):
    response = client.get("/api/research/rank/validation?composite=deep_value")
    assert response.status_code == 404


def test_rank_validation_rejects_unknown_composite(app, client):
    response = client.get("/api/research/rank/validation?composite=unknown_xyz")
    assert response.status_code == 400


def test_validate_composite_rank_insufficient_history(app):
    with app.app_context():
        repo = Repository(get_db())
        payload, status, error = validate_composite_rank(repo, composite="deep_value")
        assert payload is None
        assert status == 404
        assert error == "insufficient_history"


def test_validate_composite_rank_with_snapshots(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_rank_snapshots(repo)
        payload, status, error = validate_composite_rank(
            repo,
            composite="deep_value",
            horizons=(30,),
            snapshot_limit=4,
        )
        assert error in (None, "insufficient_price_history")
        if error is None:
            assert status == 200
            assert payload["meta"]["composite"] == "deep_value"
            assert "30" in payload["horizons"]
