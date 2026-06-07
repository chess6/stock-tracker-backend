from __future__ import annotations

from app.services.ticker_matcher import match_tickers_in_text, matches_ticker_symbol


def test_matches_dollar_prefixed_ambiguous_ticker():
    assert matches_ticker_symbol("ON", "Traders bought $ON after the upgrade")


def test_rejects_ambiguous_word_without_prefix():
    assert not matches_ticker_symbol("FOR", "This is good for investors")


def test_matches_four_letter_ticker_in_headline():
    assert matches_ticker_symbol("AAPL", "Apple Inc (AAPL) reported earnings")


def test_match_tickers_in_text_limits_results():
    companies = [
        {"id": 1, "ticker": "AAPL", "name": "Apple Inc"},
        {"id": 2, "ticker": "MSFT", "name": "Microsoft"},
        {"id": 3, "ticker": "FOR", "name": "Forestar Group"},
    ]
    matches = match_tickers_in_text("AAPL and MSFT rallied today", companies, max_matches=2)
    assert len(matches) == 2
    assert matches[0][1] in {"ticker_symbol", "cashtag", "headline_ticker"}


def test_title_caps_ticker_upgrades_to_headline_strategy():
    companies = [{"id": 1, "ticker": "NFLX", "name": "Netflix Inc"}]
    from app.services.ticker_matcher import match_ticker_signals

    matches = match_ticker_signals(
        "NFLX IS THE MOST UNDERRATED MONEY PRINTER ON THE MARKET",
        companies,
    )
    assert len(matches) == 1
    assert matches[0].match_strategy == "headline_ticker"


def test_mixed_case_words_are_not_ticker_candidates():
    companies = [
        {"id": 1, "ticker": "AD", "name": "Array Digital"},
        {"id": 2, "ticker": "BULL", "name": "Pacer Funds"},
    ]
    from app.services.ticker_matcher import match_ticker_signals

    matches = match_ticker_signals(
        "Ad revenue doubled. Bulls who understand are loading up.",
        companies,
    )
    assert matches == []
