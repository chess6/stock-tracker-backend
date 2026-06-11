"""Composable screening engine — POST /api/research/screen."""

from __future__ import annotations

from app.db import get_db
from app.repositories import Repository
from app.services.fundamentals import FundamentalsService
from app.services.screening import run_composable_screen
from app.services.prices import PricesService


def _seed_aapl_fundamentals(repo: Repository) -> dict:
    repo.upsert_companies(
        [{"ticker": "AAPL", "name": "Apple Inc", "cik": "0000320193", "sector": "Tech", "industry": "Hardware"}]
    )
    company = repo.get_company_by_ticker("AAPL")
    fundamentals = [
        ("revenue", 1000.0),
        ("netinc", 200.0),
        ("assets", 3000.0),
        ("liabilities", 1200.0),
        ("equity", 1800.0),
        ("ncfo", 250.0),
        ("ebit", 300.0),
        ("retearn", 800.0),
        ("sharesbas", 100.0),
        ("gp", 400.0),
        ("assetscurrent", 1000.0),
        ("liabilitiescurrent", 500.0),
        ("workingcapital", 500.0),
        ("receivables", 120.0),
        ("ppnenet", 900.0),
        ("depamor", 60.0),
        ("sgna", 100.0),
        ("debt", 400.0),
        ("cashneq", 300.0),
        ("interestexp", 15.0),
        ("fcf", 180.0),
        ("eps", 2.0),
    ]
    for metric, value in fundamentals:
        repo.upsert_fundamentals(
            [
                {
                    "company_id": company["id"],
                    "metric": metric,
                    "value": value,
                    "unit": "USD",
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-20",
                    "form": "10-K",
                    "accession": "1",
                    "source": "sec_companyfacts",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": metric,
                }
            ]
        )
    repo.upsert_prices(
        "AAPL",
        [{"date": "2025-01-20", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 100}],
        source="test",
    )

    class StubSec:
        def fetch_company_facts(self, cik):
            return {"facts": {}}

        def fetch_submissions(self, cik):
            return {"filings": {"recent": {}}}

    FundamentalsService(repo, StubSec())._refresh_company_scores(company["id"], "AAPL")[0]
    return company


def test_screen_rejects_invalid_spec(app):
    with app.app_context():
        repo = Repository(get_db())
        payload, status, error = run_composable_screen(repo, PricesService(repo), {"filters": "bad"})
        assert status == 400
        assert error


def test_screen_accepts_field_alias_for_filter_metric(app, client):
    with app.app_context():
        _seed_aapl_fundamentals(Repository(get_db()))

    response = client.post(
        "/api/research/screen",
        json={
            "tickers": ["AAPL"],
            "filters": [{"field": "survivability", "op": "gte", "value": 50}],
            "limit": 10,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["results"][0]["ticker"] == "AAPL"


def test_screen_filters_by_score_and_metric(app, client):
    with app.app_context():
        _seed_aapl_fundamentals(Repository(get_db()))

    response = client.post(
        "/api/research/screen",
        json={
            "tickers": ["AAPL"],
            "filters": [
                {"metric": "survivability", "op": "gte", "value": 50},
                {"metric": "pb", "op": "lt", "value": 20},
            ],
            "sort": {"metric": "survivability", "dir": "desc"},
            "limit": 10,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["meta"]["matched"] >= 1
    assert len(payload["results"]) >= 1
    row = payload["results"][0]
    assert row["ticker"] == "AAPL"
    assert len(row["filterEvidence"]) == 2
    assert all(item["passed"] for item in row["filterEvidence"])


def test_screen_returns_evidence_for_failed_filter(app, client):
    with app.app_context():
        _seed_aapl_fundamentals(Repository(get_db()))

    response = client.post(
        "/api/research/screen",
        json={
            "tickers": ["AAPL"],
            "filters": [{"metric": "pb", "op": "lt", "value": 0.01}],
            "limit": 5,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["meta"]["matched"] == 0
    assert payload["results"] == []


def test_screen_insider_buy6m_filter(app, client):
    from datetime import date, timedelta

    today = date.today()
    recent = (today - timedelta(days=10)).isoformat()

    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "BUY1", "name": "Buyer Co", "cik": "0000000001"}])
        company = repo.get_company_by_ticker("BUY1")
        repo.upsert_fundamentals(
            [
                {
                    "company_id": company["id"],
                    "metric": "revenue",
                    "value": 500.0,
                    "unit": "USD",
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "dimension": "ARY",
                    "fiscal_year": 2024,
                    "fiscal_quarter": "FY",
                    "filing_date": "2025-01-01",
                    "form": "10-K",
                    "accession": "1",
                    "source": "test",
                    "taxonomy": "us-gaap",
                    "xbrl_concept": "revenue",
                }
            ]
        )
        repo.upsert_insider_transactions(
            company["id"],
            [
                {
                    "accession": "a1",
                    "filing_date": recent,
                    "transaction_date": recent,
                    "owner_name": "CEO",
                    "owner_title": "CEO",
                    "transaction_code": "P",
                    "transaction_value": 250000.0,
                    "shares": 1000,
                    "price_per_share": 250.0,
                    "security_title": "Common",
                }
            ],
        )

    response = client.post(
        "/api/research/screen",
        json={
            "tickers": ["BUY1"],
            "filters": [{"metric": "buy6m", "op": "gte", "value": 100000}],
            "limit": 5,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["meta"]["matched"] == 1
    assert payload["results"][0]["insider"]["buy6m"] >= 100000


def _seed_minimal_ticker(repo: Repository, ticker: str, *, buy6m: float = 0, cluster_count: int = 0) -> dict:
    repo.upsert_companies([{"ticker": ticker, "name": f"{ticker} Co", "cik": f"000000{ticker[-4:]}"}])
    company = repo.get_company_by_ticker(ticker)
    repo.upsert_fundamentals(
        [
            {
                "company_id": company["id"],
                "metric": "revenue",
                "value": 500.0,
                "unit": "USD",
                "period_end": "2024-12-31",
                "period_type": "annual",
                "dimension": "ARY",
                "fiscal_year": 2024,
                "fiscal_quarter": "FY",
                "filing_date": "2025-01-01",
                "form": "10-K",
                "accession": "1",
                "source": "test",
                "taxonomy": "us-gaap",
                "xbrl_concept": "revenue",
            }
        ]
    )
    if buy6m > 0:
        from datetime import date, timedelta

        recent = (date.today() - timedelta(days=10)).isoformat()
        repo.upsert_insider_transactions(
            company["id"],
            [
                {
                    "accession": f"{ticker}-a1",
                    "filing_date": recent,
                    "transaction_date": recent,
                    "owner_name": "CEO",
                    "owner_title": "CEO",
                    "transaction_code": "P",
                    "transaction_value": buy6m,
                    "shares": 1000,
                    "price_per_share": buy6m / 1000,
                    "security_title": "Common",
                }
            ],
        )
    if cluster_count > 0:
        from datetime import date, timedelta

        today = date.today()
        window_end = today.isoformat()
        records = []
        for i in range(cluster_count):
            window_start = (today - timedelta(days=29 + i)).isoformat()
            records.append(
                {
                    "window_start": window_start,
                    "window_end": window_end,
                    "buy_count": 2,
                    "sell_count": 0,
                    "unique_buyers": 2,
                    "total_buy_value": 50000.0,
                    "total_sell_value": 0.0,
                    "avg_buy_price": 100.0,
                    "intensity_score": 0.5,
                }
            )
        repo.upsert_insider_cluster_analysis(company["id"], records)
    return company


def test_screen_or_filter_group_matches_any(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _seed_minimal_ticker(repo, "OR1", buy6m=600000, cluster_count=0)
        _seed_minimal_ticker(repo, "OR2", buy6m=0, cluster_count=4)
        _seed_minimal_ticker(repo, "OR3", buy6m=1000, cluster_count=0)

    response = client.post(
        "/api/research/screen",
        json={
            "tickers": ["OR1", "OR2", "OR3"],
            "filter_groups": [
                {
                    "op": "OR",
                    "filters": [
                        {"metric": "buy6m", "op": "gte", "value": 500000},
                        {"metric": "cluster_count", "op": "gte", "value": 3},
                    ],
                }
            ],
            "limit": 10,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    matched_tickers = {row["ticker"] for row in payload["results"]}
    assert matched_tickers == {"OR1", "OR2"}
    assert "OR3" not in matched_tickers


def test_screen_and_or_filter_groups_combined(app, client):
    with app.app_context():
        repo = Repository(get_db())
        company = _seed_aapl_fundamentals(repo)
        _seed_minimal_ticker(repo, "OR1", buy6m=600000, cluster_count=0)
        from datetime import date, timedelta

        today = date.today()
        repo.upsert_insider_cluster_analysis(
            company["id"],
            [
                {
                    "window_start": (today - timedelta(days=29 + i)).isoformat(),
                    "window_end": today.isoformat(),
                    "buy_count": 2,
                    "sell_count": 0,
                    "unique_buyers": 2,
                    "total_buy_value": 75000.0,
                    "total_sell_value": 0.0,
                    "avg_buy_price": 100.0,
                    "intensity_score": 0.6,
                }
                for i in range(3)
            ],
        )

    response = client.post(
        "/api/research/screen",
        json={
            "tickers": ["AAPL", "OR1"],
            "filter_groups": [
                {"op": "AND", "filters": [{"metric": "survivability", "op": "gte", "value": 50}]},
                {
                    "op": "OR",
                    "filters": [
                        {"metric": "buy6m", "op": "gte", "value": 500000},
                        {"metric": "cluster_count", "op": "gte", "value": 3},
                    ],
                },
            ],
            "limit": 10,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    matched_tickers = {row["ticker"] for row in payload["results"]}
    assert matched_tickers == {"AAPL"}


def test_screen_flat_filters_backward_compatible_as_and_group(app, client):
    with app.app_context():
        _seed_aapl_fundamentals(Repository(get_db()))

    response = client.post(
        "/api/research/screen",
        json={
            "tickers": ["AAPL"],
            "filters": [
                {"metric": "survivability", "op": "gte", "value": 50},
                {"metric": "pb", "op": "lt", "value": 20},
            ],
            "limit": 10,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["spec"]["filter_groups"] == [
        {
            "op": "AND",
            "filters": [
                {"metric": "survivability", "op": "gte", "value": 50},
                {"metric": "pb", "op": "lt", "value": 20},
            ],
        }
    ]
    assert payload["meta"]["matched"] >= 1


def test_screen_divergence_score_filter(app, client):
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

    response = client.post(
        "/api/research/screen",
        json={
            "tickers": ["AAPL"],
            "filters": [{"metric": "divergence_score", "op": "gte", "value": 0.7}],
            "limit": 5,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["meta"]["matched"] == 1
    row = payload["results"][0]
    evidence = {item["metric"]: item for item in row["filterEvidence"]}
    assert evidence["divergence_score"]["passed"] is True
    assert evidence["divergence_score"]["actual"] == 0.82


def test_screen_divergence_signal_filter(app, client):
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

    response = client.post(
        "/api/research/screen",
        json={
            "tickers": ["AAPL"],
            "filters": [{"metric": "divergence_signal", "op": "eq", "value": "rerating_candidate"}],
            "limit": 5,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["meta"]["matched"] == 1
    evidence = {item["metric"]: item for item in payload["results"][0]["filterEvidence"]}
    assert evidence["divergence_signal"]["passed"] is True
    assert evidence["divergence_signal"]["actual"] == "rerating_candidate"


def test_screen_divergence_filter_excludes_missing_snapshot(app, client):
    with app.app_context():
        _seed_aapl_fundamentals(Repository(get_db()))

    response = client.post(
        "/api/research/screen",
        json={
            "tickers": ["AAPL"],
            "filters": [{"metric": "divergence_score", "op": "gte", "value": 0.7}],
            "limit": 5,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["meta"]["matched"] == 0
    assert payload["results"] == []


def test_screen_rejects_invalid_filter_group_op(app):
    with app.app_context():
        repo = Repository(get_db())
        payload, status, error = run_composable_screen(
            repo,
            PricesService(repo),
            {
                "tickers": ["AAPL"],
                "filter_groups": [{"op": "XOR", "filters": []}],
            },
        )
        assert status == 400
        assert "filter_groups[0].op" in error
        assert payload is None
