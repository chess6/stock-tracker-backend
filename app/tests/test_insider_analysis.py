from __future__ import annotations

from datetime import date, timedelta

from app.services.insider_analysis import (
    analyze_insider_activity,
    build_insider_conviction_alerts,
    compute_intensity_score,
    detect_clusters,
    summarize_window,
)


def _txn(owner: str, code: str, txn_date: str, value: float = 10000.0) -> dict:
    return {
        "owner_name": owner,
        "transaction_code": code,
        "transaction_date": txn_date,
        "transaction_value": value,
        "price_per_share": 10.0,
    }


def test_compute_intensity_score_increases_with_buys():
    low = compute_intensity_score(1, 1000, 30)
    high = compute_intensity_score(5, 1_000_000, 10)
    assert high > low
    assert 0 <= high <= 1


def test_summarize_window_counts_buys_and_sells():
    today = date.today()
    txns = [
        _txn("Alice", "P", today.isoformat(), 50000),
        _txn("Bob", "S", today.isoformat(), 20000),
        _txn("Carol", "P", (today - timedelta(days=120)).isoformat(), 10000),
    ]
    summary = summarize_window(txns, days=90, as_of=today)
    assert summary["buyCount"] == 1
    assert summary["sellCount"] == 1
    assert summary["uniqueBuyers"] == 1
    assert summary["totalBuyValue"] == 50000


def test_detect_clusters_requires_three_unique_buyers():
    today = date.today()
    txns = [
        _txn("Alice", "P", (today - timedelta(days=5)).isoformat()),
        _txn("Bob", "P", (today - timedelta(days=3)).isoformat()),
        _txn("Carol", "P", (today - timedelta(days=1)).isoformat()),
    ]
    clusters = detect_clusters(txns, as_of=today)
    assert len(clusters) >= 1
    assert clusters[0]["uniqueBuyers"] >= 3
    assert clusters[0]["totalBuyValue"] > 0
    assert clusters[0]["isCluster"] is True


def test_detect_clusters_ignores_award_grants_without_dollar_value():
    today = date.today()
    txns = [
        {
            "owner_name": "Alice",
            "transaction_code": "A",
            "transaction_date": (today - timedelta(days=5)).isoformat(),
            "transaction_value": 0.0,
            "price_per_share": 0.0,
        },
        {
            "owner_name": "Bob",
            "transaction_code": "A",
            "transaction_date": (today - timedelta(days=3)).isoformat(),
            "transaction_value": 0.0,
            "price_per_share": 0.0,
        },
        {
            "owner_name": "Carol",
            "transaction_code": "A",
            "transaction_date": (today - timedelta(days=1)).isoformat(),
            "transaction_value": 0.0,
            "price_per_share": 0.0,
        },
    ]
    assert detect_clusters(txns, as_of=today) == []


def test_detect_clusters_ignores_open_market_buys_without_dollar_value():
    today = date.today()
    txns = [
        {
            "owner_name": "Alice",
            "transaction_code": "P",
            "transaction_date": (today - timedelta(days=5)).isoformat(),
            "shares": 1000,
            "price_per_share": None,
            "transaction_value": None,
        },
        {
            "owner_name": "Bob",
            "transaction_code": "P",
            "transaction_date": (today - timedelta(days=3)).isoformat(),
            "shares": 500,
            "price_per_share": None,
            "transaction_value": None,
        },
        {
            "owner_name": "Carol",
            "transaction_code": "P",
            "transaction_date": (today - timedelta(days=1)).isoformat(),
            "shares": 800,
            "price_per_share": None,
            "transaction_value": None,
        },
    ]
    assert detect_clusters(txns, as_of=today) == []


def test_detect_clusters_ignores_sparse_buying():
    today = date.today()
    txns = [
        _txn("Alice", "P", (today - timedelta(days=5)).isoformat()),
        _txn("Bob", "P", (today - timedelta(days=60)).isoformat()),
    ]
    clusters = detect_clusters(txns, as_of=today)
    assert clusters == []


def test_analyze_insider_activity_includes_ratio_windows():
    today = date.today()
    txns = [_txn("Alice", "P", today.isoformat())]
    result = analyze_insider_activity(txns)
    assert result["buyCount90d"] == 1
    assert "90d" in result["ratios"]
    assert "180d" in result["ratios"]
    assert "365d" in result["ratios"]


def test_build_insider_conviction_alerts_filters_by_intensity(app):
    from app.db import get_db
    from app.repositories import Repository

    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "CLUS", "name": "Cluster Co"}])
        company = repo.get_company_by_ticker("CLUS")
        today = date.today().isoformat()
        repo.upsert_insider_cluster_analysis(
            company["id"],
            [
                {
                    "window_start": today,
                    "window_end": today,
                    "buy_count": 4,
                    "sell_count": 0,
                    "unique_buyers": 4,
                    "total_buy_value": 2_500_000.0,
                    "total_sell_value": 0.0,
                    "avg_buy_price": 10.0,
                    "intensity_score": 0.75,
                }
            ],
        )
        payload = build_insider_conviction_alerts(repo, min_intensity=0.3, limit=10)
        tickers = [item["ticker"] for item in payload["alerts"]]
        assert "CLUS" in tickers


def test_insider_alerts_route_disabled_without_flag(app, client):
    response = client.get("/api/research/insider-alerts")
    assert response.status_code == 404
    assert response.get_json()["flag"] == "experimental_insider_alerts"

