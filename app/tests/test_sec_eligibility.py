from __future__ import annotations

from app.services.sec_eligibility import should_skip_sec_fundamentals


def test_should_skip_index_etf_tickers():
    company = {"ticker": "QQQ", "name": "INVESCO QQQ TRUST, SERIES 1", "cik": "0001067839"}
    assert should_skip_sec_fundamentals(company) == "index_etf"


def test_should_skip_trust_by_name():
    company = {"ticker": "XYZ", "name": "Some Capital Trust", "cik": "0001234567"}
    assert should_skip_sec_fundamentals(company) == "non_operating_issuer"


def test_should_not_skip_operating_company():
    company = {"ticker": "AAPL", "name": "Apple Inc.", "cik": "0000320193"}
    assert should_skip_sec_fundamentals(company) is None
