"""Phase D — narrative state detection, emerging situations, divergence."""

from __future__ import annotations

from datetime import date, timedelta

from app.db import get_db
from app.repositories import Repository
from app.services.event_classification import classify_narrative_states
from app.services.narrative import build_narrative_analysis, clear_narrative_cache
from app.services.narrative_intelligence import (
    aggregate_narrative_states,
    build_emerging_situations,
    build_narrative_intelligence,
    compute_narrative_divergence,
    snapshot_narrative_intelligence,
)


def test_classify_narrative_states_bankruptcy_fear():
    hits = classify_narrative_states("Analysts warn of bankruptcy risk and liquidity crisis at the firm")
    states = {item.state for item in hits}
    assert "bankruptcy_fear" in states or "liquidity_concern" in states


def test_aggregate_narrative_states_counts_articles():
    articles = [
        {
            "title": "Company faces bankruptcy risk amid cash crunch",
            "summary": "Liquidity concern grows",
            "publishedAt": f"{date.today().isoformat()}T12:00:00Z",
        },
        {
            "title": "Turnaround plan shows path to profitability",
            "summary": "Operational turnaround underway",
            "publishedAt": f"{(date.today() - timedelta(days=10)).isoformat()}T12:00:00Z",
        },
    ]
    states = aggregate_narrative_states(articles)
    assert len(states) >= 1
    assert states[0]["articleCount"] >= 1


def test_compute_narrative_divergence_rerating_candidate():
    payload = compute_narrative_divergence(
        sentiment_90d=-0.2,
        margin_trend=0.05,
        survivability=55,
        narrative_states=[{"state": "bankruptcy_fear", "score": 0.8, "articleCount": 2}],
        insider_buy6m=250_000,
    )
    assert payload["signal"] in {"rerating_candidate", "high_conviction"}
    assert payload["divergenceScore"] >= 0.7


def test_build_emerging_situations_insider_news_overlap():
    articles = [
        {
            "title": f"Story {idx}",
            "summary": "News",
            "publishedAt": f"{(date.today() - timedelta(days=idx)).isoformat()}T12:00:00Z",
            "primaryEvent": "earnings",
        }
        for idx in range(4)
    ]
    clusters = [{
        "windowStart": (date.today() - timedelta(days=5)).isoformat(),
        "windowEnd": date.today().isoformat(),
        "total_buy_value": 500_000,
        "buy_count": 3,
    }]
    situations = build_emerging_situations(articles, clusters, margin_trend=0.03, buy6m=500_000)
    assert len(situations) >= 1


def test_narrative_analysis_includes_phase_d_sections(app):
    with app.app_context():
        repo = Repository(get_db())
        clear_narrative_cache()
        repo.upsert_companies([{"ticker": "FEAR", "name": "Fear Co", "cik": "0000000099"}])
        company = repo.get_company_by_ticker("FEAR")
        pub = date.today().isoformat()
        article_id = repo.upsert_article({
            "canonical_url": "https://example.com/fear-story",
            "url_hash": "hash-fear",
            "title": "Fear Co bankruptcy risk rises as liquidity crisis deepens",
            "summary": "Turnaround optimism remains despite distress",
            "published_at": f"{pub}T12:00:00Z",
            "fetched_at": f"{pub}T13:00:00Z",
            "content_hash": "content-fear",
            "raw_source": "test",
            "sentiment_score": -0.35,
        })
        repo.link_article_company(article_id, company["id"], "cashtag", 0.95)

        payload = build_narrative_analysis(repo, "FEAR", use_cache=False)

    assert payload["narrativeStates"]
    assert payload["narrativeDivergence"]["signal"]
    assert isinstance(payload["emergingSituations"], list)


def test_snapshot_narrative_intelligence_persists(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "SNAP", "name": "Snap Narrative", "cik": "0000000100"}])
        company = repo.get_company_by_ticker("SNAP")
        pub = date.today().isoformat()
        article_id = repo.upsert_article({
            "canonical_url": "https://example.com/snap-story",
            "url_hash": "hash-snap",
            "title": "Snap Narrative cyclical recovery and margin stabilization",
            "summary": "AI optimism builds",
            "published_at": f"{pub}T12:00:00Z",
            "fetched_at": f"{pub}T13:00:00Z",
            "content_hash": "content-snap",
            "raw_source": "test",
            "sentiment_score": 0.2,
        })
        repo.link_article_company(article_id, company["id"], "cashtag", 0.95)

        result = snapshot_narrative_intelligence(repo, ["SNAP"])
        assert result["written"] == 1
        snapshots = repo.fetch_latest_narrative_snapshots(["SNAP"])
        assert "SNAP" in snapshots
        assert snapshots["SNAP"]["divergence_score"] is not None

        intel = build_narrative_intelligence(repo, "SNAP", repo.fetch_narrative_articles_for_ticker("SNAP"))
        assert intel["narrativeDivergence"]["divergenceScore"] is not None
