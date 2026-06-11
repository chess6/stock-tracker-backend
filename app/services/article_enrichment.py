from __future__ import annotations

import re

from .article_dedup import compute_simhash

_POSITIVE = frozenset({
    "beat", "beats", "surge", "surges", "rally", "rallies", "gain", "gains", "growth", "record",
    "strong", "upgrade", "upgraded", "profit", "profits", "bullish", "outperform", "expansion",
})
_NEGATIVE = frozenset({
    "miss", "misses", "fall", "falls", "drop", "drops", "slump", "slumps", "loss", "losses",
    "weak", "downgrade", "downgraded", "bearish", "underperform", "layoff", "layoffs", "fraud",
})

_TOPIC_KEYWORDS = {
    "semis": ("semiconductor", "chip", "nvidia", "tsmc", "foundry"),
    "cloud": ("cloud", "aws", "azure", "datacenter", "data center"),
    "security": ("cyber", "security", "breach", "ransomware", "malware"),
    "finance": ("fed", "rates", "inflation", "earnings", "ipo", "merger", "acquisition"),
    "regulatory": ("sec", "regulation", "antitrust", "lawsuit", "fine"),
}


def simhash_fingerprint(text: str, bits: int = 64) -> str:
    """Canonical simhash — delegates to article_dedup.compute_simhash."""
    del bits  # retained for call-site compatibility
    return compute_simhash(text or "", None)


def simple_sentiment(text: str) -> tuple[str | None, float | None]:
    words = re.findall(r"[a-z']+", (text or "").lower())
    if not words:
        return None, None
    pos = sum(1 for word in words if word in _POSITIVE)
    neg = sum(1 for word in words if word in _NEGATIVE)
    if pos == 0 and neg == 0:
        return "neutral", 0.0
    if pos >= neg:
        score = min(1.0, pos / max(len(words), 1) * 8)
        return "positive", round(score, 3)
    score = min(1.0, neg / max(len(words), 1) * 8)
    return "negative", round(-score, 3)


def infer_topic_cluster(text: str) -> str | None:
    lowered = (text or "").lower()
    for topic, keywords in _TOPIC_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return topic
    return None
