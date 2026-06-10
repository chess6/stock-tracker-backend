from app.repositories import Repository
from app.services.price_lookup import map_prices_by_period_end


def test_map_prices_by_period_end_matches_nearest_prior_close():
    rows = [
        {"date": "2023-12-29", "close": 8.0, "source": "stooq"},
        {"date": "2024-12-30", "close": 10.0, "source": "stooq"},
        {"date": "2024-12-31", "close": 10.5, "source": "yfinance"},
    ]
    mapped = map_prices_by_period_end(["2024-12-31", "2024-06-01", "2023-12-31"], rows)
    assert mapped["2024-12-31"] == 10.5
    assert mapped["2024-06-01"] == 8.0
    assert mapped["2023-12-31"] == 8.0


def test_map_prices_by_period_end_prefers_stooq_on_same_date():
    rows = [
        {"date": "2024-12-31", "close": 9.0, "source": "yfinance"},
        {"date": "2024-12-31", "close": 10.0, "source": "stooq"},
    ]
    mapped = map_prices_by_period_end(["2024-12-31"], rows)
    assert mapped["2024-12-31"] == 10.0


def test_fetch_prices_by_period_ends_single_query(app):
    with app.app_context():
        from app.db import get_db

        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "P3T", "name": "P3 Test", "cik": "0000000099"}])
        repo.upsert_prices(
            "P3T",
            [
                {"date": "2022-12-30", "open": 5.0, "high": 5.5, "low": 4.5, "close": 5.0, "volume": 1},
                {"date": "2023-12-29", "open": 7.0, "high": 7.5, "low": 6.5, "close": 7.0, "volume": 1},
                {"date": "2024-12-30", "open": 9.0, "high": 9.5, "low": 8.5, "close": 9.0, "volume": 1},
            ],
            source="test",
        )

        calls = {"history": 0}
        original = repo.fetch_price_history

        def counting_fetch_price_history(ticker, *, through_date=None):
            calls["history"] += 1
            return original(ticker, through_date=through_date)

        repo.fetch_price_history = counting_fetch_price_history  # type: ignore[method-assign]

        mapped = repo.fetch_prices_by_period_ends(
            "P3T",
            ["2024-12-31", "2023-12-31", "2022-12-31"],
        )
        assert calls["history"] == 1
        assert mapped["2024-12-31"] == 9.0
        assert mapped["2023-12-31"] == 7.0
        assert mapped["2022-12-31"] == 5.0
