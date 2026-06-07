from __future__ import annotations

import re

# Curated aliases for major names / rebrands / common press shorthand.
CURATED_ALIASES: dict[str, list[str]] = {
    "GOOGL": ["google", "alphabet"],
    "GOOG": ["google", "alphabet"],
    "META": ["facebook", "meta platforms"],
    "NVDA": ["nvidia"],
    "AAPL": ["apple"],
    "MSFT": ["microsoft"],
    "AMZN": ["amazon"],
    "TSLA": ["tesla"],
    "BRK.A": ["berkshire hathaway", "berkshire"],
    "BRK.B": ["berkshire hathaway", "berkshire"],
    "JPM": ["jpmorgan", "jp morgan", "chase"],
    "BAC": ["bank of america", "bofa"],
    "WMT": ["walmart"],
    "DIS": ["disney"],
    "NFLX": ["netflix"],
    "AMD": ["advanced micro devices"],
    "INTC": ["intel"],
    "COST": ["costco"],
    "KO": ["coca cola", "coca-cola"],
    "PEP": ["pepsico", "pepsi"],
    "XOM": ["exxon", "exxonmobil"],
    "CVX": ["chevron"],
    "IBM": ["international business machines"],
    "ORCL": ["oracle"],
    "CRM": ["salesforce"],
    "UBER": ["uber"],
    "ABNB": ["airbnb"],
    "PYPL": ["paypal"],
    "SQ": ["block", "square"],
    "SHOP": ["shopify"],
    "SNAP": ["snapchat", "snap inc"],
    "PINS": ["pinterest"],
    "AI": ["c3.ai", "c3 ai"],
}

_SUFFIX_RE = re.compile(
    r"\b(incorporated|inc|corp|corporation|ltd|limited|llc|plc|co|company|group|holdings)\b\.?",
    re.I,
)


def normalize_entity_text(text: str) -> str:
    lowered = (text or "").lower().strip()
    lowered = re.sub(r"'s\b", " ", lowered)
    lowered = _SUFFIX_RE.sub(" ", lowered)
    lowered = re.sub(r"[^a-z0-9&.\- ]+", " ", lowered)
    return " ".join(lowered.split())


def aliases_from_company_name(name: str) -> list[str]:
    if not name:
        return []
    normalized = normalize_entity_text(name)
    if not normalized:
        return []
    aliases = [normalized]
    tokens = normalized.split()
    if len(tokens) >= 2:
        aliases.append(tokens[0])
        if tokens[0] not in {"the", "a"}:
            aliases.append(" ".join(tokens[:2]))
    return list(dict.fromkeys(alias for alias in aliases if len(alias) >= 2))


def build_alias_records(companies: list[dict]) -> list[dict]:
    records: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for company in companies:
        company_id = company["id"]
        ticker = (company.get("ticker") or "").upper()
        name = company.get("name") or ""
        candidates: list[tuple[str, str]] = []
        for alias in CURATED_ALIASES.get(ticker, []):
            candidates.append((alias, "curated"))
        for alias in aliases_from_company_name(name):
            candidates.append((alias, "name"))
        for alias, alias_type in candidates:
            normalized = normalize_entity_text(alias)
            if len(normalized) < 2:
                continue
            key = (company_id, normalized)
            if key in seen:
                continue
            seen.add(key)
            records.append(
                {
                    "company_id": company_id,
                    "alias": alias,
                    "alias_type": alias_type,
                    "normalized_alias": normalized,
                }
            )
    return records
