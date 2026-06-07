from __future__ import annotations

import re
from dataclasses import dataclass

EVENT_TYPES = (
    "earnings_beat",
    "earnings_miss",
    "guidance_increase",
    "guidance_cut",
    "insider_buying",
    "layoffs",
    "ai_datacenter_expansion",
    "semiconductor_supply_chain",
    "regulation_legal_risk",
    "mergers_acquisitions",
    "debt_reduction",
    "stock_buyback",
    "capital_raise",
    "macroeconomic",
)

# Rule patterns: (event_type, [(regex, weight), ...])
_EVENT_RULES: dict[str, list[tuple[str, float]]] = {
    "earnings_beat": [
        (r"\bbeats?\s+(earnings|estimates|expectations)\b", 0.9),
        (r"\bearnings\s+beat\b", 0.95),
        (r"\btopped\s+analyst\s+estimates\b", 0.85),
        (r"\brecord\s+(revenue|profit|earnings)\b", 0.7),
    ],
    "earnings_miss": [
        (r"\bmiss(es|ed)?\s+(earnings|estimates|expectations)\b", 0.9),
        (r"\bearnings\s+miss\b", 0.95),
        (r"\bfell\s+short\s+of\s+estimates\b", 0.85),
        (r"\bdisappointing\s+(earnings|results|quarter)\b", 0.8),
    ],
    "guidance_increase": [
        (r"\brais(es|ed)?\s+(guidance|outlook|forecast)\b", 0.9),
        (r"\bupgraded\s+guidance\b", 0.9),
        (r"\bstronger\s+(outlook|forecast|guidance)\b", 0.75),
    ],
    "guidance_cut": [
        (r"\bcut(s|ting)?\s+(guidance|outlook|forecast)\b", 0.9),
        (r"\blowered\s+(guidance|outlook|forecast)\b", 0.9),
        (r"\bweak(er)?\s+(outlook|forecast|guidance)\b", 0.75),
    ],
    "insider_buying": [
        (r"\binsider\s+(buy|buying|purchase|purchases)\b", 0.9),
        (r"\bform\s+4\b", 0.7),
        (r"\bceo\s+bought\s+shares\b", 0.85),
    ],
    "layoffs": [
        (r"\blayoff(s)?\b", 0.9),
        (r"\bjob\s+cuts?\b", 0.85),
        (r"\bworkforce\s+reduction\b", 0.85),
        (r"\brestructur(ing|e)\b", 0.55),
    ],
    "ai_datacenter_expansion": [
        (r"\b(ai|artificial intelligence)\b.*\b(data\s*center|datacenter|gpu|nvidia|hyperscaler)\b", 0.8),
        (r"\b(data\s*center|datacenter)\b.*\b(expan(sion|ding)|build|invest)\b", 0.75),
        (r"\bcloud\s+capacity\b", 0.65),
    ],
    "semiconductor_supply_chain": [
        (r"\b(semiconductor|chip|foundry|wafer|tsmc|asml)\b", 0.7),
        (r"\bsupply\s+chain\b.*\b(chip|semiconductor)\b", 0.8),
        (r"\bchip\s+shortage\b", 0.85),
    ],
    "regulation_legal_risk": [
        (r"\b(sec|doj|ftc|antitrust|lawsuit|investigation|subpoena|fine|penalty)\b", 0.75),
        (r"\bregulat(ory|ion|or)\b", 0.55),
        (r"\bclass\s+action\b", 0.8),
    ],
    "mergers_acquisitions": [
        (r"\b(acquires?|acquisition|merger|buyout|takeover|deal\s+to\s+buy)\b", 0.85),
        (r"\bmergers?\s+and\s+acquisitions\b", 0.9),
    ],
    "debt_reduction": [
        (r"\bdebt\s+reduction\b", 0.9),
        (r"\bpay(s|ing)?\s+down\s+debt\b", 0.85),
        (r"\bdeleverag(e|ing)\b", 0.8),
    ],
    "stock_buyback": [
        (r"\b(share|stock)\s+buyback\b", 0.9),
        (r"\brepurchase(s|d)?\s+shares\b", 0.85),
        (r"\bauthorized\s+.*\bbuyback\b", 0.8),
    ],
    "capital_raise": [
        (r"\b(secondary\s+offering|stock\s+offering|capital\s+raise|raises?\s+\$\d)", 0.85),
        (r"\bipo\b", 0.6),
        (r"\bconvertible\s+notes?\b", 0.7),
    ],
    "macroeconomic": [
        (r"\b(fed|federal reserve|cpi|inflation|jobs\s+report|gdp|interest\s+rate|treasury\s+yield)\b", 0.8),
        (r"\brecession\b", 0.75),
        (r"\btariff(s)?\b", 0.7),
    ],
}

_COMPILED_RULES = {
    event_type: [(re.compile(pattern, re.I), weight) for pattern, weight in patterns]
    for event_type, patterns in _EVENT_RULES.items()
}

# Anchor phrases for embedding-assisted classification (optional)
EVENT_ANCHORS: dict[str, str] = {
    "earnings_beat": "Company reported quarterly earnings that beat analyst estimates and expectations.",
    "earnings_miss": "Company missed earnings estimates and reported disappointing quarterly results.",
    "guidance_increase": "Company raised full-year guidance and issued a stronger outlook.",
    "guidance_cut": "Company cut guidance and lowered its forward outlook.",
    "insider_buying": "Corporate insiders purchased shares according to SEC Form 4 filings.",
    "layoffs": "Company announced layoffs and workforce reductions.",
    "ai_datacenter_expansion": "Expansion of AI infrastructure, GPUs, and data center capacity.",
    "semiconductor_supply_chain": "Semiconductor supply chain, chip manufacturing, and foundry news.",
    "regulation_legal_risk": "Regulatory investigation, legal risk, antitrust, or government enforcement.",
    "mergers_acquisitions": "Merger, acquisition, or takeover deal announcement.",
    "debt_reduction": "Company reduced debt and improved balance sheet leverage.",
    "stock_buyback": "Company announced a share repurchase or stock buyback program.",
    "capital_raise": "Company raised capital through a stock offering or convertible debt.",
    "macroeconomic": "Macroeconomic news on Fed policy, inflation, rates, or employment.",
}


@dataclass
class EventClassification:
    event_type: str
    confidence: float
    method: str


def classify_events_rules(text: str, *, min_confidence: float = 0.55) -> list[EventClassification]:
    if not text.strip():
        return []
    normalized = re.sub(r"\s+", " ", text.lower())
    results: list[EventClassification] = []
    for event_type, patterns in _COMPILED_RULES.items():
        best = 0.0
        for regex, weight in patterns:
            if regex.search(normalized):
                best = max(best, weight)
        if best >= min_confidence:
            results.append(EventClassification(event_type=event_type, confidence=best, method="rules"))
    results.sort(key=lambda item: item.confidence, reverse=True)
    return results


def classify_events_embedding(
    text: str,
    *,
    embed_fn,
    min_confidence: float = 0.45,
) -> list[EventClassification]:
    """Optional second pass using cosine similarity to EVENT_ANCHORS."""
    if embed_fn is None or not text.strip():
        return []
    try:
        from .embeddings_service import cosine_similarity

        text_vec = embed_fn(text[:2000])
        if not text_vec:
            return []
        anchor_vecs = {event: embed_fn(anchor) for event, anchor in EVENT_ANCHORS.items()}
        results: list[EventClassification] = []
        for event_type, anchor_vec in anchor_vecs.items():
            if not anchor_vec:
                continue
            sim = cosine_similarity(text_vec, anchor_vec)
            if sim >= min_confidence:
                results.append(
                    EventClassification(event_type=event_type, confidence=sim, method="embedding")
                )
        results.sort(key=lambda item: item.confidence, reverse=True)
        return results[:3]
    except Exception:
        return []


def classify_events(text: str, *, embed_fn=None) -> list[EventClassification]:
    rules = classify_events_rules(text)
    if rules:
        return rules[:5]
    embedding_hits = classify_events_embedding(text, embed_fn=embed_fn)
    return embedding_hits[:3]
