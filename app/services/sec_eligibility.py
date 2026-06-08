from __future__ import annotations

import re

# Issuers that file with the SEC but do not publish XBRL CompanyFacts (ETFs, trusts, funds).
_NON_OPERATING_ISSUER_RE = re.compile(
    r"\b("
    r"etf|etn|trust|fund|index|depositary shares|unit investment|"
    r"spdr|ishares|vanguard index|proshares|invesco qqq"
    r")\b",
    re.I,
)

_INDEX_ETF_TICKERS = frozenset(
    {
        "QQQ",
        "SPY",
        "IWM",
        "DIA",
        "VTI",
        "VOO",
        "IVV",
        "ARKK",
        "XLF",
        "XLK",
        "XLE",
        "XLV",
        "XLI",
        "XLP",
        "XLY",
        "XLU",
        "XLRE",
        "XLC",
    }
)


def should_skip_sec_fundamentals(company: dict | None) -> str | None:
    """Return a skip reason when SEC fundamentals are not expected for this issuer."""
    if not company:
        return "no_company"
    ticker = (company.get("ticker") or "").upper()
    if ticker in _INDEX_ETF_TICKERS:
        return "index_etf"
    name = company.get("name") or ""
    if _NON_OPERATING_ISSUER_RE.search(name):
        return "non_operating_issuer"
    return None


def sec_http_outcome(status: int | None) -> str:
    """Classify SEC HTTP status for batch refresh: expected skips vs real errors."""
    if status in {404, 400}:
        return "skip"
    return "error"
