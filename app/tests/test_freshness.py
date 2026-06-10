from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import connect_db, init_db, migrate_schema
from app.repositories import Repository, utc_now_iso


def _iso_days_ago(days: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_freshness_migrations_are_idempotent(tmp_path):
    db_path = tmp_path / "freshness.sqlite3"
    init_db(str(db_path))
    conn = connect_db(str(db_path))
    try:
        migrate_schema(conn)
        migrate_schema(conn)
        fundamentals_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(fundamentals)").fetchall()
        }
        scores_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(company_scores)").fetchall()
        }
        article_cols = {row[1] for row in conn.execute("PRAGMA table_info(articles)").fetchall()}
        embedding_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(article_embedding_vectors)").fetchall()
        }
        prices_cols = {row[1] for row in conn.execute("PRAGMA table_info(prices)").fetchall()}
        assert "source_updated_at" in fundamentals_cols
        assert "scoring_version" in scores_cols
        assert "enrichment_version" in article_cols
        assert "updated_at" in embedding_cols
        assert "fetched_at" in prices_cols
    finally:
        conn.close()


def test_fetch_stale_fundamentals_tickers(app):
    with app.app_context():
        from app.db import get_db

        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "AAPL", "name": "Apple Inc", "cik": "0000320193"}])
        company = repo.get_company_by_ticker("AAPL")
        repo.upsert_fundamentals(
            [
                {
                    "company_id": company["id"],
                    "metric": "revenue",
                    "value": 1000.0,
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
                    "xbrl_concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
                    "source_updated_at": _iso_days_ago(30),
                }
            ]
        )
        repo.conn.execute(
            "UPDATE fundamentals SET updated_at = ? WHERE company_id = ?",
            (_iso_days_ago(30), company["id"]),
        )
        repo.conn.commit()

        stale = repo.fetch_stale_fundamentals_tickers(stale_after_days=7)
        assert "AAPL" in stale

        repo.conn.execute(
            "UPDATE fundamentals SET updated_at = ? WHERE company_id = ?",
            (utc_now_iso(), company["id"]),
        )
        repo.conn.commit()
        assert "AAPL" not in repo.fetch_stale_fundamentals_tickers(stale_after_days=7)


def test_fetch_scores_needing_recompute(app):
    with app.app_context():
        from app.db import get_db
        from app.services.freshness import CURRENT_SCORING_VERSION

        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "MSFT", "name": "Microsoft", "cik": "0000789019"}])
        company = repo.get_company_by_ticker("MSFT")
        repo.upsert_fundamentals(
            [
                {
                    "company_id": company["id"],
                    "metric": "revenue",
                    "value": 2000.0,
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
                    "xbrl_concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
                }
            ]
        )

        needing = repo.fetch_scores_needing_recompute(CURRENT_SCORING_VERSION)
        assert "MSFT" in needing

        repo.upsert_company_scores(
            company["id"],
            [
                {
                    "period_end": "2024-12-31",
                    "dimension": "ARY",
                    "piotroski_f": 7,
                    "scoring_version": CURRENT_SCORING_VERSION,
                }
            ],
        )
        assert "MSFT" not in repo.fetch_scores_needing_recompute(CURRENT_SCORING_VERSION)

        repo.conn.execute(
            "UPDATE company_scores SET scoring_version = 0 WHERE company_id = ?",
            (company["id"],),
        )
        repo.conn.commit()
        assert "MSFT" in repo.fetch_scores_needing_recompute(CURRENT_SCORING_VERSION)


def test_get_article_embedding_hash(app):
    with app.app_context():
        from app.db import get_db

        repo = Repository(get_db())
        repo.conn.execute(
            """
            INSERT INTO articles (
                url_hash, title, canonical_url, content_hash, fetched_at
            ) VALUES ('hash-1', 'Title', 'https://example.com/a', 'body-hash', ?)
            """,
            (utc_now_iso(),),
        )
        article_id = repo.conn.execute("SELECT id FROM articles WHERE url_hash = 'hash-1'").fetchone()[0]
        repo.upsert_article_embedding(
            article_id,
            model="test-model",
            vector=[0.1, 0.2, 0.3],
            content_hash="body-hash",
        )

        assert repo.get_article_embedding_hash(article_id, model="test-model") == "body-hash"
        assert repo.get_article_embedding_hash(article_id, model="missing") is None


def test_pipeline_status_route(app, client):
    with app.app_context():
        from app.db import get_db

        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "NVDA", "name": "NVIDIA", "cik": "0001045810"}])
        company = repo.get_company_by_ticker("NVDA")
        repo.upsert_prices(
            "NVDA",
            [{"date": "2025-06-01", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 10}],
            source="test",
        )
        repo.upsert_fundamentals(
            [
                {
                    "company_id": company["id"],
                    "metric": "revenue",
                    "value": 3000.0,
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
                    "xbrl_concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
                }
            ],
        )

    response = client.get("/api/admin/pipeline-status")
    assert response.status_code == 200
    payload = response.get_json()
    assert "articles" in payload
    assert "freshness" in payload
    assert payload["freshness"]["pricesFetchedAt"] is not None
    assert payload["freshness"]["fundamentalsUpdatedAt"] is not None
    assert payload["versions"]["scoring"] == 1
    assert "stale" in payload
    assert "recentJobRuns" in payload
