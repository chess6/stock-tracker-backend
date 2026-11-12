from __future__ import annotations

from app.db import get_db
from app.repositories import Repository
from app.services.entity_linker_factory import create_entity_linker
from app.services.entity_linking import EntityLinker


def _companies():
    return [
        {"id": 1, "ticker": "AAPL", "name": "Apple Inc", "sector": "Technology", "industry": "Consumer Electronics"},
        {"id": 2, "ticker": "AI", "name": "C3.ai Inc", "sector": "Technology", "industry": "Software"},
        {"id": 3, "ticker": "GOOGL", "name": "Alphabet Inc", "sector": "Technology", "industry": "Internet"},
    ]


def test_company_name_links_apple(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies(
            [
                {"ticker": "AAPL", "name": "Apple Inc"},
            ]
        )
        repo.seed_company_aliases()
        linker = create_entity_linker(repo)
        matches = linker.link_entities(
            "Apple reported record quarterly revenue and strong iPhone demand.",
            stage="enrichment",
            enable_embeddings=False,
        )
        assert any(
            match.ticker == "AAPL"
            and match.match_strategy in {"company_name", "company_alias", "alias", "fuzzy_name"}
            for match in matches
        )


def test_alias_links_google_without_ticker(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "GOOGL", "name": "Alphabet Inc"}])
        repo.seed_company_aliases()
        linker = create_entity_linker(repo)
        matches = linker.link_entities(
            "Google unveiled new cloud AI services for enterprise customers.",
            stage="enrichment",
            enable_embeddings=False,
        )
        assert any(match.ticker == "GOOGL" and match.match_strategy == "alias" for match in matches)


def test_ambiguous_ai_requires_context(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "AI", "name": "C3.ai Inc"}])
        repo.seed_company_aliases()
        companies = repo.list_companies_for_matching()
        alias_index = repo.get_alias_index()
        linker = EntityLinker(companies=companies, alias_index=alias_index)

        generic = linker.link_entities(
            "AI datacenter demand continues to accelerate across hyperscalers.",
            stage="ingest",
            enable_embeddings=False,
        )
        assert not any(match.ticker == "AI" for match in generic)

        specific = linker.link_entities(
            "C3.ai reported earnings that beat analyst expectations.",
            stage="enrichment",
            enable_embeddings=False,
        )
        assert any(match.ticker == "AI" for match in specific)


def test_enrichment_retag_persists_strategy(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "AAPL", "name": "Apple Inc"}])
        company = repo.get_company_by_ticker("AAPL")
        article_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/apple-earnings",
                "url_hash": "hash-apple-earnings",
                "title": "Apple beats earnings",
                "summary": "Strong quarter",
                "body_text": "",
                "published_at": "2025-01-20T14:00:00Z",
                "fetched_at": "2025-01-20T14:00:00Z",
                "raw_source": "test",
            },
            skip_dedup=True,
        )
        repo.seed_company_aliases()
        linker = create_entity_linker(repo)
        matches = linker.link_entities(
            "Apple Inc reported earnings that beat analyst estimates for the quarter.",
            stage="enrichment",
            enable_embeddings=False,
        )
        repo.save_entity_matches(article_id, matches, merge=True)
        row = repo.conn.execute(
            """
            SELECT match_strategy, extraction_stage, confidence, evidence_text
            FROM article_company
            WHERE article_id = ? AND company_id = ?
            """,
            (article_id, company["id"]),
        ).fetchone()
        assert row is not None
        assert row["match_strategy"] in {"company_name", "company_alias", "alias", "fuzzy_name"}
        assert row["extraction_stage"] == "enrichment"
        assert row["confidence"] >= 0.85


def test_nflx_meme_post_avoids_word_false_positives():
    companies = [{"id": index, "ticker": f"Z{index:04d}", "name": f"Placeholder {index}"} for index in range(1, 402)]
    companies.extend(
        [
            {"id": 500, "ticker": "NFLX", "name": "Netflix Inc"},
            {"id": 501, "ticker": "TD", "name": "Toronto-Dominion Bank"},
            {"id": 502, "ticker": "AD", "name": "Array Digital"},
            {"id": 503, "ticker": "AMP", "name": "Ameriprise Financial"},
            {"id": 504, "ticker": "BULL", "name": "Pacer Funds"},
            {"id": 505, "ticker": "CASH", "name": "Meta Financial Group"},
            {"id": 506, "ticker": "BGHL", "name": "Billion Group Holdings Ltd"},
            {"id": 507, "ticker": "RTON", "name": "Right On Brands Inc"},
            {"id": 508, "ticker": "GWKSY", "name": "Games Workshop Group PLC"},
            {"id": 509, "ticker": "SYBT", "name": "Stock Yards Bancorp Inc"},
        ]
    )
    linker = EntityLinker(companies=companies, alias_index={})
    text = (
        "🚀 NFLX IS THE MOST UNDERRATED MONEY PRINTER ON THE MARKET RIGHT NOW 🚀 "
        "Ad revenue expected to roughly DOUBLE to ~$3 billion in 2026. "
        "Bulls who understand this are loading up while smoothbrains panic. "
        "That's FREE. CASH. FLOW. TipRanks"
    )
    matches = linker.link_entities(text, stage="enrichment", enable_embeddings=False)
    tickers = {match.ticker for match in matches}
    assert tickers == {"NFLX"}


def test_enrichment_merge_drops_stale_enrichment_tags(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies(
            [
                {"ticker": "NFLX", "name": "Netflix Inc"},
                {"ticker": "BGHL", "name": "Billion Group Holdings Ltd"},
            ]
        )
        nflx = repo.get_company_by_ticker("NFLX")
        bghl = repo.get_company_by_ticker("BGHL")
        article_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/nflx-stale-tags",
                "url_hash": "hash-nflx-stale-tags",
                "title": "NFLX buyback thesis",
                "summary": "",
                "body_text": "",
                "published_at": "2025-01-20T14:00:00Z",
                "fetched_at": "2025-01-20T14:00:00Z",
                "raw_source": "test",
            },
            skip_dedup=True,
        )
        repo.link_entity_match(
            article_id,
            {
                "company_id": bghl["id"],
                "match_type": "company_name",
                "match_strategy": "company_name",
                "confidence": 0.92,
                "extraction_stage": "enrichment",
                "evidence_text": "billion",
            },
        )
        repo.seed_company_aliases()
        linker = create_entity_linker(repo)
        matches = linker.link_entities(
            "NFLX IS THE MOST UNDERRATED MONEY PRINTER ON THE MARKET",
            stage="enrichment",
            enable_embeddings=False,
        )
        repo.save_entity_matches(article_id, matches, merge=True)
        rows = repo.conn.execute(
            """
            SELECT c.ticker
            FROM article_company ac
            JOIN companies c ON c.id = ac.company_id
            WHERE ac.article_id = ?
            ORDER BY c.ticker
            """,
            (article_id,),
        ).fetchall()
        assert [row["ticker"] for row in rows] == ["NFLX"]


def test_enrichment_merge_replaces_ingest_tags(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies(
            [
                {"ticker": "NFLX", "name": "Netflix Inc"},
                {"ticker": "TD", "name": "Toronto-Dominion Bank"},
            ]
        )
        nflx = repo.get_company_by_ticker("NFLX")
        td = repo.get_company_by_ticker("TD")
        article_id = repo.upsert_article(
            {
                "canonical_url": "https://example.com/nflx-meme",
                "url_hash": "hash-nflx-meme",
                "title": "NFLX buyback thesis",
                "summary": "",
                "body_text": "",
                "published_at": "2025-01-20T14:00:00Z",
                "fetched_at": "2025-01-20T14:00:00Z",
                "raw_source": "test",
            },
            skip_dedup=True,
        )
        repo.link_entity_match(
            article_id,
            {
                "company_id": td["id"],
                "match_type": "ticker",
                "match_strategy": "ticker",
                "confidence": 0.95,
                "extraction_stage": "ingest",
            },
        )
        repo.seed_company_aliases()
        linker = create_entity_linker(repo)
        matches = linker.link_entities(
            "NFLX IS THE MOST UNDERRATED MONEY PRINTER ON THE MARKET",
            stage="enrichment",
            enable_embeddings=False,
        )
        repo.save_entity_matches(article_id, matches, merge=True)
        rows = repo.conn.execute(
            """
            SELECT c.ticker, ac.extraction_stage
            FROM article_company ac
            JOIN companies c ON c.id = ac.company_id
            WHERE ac.article_id = ?
            """,
            (article_id,),
        ).fetchall()
        tickers = {row["ticker"]: row["extraction_stage"] for row in rows}
        assert "TD" not in tickers
        assert tickers.get("NFLX") == "enrichment"


def test_embedding_matching_searches_all_companies():
    """The vector index covers every company, not just the first ~200 scanned before."""
    import math

    companies = [{"id": idx, "ticker": f"T{idx:03d}", "name": f"Filler Holdings {idx}"} for idx in range(1, 301)]
    dims = 8

    def unit(vec):
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec]

    vectors = {company["id"]: unit([1.0] + [0.0] * (dims - 1)) for company in companies}
    target_id = 250
    vectors[target_id] = unit([0.0, 1.0] + [0.0] * (dims - 2))
    linker = EntityLinker(companies=companies, alias_index={}, company_vectors=vectors)

    article_vector = unit([0.05, 1.0] + [0.0] * (dims - 2))
    matches = linker._match_embeddings("", "enrichment", article_vector, exclude_company_ids=set())

    assert matches, "expected an embedding match"
    assert matches[0].company_id == target_id
    assert matches[0].match_strategy == "embedding"
    assert matches[0].embedding_similarity > 0.99
    # Excluded companies (already matched by stronger strategies) are skipped.
    assert target_id not in {
        m.company_id
        for m in linker._match_embeddings("", "enrichment", article_vector, exclude_company_ids={target_id})
    }


def test_embedding_matching_ignores_mismatched_dimensions():
    companies = [{"id": 1, "ticker": "AAA", "name": "Alpha Corp"}]
    linker = EntityLinker(companies=companies, alias_index={}, company_vectors={1: [1.0, 0.0, 0.0]})
    assert linker._match_embeddings("", "enrichment", [1.0, 0.0], exclude_company_ids=set()) == []
