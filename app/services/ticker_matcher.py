from __future__ import annotations

import functools
import re
from dataclasses import dataclass

# Tickers that collide with common English words in headlines.
_AMBIGUOUS_TICKERS = frozenset({
    "A", "AN", "AS", "AT", "BE", "BY", "DO", "FOR", "GO", "HAS", "HE", "IF", "IN", "IS", "IT",
    "JOB", "LOT", "ON", "OR", "OUT", "S", "SO", "TOP", "UP", "US", "W", "WE", "ALL", "ARE",
    "YOU", "HAS", "WAY", "BILL", "PAY", "FOUR", "NICE", "SITE", "POST", "MOVE", "NOTE", "BAR",
    "BACK", "CARD", "CARE", "HELP", "FUND", "FIVE", "TECH", "EU", "OC", "AI", "NOW", "WELL",
    "TAP", "SN", "RNG", "ROCK", "DQ", "SAM", "EW", "PM", "TK", "SLG", "AZN", "NHC", "STRG",
    # Common finance/meme prose false positives (require $TICKER, headline form, or finance context).
    "AD", "AMP", "BR", "BULL", "CASH", "REAL", "PLAY", "GAME", "GIFT", "LINK", "HERE", "CAN",
    "VS", "FCF", "AGM", "AREN", "TD", "LOW", "BIT", "FUN", "SUN", "WAR", "OPEN", "CLOSE", "RUN",
    "GOT", "HIT", "KEY", "NEW", "OLD", "BIG", "BOX", "CAR", "DAY", "EAT", "EGO", "END", "FAT",
    "FED", "FIX", "FLY", "GAS", "GOT", "GUN", "HOT", "ICE", "JOB", "LAW", "LEG", "MAP", "MOM",
    "NET", "ODD", "OFF", "OIL", "OWN", "PEN", "PIE", "POP", "POW", "PRO", "PUB", "RAW", "RED",
    "RIG", "ROW", "RUM", "SAY", "SEA", "SET", "SIT", "SKY", "TAX", "TIE", "TOO", "TOY", "TRY",
    "TWO", "VIA", "WIN", "YES", "ZOO",
})

STRICT_TICKER_MAX_LEN = 4
TITLE_LEAD_CHARS = 250


@dataclass
class TickerSignal:
    company_id: int
    match_strategy: str
    confidence: float
    evidence_text: str | None = None


def _upper_padded(text: str) -> str:
    return f" {text.upper()} "


@functools.lru_cache(maxsize=16384)
def ticker_match_patterns(ticker: str) -> tuple[re.Pattern, ...]:
    escaped = re.escape(ticker.upper())
    patterns = [
        re.compile(rf"\${escaped}\b"),
        re.compile(rf"\({escaped}:"),
        re.compile(rf"\b{escaped}\b"),
    ]
    if len(ticker) >= 4:
        patterns.append(re.compile(rf"(?<![A-Z0-9]){escaped}(?![A-Z0-9])"))
    return tuple(patterns)


def matches_ticker_symbol(ticker: str, text: str, *, _haystack: str | None = None) -> bool:
    symbol = (ticker or "").upper().strip()
    if not symbol or len(symbol) < 2:
        return False
    if symbol in _AMBIGUOUS_TICKERS:
        return bool(re.search(rf"\${re.escape(symbol)}\b|\({re.escape(symbol)}:", text, re.I))
    haystack = _haystack or _upper_padded(text)
    for pattern in ticker_match_patterns(symbol):
        if pattern.search(haystack) or pattern.search(text):
            return True
    return False


def _upgrade_title_caps_strategy(
    ticker: str,
    text: str,
    strategy: str,
    confidence: float,
    found: re.Match,
) -> tuple[str, float]:
    symbol = ticker.upper()
    if strategy != "ticker_symbol" or found.start() >= TITLE_LEAD_CHARS:
        return strategy, confidence
    lead_caps = set(re.findall(r"\b([A-Z]{2,6})\b", text[:TITLE_LEAD_CHARS]))
    if symbol in lead_caps:
        return "headline_ticker", 0.96
    return strategy, confidence


def _best_ticker_signal(ticker: str, text: str, *, haystack: str) -> TickerSignal | None:
    symbol = ticker.upper()
    strategy_order = (
        ("cashtag", 0.98, 0),
        ("headline_ticker", 0.96, 1),
        ("ticker_symbol", 0.95, 2),
    )
    patterns = ticker_match_patterns(symbol)
    for strategy, confidence, pattern_index in strategy_order:
        pattern = patterns[pattern_index]
        found = pattern.search(text) or pattern.search(haystack)
        if found:
            strategy, confidence = _upgrade_title_caps_strategy(ticker, text, strategy, confidence, found)
            return TickerSignal(
                company_id=-1,
                match_strategy=strategy,
                confidence=confidence,
                evidence_text=text[max(0, found.start() - 20) : found.end() + 20].strip(),
            )
    if len(symbol) >= 4 and len(patterns) > 3:
        found = patterns[3].search(text) or patterns[3].search(haystack)
        if found:
            strategy, confidence = _upgrade_title_caps_strategy(
                ticker, text, "ticker_symbol", 0.95, found
            )
            return TickerSignal(
                company_id=-1,
                match_strategy=strategy,
                confidence=confidence,
                evidence_text=text[max(0, found.start() - 20) : found.end() + 20].strip(),
            )
    return None


def match_ticker_signals(text: str, companies: list[dict], *, max_matches: int = 8) -> list[TickerSignal]:
    matches: list[TickerSignal] = []
    seen: set[int] = set()
    haystack = _upper_padded(text)
    ticker_to_company: dict[str, dict] = {}
    for company in companies:
        ticker = (company.get("ticker") or "").upper()
        if ticker:
            ticker_to_company[ticker] = company

    all_caps_words = set(re.findall(r"\b([A-Z]{2,6})\b", text))
    cashtags = {ticker.upper() for ticker in re.findall(r"\$([A-Za-z]{2,6})", text)}
    headline_tickers = {ticker.upper() for ticker in re.findall(r"\(([A-Za-z]{1,6}):", text)}
    candidate_tickers = all_caps_words | cashtags | headline_tickers

    for ticker in candidate_tickers:
        company = ticker_to_company.get(ticker)
        if not company:
            continue
        company_id = company["id"]
        if company_id in seen:
            continue
        if not matches_ticker_symbol(ticker, text, _haystack=haystack):
            continue
        signal = _best_ticker_signal(ticker, text, haystack=haystack)
        if signal is None:
            continue
        matches.append(
            TickerSignal(
                company_id=company_id,
                match_strategy=signal.match_strategy,
                confidence=signal.confidence,
                evidence_text=signal.evidence_text,
            )
        )
        seen.add(company_id)
        if len(matches) >= max_matches:
            break
    return matches


def match_tickers_in_text(text: str, companies: list[dict], *, max_matches: int = 6) -> list[tuple[int, str, float]]:
    """Backward-compatible wrapper."""
    return [
        (signal.company_id, signal.match_strategy, signal.confidence)
        for signal in match_ticker_signals(text, companies, max_matches=max_matches)
    ]
