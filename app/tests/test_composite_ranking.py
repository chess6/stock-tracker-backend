"""Composite opportunity ranking — GET /api/research/rank."""

from __future__ import annotations

from app.db import get_db
from app.repositories import Repository
from app.services.composite_ranking import (
    approximate_sector_percentile,
    run_composite_rank,
)
from app.services.prices import PricesService
from app.tests.test_screening import _seed_aapl_fundamentals


def test_approximate_sector_percentile_interpolates_and_inverts():
    breakpoints = {
        "count": 10,
        "min": 0.0,
        "p20": 2.0,
        "p40": 4.0,
        "p60": 6.0,
        "p80": 8.0,
        "p95": 9.5,
        "max": 10.0,
    }
    assert approximate_sector_percentile(5.0, breakpoints) == 0.5
    assert approximate_sector_percentile(5.0, breakpoints, invert=True) == 0.5
    assert approximate_sector_percentile(0.0, breakpoints, invert=True) == 1.0


def test_rank_route_disabled_by_default(app, client):
    response = client.get("/api/research/rank?composite=deep_value&tickers=AAPL")
    assert response.status_code == 403
    assert response.get_json()["featureFlag"] == "experimental_research_composite_rank"


def test_rank_deep_value_for_seeded_ticker(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _seed_aapl_fundamentals(repo)
        repo.set_config("experimental_research_composite_rank", True)

    response = client.get("/api/research/rank?composite=deep_value&tickers=AAPL&limit=5")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["meta"]["composite"] == "deep_value"
    assert payload["meta"]["returned"] == 1
    row = payload["results"][0]
    assert row["ticker"] == "AAPL"
    assert row["rank"] == 1
    assert 0 <= row["compositeScore"] <= 1
    assert row["factorsPresent"] >= 1
    assert any(f["key"] == "survivability" for f in row["factors"])


def test_rank_rejects_unknown_composite(app, client):
    with app.app_context():
        repo = Repository(get_db())
        repo.set_config("experimental_research_composite_rank", True)

    response = client.get("/api/research/rank?composite=unknown_xyz&tickers=AAPL")
    assert response.status_code == 400
    assert "Unknown composite" in response.get_json()["error"]


def test_run_composite_rank_service(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_aapl_fundamentals(repo)
        payload, status, error = run_composite_rank(
            repo,
            PricesService(repo),
            composite="turnaround",
            tickers=["AAPL"],
            limit=10,
        )
        assert error is None
        assert status == 200
        assert payload["results"][0]["ticker"] == "AAPL"
