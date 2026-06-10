from app.repositories import Repository


def _plan_text(conn, sql: str, params: list) -> str:
    rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    return "\n".join(str(dict(row)) for row in rows)


def test_company_scores_latest_lookup_uses_company_period_index(app):
    with app.app_context():
        from app.db import get_db

        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "QP1", "name": "Query Plan 1", "cik": "0000000100"}])
        company = repo.get_company_by_ticker("QP1")
        repo.upsert_company_scores(
            company["id"],
            [
                {
                    "period_end": "2024-12-31",
                    "dimension": "ARY",
                    "piotroski_f": 5,
                    "altman_z": 2.0,
                    "beneish_m": -2.0,
                    "survivability": 0.8,
                }
            ],
        )

        placeholders = "?"
        sql = f"""
            SELECT
                c.ticker,
                cs.period_end,
                cs.dimension,
                cs.piotroski_f,
                cs.altman_z,
                cs.beneish_m,
                cs.survivability,
                cs.computed_at
            FROM company_scores cs
            JOIN companies c ON c.id = cs.company_id
            WHERE c.ticker IN ({placeholders})
              AND cs.dimension = ?
              AND cs.period_end = (
                  SELECT MAX(cs2.period_end)
                  FROM company_scores cs2
                  WHERE cs2.company_id = cs.company_id AND cs2.dimension = cs.dimension
              )
        """
        plan = _plan_text(repo.conn, sql, ["QP1", "ARY"])
        assert "idx_company_scores_company_period" in plan


def test_price_history_lookup_uses_ticker_date_index(app):
    with app.app_context():
        from app.db import get_db

        repo = Repository(get_db())
        repo.upsert_prices(
            "QP2",
            [{"date": "2024-12-31", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}],
            source="test",
        )

        sql = """
            SELECT ticker, date, open, high, low, close, volume, source
            FROM prices
            WHERE ticker = ? AND date <= ?
            ORDER BY date ASC
        """
        plan = _plan_text(repo.conn, sql, ["QP2", "2024-12-31"])
        assert "idx_prices_ticker_date" in plan
