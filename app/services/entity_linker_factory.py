from __future__ import annotations

from ..repositories import Repository
from .embeddings_service import DEFAULT_MODEL
from .entity_linking import EntityLinker, build_company_vectors

_company_vectors_cache: dict[str, dict[int, list[float]]] = {}
_linker_cache: dict[str, EntityLinker] = {}


def _cache_key(companies: list[dict], model_name: str) -> str:
    tickers = ",".join(sorted((company.get("ticker") or "") for company in companies)[:50])
    return f"{model_name}:{len(companies)}:{tickers}"


def get_company_vectors(companies: list[dict], *, model_name: str = DEFAULT_MODEL, device: str = "cpu") -> dict[int, list[float]]:
    key = _cache_key(companies, model_name)
    if key not in _company_vectors_cache:
        _company_vectors_cache[key] = build_company_vectors(companies, model_name=model_name, device=device)
    return _company_vectors_cache[key]


def create_entity_linker(
    repo: Repository,
    *,
    companies: list[dict] | None = None,
    enable_embedding_profiles: bool = False,
    embedding_device: str = "cpu",
    embedding_model: str = DEFAULT_MODEL,
) -> EntityLinker:
    repo.seed_company_aliases()
    company_rows = companies or repo.list_companies_for_matching()
    alias_index = repo.get_alias_index()
    tickers_key = ",".join(sorted((row.get("ticker") or "").upper() for row in company_rows))
    alias_count = sum(len(rows) for rows in alias_index.values())
    curated_count = sum(
        1
        for rows in alias_index.values()
        for row in rows
        if row.get("alias_type") == "curated"
    )
    cache_key = (
        f"{tickers_key}:{alias_count}:{curated_count}:{enable_embedding_profiles}:{embedding_device}:{embedding_model}"
    )
    if cache_key in _linker_cache:
        return _linker_cache[cache_key]
    vectors = (
        get_company_vectors(company_rows, model_name=embedding_model, device=embedding_device)
        if enable_embedding_profiles
        else None
    )
    linker = EntityLinker(
        companies=company_rows,
        alias_index=alias_index,
        company_vectors=vectors,
        boosted_tickers=repo.get_boosted_tickers(),
    )
    _linker_cache[cache_key] = linker
    return linker
