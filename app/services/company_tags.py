from __future__ import annotations

MAX_TAGS_PER_TICKER = 12
MAX_TAG_LENGTH = 40


def normalize_ticker(ticker: str) -> str:
    return str(ticker or "").strip().upper()


def normalize_tag_label(tag: str) -> str:
    trimmed = " ".join(str(tag or "").strip().split())
    if not trimmed:
        return ""
    return trimmed[:MAX_TAG_LENGTH]


def _tag_key(tag: str) -> str:
    return normalize_tag_label(tag).lower()


def dedupe_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        label = normalize_tag_label(tag)
        key = _tag_key(label)
        if not label or key in seen:
            continue
        seen.add(key)
        out.append(label)
    return out[:MAX_TAGS_PER_TICKER]


def sanitize_ticker_tags_map(ticker_tags: dict | None) -> dict[str, list[str]]:
    if not isinstance(ticker_tags, dict):
        return {}
    out: dict[str, list[str]] = {}
    for ticker, tags in ticker_tags.items():
        normalized_ticker = normalize_ticker(ticker)
        if not normalized_ticker:
            continue
        normalized_tags = dedupe_tags(tags if isinstance(tags, list) else [])
        if normalized_tags:
            out[normalized_ticker] = normalized_tags
    return out
