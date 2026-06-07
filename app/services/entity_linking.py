from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .company_aliases import normalize_entity_text
from .embeddings_service import cosine_similarity, embed_text
from .ticker_matcher import (
    STRICT_TICKER_MAX_LEN,
    TITLE_LEAD_CHARS,
    _AMBIGUOUS_TICKERS,
    match_ticker_signals,
)

logger = logging.getLogger("stock_tracker.entity_linking")

try:
    from rapidfuzz import fuzz  # type: ignore
except ImportError:  # pragma: no cover
    fuzz = None

FINANCE_CONTEXT_RE = re.compile(
    r"\b("
    r"earnings|revenue|profit|guidance|outlook|forecast|shares?|stock|stocks|equity|equities|"
    r"dividend|buyback|sec|analyst|upgrade|downgrade|price target|market cap|ipo|"
    r"quarterly|annual|results|eps|merger|acquisition|buyout|insider"
    r")\b",
    re.I,
)

MIN_DISPLAY_CONFIDENCE = 0.85
MIN_MARKET_REACTION_CONFIDENCE = 0.80
EMBEDDING_MIN_SIMILARITY = 0.52
EMBEDDING_MAX_CONFIDENCE = 0.82
PORTFOLIO_BOOST = 0.03


FUZZY_NAME_MAX_COMPANIES = 400
EMBEDDING_MAX_COMPANY_SCAN = 200
ENTITY_LINK_TEXT_MAX_CHARS = 12_000
NEWS_DISPLAY_MATCH_STRATEGIES = frozenset(
    {
        "cashtag",
        "headline_ticker",
        "alias",
        "company_name",
        "company_alias",
    }
)
NAME_MATCH_STRATEGIES = frozenset({"company_name", "company_alias", "alias", "fuzzy_name"})
HIGH_TRUST_TICKER_STRATEGIES = frozenset({"cashtag", "headline_ticker"})
_ALIAS_TOKEN_STOPWORDS = frozenset(
    {
        "inc",
        "corp",
        "ltd",
        "llc",
        "plc",
        "co",
        "group",
        "holdings",
        "company",
        "the",
        "and",
        "for",
        "international",
        "global",
        "systems",
        "services",
        "solutions",
        "technologies",
        "technology",
    }
)
# Single-token company names/aliases that collide with common finance/meme prose.
_SINGLE_TOKEN_ALIAS_BLOCKLIST = frozenset(
    {
        "stock",
        "stocks",
        "games",
        "game",
        "right",
        "price",
        "free",
        "cash",
        "play",
        "real",
        "flow",
        "money",
        "market",
        "billion",
        "million",
        "bank",
        "fund",
        "funds",
        "tech",
        "media",
        "news",
        "data",
        "cloud",
        "health",
        "food",
        "life",
        "live",
        "love",
        "next",
        "open",
        "close",
        "high",
        "call",
        "puts",
        "bull",
        "bears",
        "growth",
        "value",
        "share",
        "shares",
        "capital",
        "digital",
        "global",
        "american",
        "national",
        "first",
        "best",
        "world",
        "power",
        "energy",
        "water",
        "gold",
        "silver",
        "trust",
        "partners",
        "resources",
        "services",
        "solutions",
        "systems",
        "international",
        "financial",
        "investment",
        "investments",
        "insurance",
        "communications",
        "technology",
        "therapeutics",
        "pharmaceuticals",
        "properties",
        "enterprises",
        "industries",
        "generation",
        "universal",
        "preview",
        "personal",
        "document",
        "native",
        "model",
        "scale",
        "custom",
        "source",
        "travel",
        "limits",
        "compute",
        "filing",
        "search",
        "business",
        "interview",
        "adoption",
        "capacity",
        "extended",
        "thinking",
        "finance",
        "coding",
        "build",
        "agent",
        "agents",
        "frontier",
        "research",
        "software",
        "hardware",
        "platform",
        "network",
        "security",
        "digital",
    }
)


@dataclass(frozen=True)
class _AliasEntry:
    alias: str
    company_id: int
    ticker: str
    alias_type: str


@dataclass
class EntityMatch:
    company_id: int
    ticker: str
    match_strategy: str
    confidence: float
    extraction_stage: str
    evidence_text: str | None = None
    embedding_similarity: float | None = None
    match_type: str = field(default="entity")

    def boosted(self, amount: float = PORTFOLIO_BOOST) -> EntityMatch:
        return EntityMatch(
            company_id=self.company_id,
            ticker=self.ticker,
            match_strategy=self.match_strategy,
            confidence=min(1.0, round(self.confidence + amount, 4)),
            extraction_stage=self.extraction_stage,
            evidence_text=self.evidence_text,
            embedding_similarity=self.embedding_similarity,
            match_type=self.match_type,
        )


def build_entity_link_text(
    title: str,
    summary: str | None,
    body: str | None,
    *,
    max_chars: int = ENTITY_LINK_TEXT_MAX_CHARS,
) -> str:
    """Title + summary + leading body slice — entity mentions are almost always in the lead."""
    parts = [title or "", summary or ""]
    used = sum(len(part) for part in parts) + max(0, len(parts) - 1)
    if body and used < max_chars:
        parts.append(body[: max_chars - used])
    return " ".join(part for part in parts if part).strip()


def _snippet(text: str, start: int, end: int, *, radius: int = 40) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return text[left:right].strip()


def _alias_matches_text(alias: str, normalized_text: str) -> bool:
    if " " in alias:
        return alias in normalized_text
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", normalized_text))


def _is_noisy_single_token_alias(entry: _AliasEntry) -> bool:
    if " " in entry.alias:
        return False
    if entry.alias_type == "curated":
        return False
    # Legal-name single tokens (e.g. "research", "frontier") collide constantly in prose.
    if entry.alias_type == "name":
        return True
    if entry.alias in _SINGLE_TOKEN_ALIAS_BLOCKLIST:
        return True
    return len(entry.alias) < 5


def _merge_matches(matches: list[EntityMatch]) -> list[EntityMatch]:
    best: dict[int, EntityMatch] = {}
    for match in matches:
        current = best.get(match.company_id)
        if current is None or match.confidence > current.confidence:
            best[match.company_id] = match
    return sorted(best.values(), key=lambda item: item.confidence, reverse=True)


class EntityLinker:
    def __init__(
        self,
        *,
        companies: list[dict],
        alias_index: dict[str, list[dict]],
        company_vectors: dict[int, list[float]] | None = None,
        boosted_tickers: set[str] | None = None,
        max_matches: int = 8,
    ) -> None:
        self.companies = companies
        self.companies_by_id = {company["id"]: company for company in companies}
        self.alias_index = alias_index
        self.company_vectors = company_vectors or {}
        self.boosted_tickers = {ticker.upper() for ticker in (boosted_tickers or set())}
        self.max_matches = max_matches
        self._alias_entries = self._build_alias_entries()
        self._alias_by_token = self._build_alias_token_index(self._alias_entries)
        self._sector_labels = self._build_sector_labels()
        self._enable_fuzzy_names = len(companies) <= FUZZY_NAME_MAX_COMPANIES

    def _build_alias_entries(self) -> list[_AliasEntry]:
        entries: list[_AliasEntry] = []
        seen: set[tuple[int, str]] = set()
        for company in self.companies:
            company_id = company["id"]
            ticker = (company.get("ticker") or "").upper()
            name = company.get("name") or ""
            normalized_name = normalize_entity_text(name)
            name_candidates: list[tuple[str, str]] = []
            if normalized_name and " " in normalized_name:
                name_candidates.append((normalized_name, "name"))
            for alias, alias_type in (
                *name_candidates,
                *((row["normalized_alias"], row["alias_type"]) for row in self.alias_index.get(ticker, [])),
            ):
                if len(alias) < 3:
                    continue
                key = (company_id, alias)
                if key in seen:
                    continue
                seen.add(key)
                entries.append(_AliasEntry(alias=alias, company_id=company_id, ticker=ticker, alias_type=alias_type))
        entries.sort(key=lambda item: len(item.alias), reverse=True)
        return entries

    def _build_alias_token_index(self, entries: list[_AliasEntry]) -> dict[str, list[_AliasEntry]]:
        index: dict[str, list[_AliasEntry]] = {}
        for entry in entries:
            token = entry.alias.split()[0]
            if len(token) < 3 or token in _ALIAS_TOKEN_STOPWORDS:
                continue
            index.setdefault(token, []).append(entry)
        return index

    def _alias_candidates(self, normalized_text: str) -> list[_AliasEntry]:
        words = {word for word in normalized_text.split() if len(word) >= 3 and word not in _ALIAS_TOKEN_STOPWORDS}
        seen: set[tuple[int, str]] = set()
        candidates: list[_AliasEntry] = []
        for word in words:
            for entry in self._alias_by_token.get(word, ()):
                key = (entry.company_id, entry.alias)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(entry)
        candidates.sort(key=lambda item: len(item.alias), reverse=True)
        return candidates

    def _build_sector_labels(self) -> dict[str, list[tuple[int, str]]]:
        labels: dict[str, list[tuple[int, str]]] = {}
        for company in self.companies:
            ticker = (company.get("ticker") or "").upper()
            for raw in (company.get("sector"), company.get("industry")):
                label = normalize_entity_text(raw or "")
                if len(label) < 5:
                    continue
                labels.setdefault(label, []).append((company["id"], ticker))
        return labels

    def _apply_boosts(self, matches: list[EntityMatch]) -> list[EntityMatch]:
        output: list[EntityMatch] = []
        for match in matches:
            if match.ticker in self.boosted_tickers and 0.72 <= match.confidence < 0.92:
                output.append(match.boosted())
            else:
                output.append(match)
        return output

    def _passes_ambiguous_ticker_gate(
        self,
        *,
        ticker: str,
        text: str,
        strategy: str,
        company: dict,
        alias_hits: bool,
    ) -> bool:
        if strategy in HIGH_TRUST_TICKER_STRATEGIES or strategy in NAME_MATCH_STRATEGIES:
            return True
        if alias_hits:
            return True
        normalized_name = normalize_entity_text(company.get("name") or "")
        if normalized_name and normalized_name in normalize_entity_text(text):
            return True

        needs_strict_context = ticker in _AMBIGUOUS_TICKERS or len(ticker) <= STRICT_TICKER_MAX_LEN
        if not needs_strict_context:
            return True
        if strategy != "ticker_symbol":
            return strategy not in {"sector_context", "embedding"}

        if FINANCE_CONTEXT_RE.search(text):
            return True
        lead = text[:TITLE_LEAD_CHARS]
        if ticker in set(re.findall(r"\b([A-Z]{2,6})\b", lead)):
            return bool(re.search(rf"\b{re.escape(ticker)}\b", lead))
        return False

    def _match_aliases_and_names(self, text: str, stage: str) -> list[EntityMatch]:
        normalized_text = normalize_entity_text(text)
        if not normalized_text:
            return []
        matches: list[EntityMatch] = []
        matched_companies: set[int] = set()
        for entry in self._alias_candidates(normalized_text):
            if entry.company_id in matched_companies:
                continue
            if _is_noisy_single_token_alias(entry):
                continue
            if not _alias_matches_text(entry.alias, normalized_text):
                continue
            if entry.alias_type == "name":
                strategy = "company_name"
                confidence = 0.92
            elif entry.alias_type == "curated":
                strategy = "alias"
                confidence = 0.88
            else:
                strategy = "company_alias"
                confidence = 0.86
            matches.append(
                EntityMatch(
                    company_id=entry.company_id,
                    ticker=entry.ticker,
                    match_strategy=strategy,
                    confidence=confidence,
                    extraction_stage=stage,
                    evidence_text=entry.alias,
                )
            )
            matched_companies.add(entry.company_id)

        if self._enable_fuzzy_names and fuzz is not None:
            for company in self.companies:
                company_id = company["id"]
                if company_id in matched_companies:
                    continue
                normalized_name = normalize_entity_text(company.get("name") or "")
                if len(normalized_name) < 10 or " " not in normalized_name:
                    continue
                score = fuzz.partial_ratio(normalized_name, normalized_text) / 100.0
                if score >= 0.94:
                    matches.append(
                        EntityMatch(
                            company_id=company_id,
                            ticker=(company.get("ticker") or "").upper(),
                            match_strategy="fuzzy_name",
                            confidence=round(min(0.85, 0.75 + (score - 0.88) * 0.8), 4),
                            extraction_stage=stage,
                            evidence_text=normalized_name,
                        )
                    )
        return matches

    def _match_sector_context(self, text: str, stage: str) -> list[EntityMatch]:
        if not FINANCE_CONTEXT_RE.search(text):
            return []
        normalized_text = normalize_entity_text(text)
        matches: list[EntityMatch] = []
        seen: set[int] = set()
        for label, companies in self._sector_labels.items():
            if label not in normalized_text:
                continue
            for company_id, ticker in companies:
                if company_id in seen:
                    continue
                seen.add(company_id)
                matches.append(
                    EntityMatch(
                        company_id=company_id,
                        ticker=ticker,
                        match_strategy="sector_context",
                        confidence=0.72,
                        extraction_stage=stage,
                        evidence_text=label,
                    )
                )
        return matches

    def _match_embeddings(
        self,
        text: str,
        stage: str,
        article_vector: list[float] | None,
        *,
        exclude_company_ids: set[int],
    ) -> list[EntityMatch]:
        if not article_vector or not self.company_vectors:
            return []
        matches: list[EntityMatch] = []
        candidate_ids: list[int] = []
        for company in self.companies:
            company_id = company["id"]
            if company_id in exclude_company_ids:
                continue
            ticker = (company.get("ticker") or "").upper()
            if ticker in self.boosted_tickers:
                candidate_ids.append(company_id)
        if len(candidate_ids) < EMBEDDING_MAX_COMPANY_SCAN:
            for company_id in self.company_vectors:
                if company_id in exclude_company_ids or company_id in candidate_ids:
                    continue
                candidate_ids.append(company_id)
                if len(candidate_ids) >= EMBEDDING_MAX_COMPANY_SCAN:
                    break
        for company_id in candidate_ids:
            vector = self.company_vectors.get(company_id)
            if not vector:
                continue
            company = self.companies_by_id.get(company_id)
            if not company:
                continue
            similarity = cosine_similarity(article_vector, vector)
            if similarity < EMBEDDING_MIN_SIMILARITY:
                continue
            confidence = round(min(EMBEDDING_MAX_CONFIDENCE, 0.55 + similarity * 0.35), 4)
            matches.append(
                EntityMatch(
                    company_id=company_id,
                    ticker=(company.get("ticker") or "").upper(),
                    match_strategy="embedding",
                    confidence=confidence,
                    extraction_stage=stage,
                    evidence_text=(company.get("name") or "")[:120],
                    embedding_similarity=round(similarity, 4),
                )
            )
        matches.sort(key=lambda item: item.confidence, reverse=True)
        return matches[:3]

    def link_entities(
        self,
        text: str,
        *,
        stage: str,
        article_vector: list[float] | None = None,
        enable_embeddings: bool = True,
    ) -> list[EntityMatch]:
        if not text.strip():
            return []

        matches: list[EntityMatch] = []
        alias_company_ids: set[int] = set()

        for signal in match_ticker_signals(text, self.companies):
            company = self.companies_by_id.get(signal.company_id)
            if not company:
                continue
            ticker = (company.get("ticker") or "").upper()
            if not self._passes_ambiguous_ticker_gate(
                ticker=ticker,
                text=text,
                strategy=signal.match_strategy,
                company=company,
                alias_hits=False,
            ):
                continue
            matches.append(
                EntityMatch(
                    company_id=signal.company_id,
                    ticker=ticker,
                    match_strategy=signal.match_strategy,
                    confidence=signal.confidence,
                    extraction_stage=stage,
                    evidence_text=signal.evidence_text,
                )
            )

        name_matches = self._match_aliases_and_names(text, stage)
        alias_company_ids = {match.company_id for match in name_matches}
        matches.extend(name_matches)

        filtered: list[EntityMatch] = []
        for match in matches:
            company = self.companies_by_id.get(match.company_id)
            if not company:
                continue
            ticker = (company.get("ticker") or "").upper()
            if not self._passes_ambiguous_ticker_gate(
                ticker=ticker,
                text=text,
                strategy=match.match_strategy,
                company=company,
                alias_hits=match.company_id in alias_company_ids,
            ):
                continue
            filtered.append(match)
        matches = filtered

        matches.extend(self._match_sector_context(text, stage))

        merged = _merge_matches(matches)
        strong_ids = {match.company_id for match in merged if match.confidence >= 0.8}

        if enable_embeddings:
            merged.extend(
                self._match_embeddings(
                    text,
                    stage,
                    article_vector,
                    exclude_company_ids=strong_ids,
                )
            )
            merged = _merge_matches(merged)

        merged = self._apply_boosts(merged)
        return merged[: self.max_matches]


def build_company_profile_text(company: dict) -> str:
    parts = [
        company.get("name") or "",
        company.get("ticker") or "",
        company.get("sector") or "",
        company.get("industry") or "",
    ]
    return " ".join(part for part in parts if part).strip()


def build_company_vectors(
    companies: list[dict],
    *,
    model_name: str = "all-MiniLM-L6-v2",
    device: str = "cpu",
) -> dict[int, list[float]]:
    from .embeddings_service import embed_texts_batch

    profiles: list[tuple[int, str]] = []
    for company in companies:
        profile = build_company_profile_text(company)
        if profile:
            profiles.append((company["id"], profile))
    if not profiles:
        return {}
    vectors_list = embed_texts_batch(
        [profile for _, profile in profiles],
        model_name=model_name,
        device=device,
        batch_size=64,
    )
    vectors: dict[int, list[float]] = {}
    for (company_id, _), vector in zip(profiles, vectors_list):
        if vector:
            vectors[company_id] = vector
    return vectors
