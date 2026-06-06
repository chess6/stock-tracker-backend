from __future__ import annotations

from datetime import datetime, timedelta

from app.db import connect_db, init_db
from app.repositories import Repository
from app.services.prices import PricesService


def test_prices_service_reads_from_sqlite(tmp_path):
    db_path = tmp_path / "prices.sqlite3"
    init_db(str(db_path))
    conn = connect_db(str(db_path))
    try:
        repo = Repository(conn)
        today = datetime.utcnow().date()
        repo.upsert_prices(
            "AAPL",
            [
                {
                    "date": (today - timedelta(days=1)).isoformat(),
                    "open": 1,
                    "high": 2,
                    "low": 0.5,
                    "close": 1.5,
                    "volume": 100,
                },
                {
                    "date": today.isoformat(),
                    "open": 1.5,
                    "high": 2.5,
                    "low": 1.4,
                    "close": 2.0,
                    "volume": 120,
                },
            ],
            source="stooq",
        )
        service = PricesService(repo)
        history = service.get_price_history("AAPL", days=30)
        quotes = service.get_quotes(["AAPL"])
        changes = service.get_daily_changes(["AAPL"])
        assert len(history) == 2
        assert quotes["AAPL"]["last"] == 2.0
        assert changes["AAPL"]["todayClose"] == 2.0
        assert changes["AAPL"]["prevClose"] == 1.5
    finally:
        conn.close()
