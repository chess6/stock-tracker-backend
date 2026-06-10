"""Composite opportunity ranking — GET /api/research/rank."""

from __future__ import annotations

from datetime import date, timedelta

from app.db import get_db
from app.repositories import Repository
from app.services.composite_ranking import (
    approximate_sector_percentile,
    get_rank_history,
    run_composite_rank,
    snapshot_composite_ranks,
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


def test_rank_route_available_without_feature_flag(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _seed_aapl_fundamentals(repo)

    response = client.get("/api/research/rank?composite=deep_value&tickers=AAPL&limit=5")
    assert response.status_code == 200


def test_rank_deep_value_for_seeded_ticker(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _seed_aapl_fundamentals(repo)

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


def test_snapshot_and_rank_history(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _seed_aapl_fundamentals(repo)

        snapshot = snapshot_composite_ranks(
            repo,
            PricesService(repo),
            composites=["deep_value"],
            universe=None,
        )
        assert snapshot["written"] >= 1

        payload, status, error = get_rank_history(
            repo,
            ticker="AAPL",
            composite="deep_value",
            limit=30,
        )
        assert error is None
        assert status == 200
        assert payload["history"][-1]["composite_score"] is not None

    response = client.get("/api/research/rank/history/AAPL?composite=deep_value&limit=10")
    assert response.status_code == 200
    history = response.get_json()["history"]
    assert len(history) >= 1
    assert history[0]["snapshot_date"]


def test_rank_history_available_without_feature_flag(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _seed_aapl_fundamentals(repo)
        snapshot_composite_ranks(
            repo,
            PricesService(repo),
            composites=["deep_value"],
            universe=None,
        )

    response = client.get("/api/research/rank/history/AAPL?composite=deep_value&limit=10")
    assert response.status_code == 200


def test_deep_value_includes_sentiment_divergence_with_snapshot(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _seed_aapl_fundamentals(repo)
        repo.upsert_company_narrative_snapshots([
            {
                "ticker": "AAPL",
                "snapshot_date": "2026-06-09",
                "states": [{"state": "bankruptcy_fear", "score": 0.8, "articleCount": 2}],
                "divergence_score": 0.82,
                "divergence_signal": "rerating_candidate",
                "emerging_situations": [],
            }
        ])

    response = client.get("/api/research/rank?composite=deep_value&tickers=AAPL&limit=5")
    assert response.status_code == 200
    factors = {item["key"] for item in response.get_json()["results"][0]["factors"]}
    assert "sentiment_divergence" in factors


def test_rank_history_not_found(app, client):
    response = client.get("/api/research/rank/history/ZZZZ?composite=deep_value")
    assert response.status_code == 404


def test_rank_delta_vs_prior_snapshot(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _seed_aapl_fundamentals(repo)
        prior_date = (date.today() - timedelta(days=8)).isoformat()
        repo.upsert_company_rank_snapshots([
            {
                "ticker": "AAPL",
                "composite": "deep_value",
                "snapshot_date": prior_date,
                "composite_score": 0.55,
                "rank_in_universe": 12,
                "factors": [],
            }
        ])

    response = client.get("/api/research/rank?composite=deep_value&tickers=AAPL&limit=5")
    assert response.status_code == 200
    row = response.get_json()["results"][0]
    assert row["rank"] == 1
    assert row["rank_delta"] == 11


def test_rank_delta_null_without_prior_snapshot(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _seed_aapl_fundamentals(repo)

    response = client.get("/api/research/rank?composite=deep_value&tickers=AAPL&limit=5")
    assert response.status_code == 200
    row = response.get_json()["results"][0]
    assert row.get("rank_delta") is None


def test_rerating_candidate_composite_preset(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _seed_aapl_fundamentals(repo)
        repo.upsert_company_narrative_snapshots([
            {
                "ticker": "AAPL",
                "snapshot_date": "2026-06-09",
                "states": [{"state": "bankruptcy_fear", "score": 0.8, "articleCount": 2}],
                "divergence_score": 0.82,
                "divergence_signal": "rerating_candidate",
                "emerging_situations": [],
            }
        ])

    response = client.get("/api/research/rank?composite=rerating_candidate&tickers=AAPL&limit=5")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["meta"]["composite"] == "rerating_candidate"
    assert payload["meta"]["label"] == "Rerating Candidate"
    factors = {item["key"] for item in payload["results"][0]["factors"]}
    assert "sentiment_divergence" in factors
