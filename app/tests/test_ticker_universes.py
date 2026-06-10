"""Tests for curated admin ticker universes."""

from __future__ import annotations

from app.services.ticker_universes import chunk_tickers, get_universe, get_universe_tickers, list_universes


def test_list_universes_includes_sp500():
    universes = list_universes()
    assert any(item["id"] == "sp500" for item in universes)
    sp500 = next(item for item in universes if item["id"] == "sp500")
    assert sp500["count"] >= 500


def test_get_universe_tickers_sorted_unique():
    tickers = get_universe_tickers("sp500")
    assert len(tickers) >= 500
    assert "AAPL" in tickers
    assert tickers == sorted({t.upper() for t in tickers})


def test_get_universe_unknown():
    assert get_universe("not-a-universe") is None
    assert get_universe_tickers("not-a-universe") == []


def test_chunk_tickers():
    assert chunk_tickers(["A", "B", "C", "D", "E"], chunk_size=2) == [["A", "B"], ["C", "D"], ["E"]]
