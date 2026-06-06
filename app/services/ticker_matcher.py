from __future__ import annotations

import re

# Tickers that collide with common English words in headlines.
_AMBIGUOUS_TICKERS = frozenset({
    "A", "AN", "AS", "AT", "BE", "BY", "DO", "FOR", "GO", "HAS", "HE", "IF", "IN", "IS", "IT",
    "JOB", "LOT", "ON", "OR", "OUT", "S", "SO", "TOP", "UP", "US", "W", "WE", "ALL", "ARE",
    "YOU", "HAS", "WAY", "BILL", "PAY", "FOUR", "NICE", "SITE", "POST", "MOVE", "NOTE", "BAR",
    "BACK", "CARD", "CARE", "HELP", "FUND", "FIVE", "TECH", "EU", "OC", "AI", "NOW", "WELL",
    "TAP", "SN", "RNG", "ROCK", "DQ", "SAM", "EW", "PM", "TK", "SLG", "AZN", "NHC", "STRG",
})


def _upper_padded(text: str) -> str:
    return f" {text.upper()} "


def ticker_match_patterns(ticker: str) -> list[re.Pattern]:
    escaped = re.escape(ticker.upper())
    patterns = [
        re.compile(rf"\${escaped}\b"),
        re.compile(rf"\({escaped}:"),
        re.compile(rf"\b{escaped}\b"),
    ]
    if len(ticker) >= 4:
        patterns.append(re.compile(rf"(?<![A-Z0-9]){escaped}(?![A-Z0-9])"))
    return patterns


def matches_ticker_symbol(ticker: str, text: str) -> bool:
    symbol = (ticker or "").upper().strip()
    if not symbol or len(symbol) < 2:
        return False
    if symbol in _AMBIGUOUS_TICKERS:
        return bool(re.search(rf"\${re.escape(symbol)}\b|\({re.escape(symbol)}:", text, re.I))
    haystack = _upper_padded(text)
    for pattern in ticker_match_patterns(symbol):
        if pattern.search(haystack) or pattern.search(text):
            return True
    return False


def match_tickers_in_text(text: str, companies: list[dict], *, max_matches: int = 6) -> list[tuple[int, str, float]]:
    matches: list[tuple[int, str, float]] = []
    seen: set[int] = set()
    for company in companies:
        ticker = (company.get("ticker") or "").upper()
        if not ticker or company["id"] in seen:
            continue
        if matches_ticker_symbol(ticker, text):
            matches.append((company["id"], "ticker", 0.95))
            seen.add(company["id"])
            if len(matches) >= max_matches:
                break
    return matches
