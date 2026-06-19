from __future__ import annotations

import logging
from dataclasses import dataclass

from ..repositories import Repository
from .article_extraction import DomainFetcher, needs_extraction
from .article_cluster import assign_article_to_event_cluster
from .article_ranking import (
    RankInputs,
    compute_news_importance_score,
    compute_novelty_score,
    compute_rank_score,
)
from .feature_flags import is_enabled
from .embeddings_service import (
    DEFAULT_MODEL,
    EmbeddingsUnavailableError,
    embed_text,
    embed_texts_batch,
    ensure_embedding_model_available,
    make_embed_fn,
)
from .event_classification import classify_events
from .market_reaction import compute_market_reactions
from .nlp_device import gpu_batch_enabled, torch_device_for_batch
from .entity_linker_factory import create_entity_linker
from .entity_linking import build_entity_link_text
from .sentiment_analysis import analyze_finbert_batch, build_sentiment_result

logger = logging.getLogger("stock_tracker.pipeline")


@dataclass
class _PreparedArticle:
    article_id: int
    row: dict
    title: str
    summary: str | None
    body: str | None
    full_text: str


class ArticlePipeline:
    """Multi-stage enrichment: extract → sentiment → embeddings → events → market reaction → rank."""

    def __init__(
        self,
        repo: Repository,
        *,
        fetcher: DomainFetcher | None = None,
        embedding_model: str = DEFAULT_MODEL,
        enable_finbert: bool = True,
        enable_embeddings: bool = True,
    ) -> None:
        self.repo = repo
        self.fetcher = fetcher or DomainFetcher(repo)
        self.embedding_model = embedding_model
        self.enable_finbert = enable_finbert
        self.enable_embeddings = enable_embeddings

    def _full_text(self, title: str, summary: str | None, body: str | None) -> str:
        return " ".join(filter(None, [title, summary, body])).strip()

    def _entity_link_text(self, title: str, summary: str | None, body: str | None) -> str:
        return build_entity_link_text(title, summary, body)

    def _extract_article_content(
        self,
        article_id: int,
        row: dict,
    ) -> tuple[str, str | None, str | None]:
        title = row.get("title") or ""
        summary = row.get("summary")
        body = row.get("body_text")
        url = row.get("canonical_url") or ""

        if needs_extraction(body) and url:
            extracted, content_hash = self.fetcher.fetch_and_extract(url)
            if extracted:
                body = extracted
                self.repo.update_article_body(article_id, extracted, content_hash=content_hash)
                self.repo.set_article_extraction_status(article_id, "extracted")
            else:
                self.repo.set_article_extraction_status(article_id, "failed")
        return title, summary, body

    def _cached_embedding_vector(self, row: dict) -> list[float] | None:
        if not self.enable_embeddings:
            return None
        article_id = row.get("id")
        content_hash = row.get("content_hash")
        if not article_id or not content_hash:
            return None
        stored_hash = self.repo.get_article_embedding_hash(article_id, model=self.embedding_model)
        if stored_hash != content_hash:
            return None
        vector = self.repo.get_article_embedding_vector(article_id, model=self.embedding_model)
        if vector:
            logger.debug("embedding skip article_id=%s unchanged content_hash", article_id)
        return vector

    def _finalize_article(
        self,
        prepared: _PreparedArticle,
        *,
        sentiment,
        vector: list[float] | None,
        embed_device: str,
    ) -> dict:
        article_id = prepared.article_id
        row = prepared.row
        title = prepared.title
        summary = prepared.summary
        body = prepared.body

        max_similarity = None
        dup_id = None
        embed_fn = (
            make_embed_fn(self.embedding_model, device=embed_device)
            if self.enable_embeddings
            else None
        )
        if vector:
            self.repo.upsert_article_embedding(
                article_id,
                model=self.embedding_model,
                vector=vector,
                content_hash=row.get("content_hash"),
            )
            dup_id, similarity = self.repo.find_embedding_duplicate(
                article_id,
                vector,
                model=self.embedding_model,
                threshold=0.92,
            )
            max_similarity = similarity
            if dup_id:
                self.repo.mark_article_duplicate(article_id, dup_id)
                self.repo.copy_article_entity_matches(dup_id, article_id)

        if not dup_id:
            linker = create_entity_linker(
                self.repo,
                enable_embedding_profiles=self.enable_embeddings,
                embedding_device=embed_device,
            )
            entity_matches = linker.link_entities(
                self._entity_link_text(title, summary, body),
                stage="enrichment",
                article_vector=vector,
                enable_embeddings=self.enable_embeddings,
            )
            self.repo.save_entity_matches(article_id, entity_matches, merge=True)

        self.repo.update_article_sentiment(article_id, sentiment)

        events = classify_events(prepared.full_text, embed_fn=embed_fn)
        self.repo.replace_article_events(article_id, events)
        primary_event = events[0].event_type if events else None
        event_confidence = events[0].confidence if events else None

        tickers = self.repo.get_article_tickers(article_id)
        reactions = compute_market_reactions(
            self.repo,
            article_id=article_id,
            tickers=tickers,
            published_at=row.get("published_at"),
            sentiment_score=sentiment.score,
            primary_event=primary_event,
        )
        self.repo.replace_article_market_reactions(article_id, reactions)

        abnormal = reactions[0].abnormal_return_1d if reactions else None
        novelty = compute_novelty_score(max_similarity)
        feed_source_weight = self.repo.get_feed_source_weight(row.get("raw_source"))
        entity_confidence = self.repo.get_article_entity_confidence_avg(article_id)
        divergence_context = None
        for ticker in tickers:
            snapshot = self.repo.get_recent_narrative_divergence_for_ticker(ticker)
            if snapshot:
                divergence_context = f"{snapshot['divergence_signal']}:{ticker.upper()}"
                break

        cluster_id = assign_article_to_event_cluster(
            self.repo,
            article_id=article_id,
            event_type=primary_event,
            headline=row.get("title"),
            published_at=row.get("published_at"),
            source_domain=row.get("source_domain"),
            sentiment_score=sentiment.score,
            vector=vector,
        )

        rank_inputs = RankInputs(
            sentiment_score=sentiment.score,
            vader_compound=sentiment.vader_compound,
            source_domain=row.get("source_domain"),
            novelty_score=novelty,
            abnormal_return_1d=abnormal,
            event_confidence=event_confidence,
            source_weight=feed_source_weight,
            entity_confidence=entity_confidence,
            published_at=row.get("published_at"),
        )
        rank_score = None
        importance_score = None
        if is_enabled("experimental_composite_rank", self.repo):
            rank_score = compute_rank_score(rank_inputs)
            importance_score = compute_news_importance_score(rank_inputs)
            self.repo.update_article_ranking(
                article_id,
                rank_score=rank_score,
                novelty_score=novelty,
                news_importance_score=importance_score,
                divergence_context=divergence_context,
                event_cluster_id=cluster_id,
            )
        self.repo.set_article_pipeline_status(article_id, "complete")

        return {
            "article_id": article_id,
            "status": "complete",
            "sentiment_label": sentiment.label,
            "events": [e.event_type for e in events],
            "rank_score": rank_score,
            "news_importance_score": importance_score,
            "event_cluster_id": cluster_id,
            "duplicate_of": dup_id if vector and dup_id else None,
        }

    def process_article(self, article_id: int) -> dict:
        row = self.repo.get_article_by_id(article_id)
        if not row:
            return {"article_id": article_id, "status": "missing"}

        self.repo.set_article_pipeline_status(article_id, "processing")
        title, summary, body = self._extract_article_content(article_id, row)
        row = self.repo.get_article_by_id(article_id) or row
        full_text = self._full_text(title, summary, body)

        vector = self._cached_embedding_vector(row)
        if vector is None and self.enable_embeddings:
            vector = embed_text(full_text, model_name=self.embedding_model, device="cpu")

        finbert = None
        if self.enable_finbert:
            from .sentiment_analysis import analyze_finbert

            finbert = analyze_finbert(full_text)

        sentiment = build_sentiment_result(title, summary=summary, body=body, finbert=finbert)
        prepared = _PreparedArticle(
            article_id=article_id,
            row=row,
            title=title,
            summary=summary,
            body=body,
            full_text=full_text,
        )
        return self._finalize_article(prepared, sentiment=sentiment, vector=vector, embed_device="cpu")

    def process_batch(self, *, limit: int = 25) -> dict:
        recovered = self.repo.recover_stuck_pipeline_articles()
        if self.enable_embeddings:
            ensure_embedding_model_available(self.embedding_model, device="cpu")
        pending = self.repo.list_articles_pending_pipeline(limit=limit)
        pipeline_counts = self.repo.get_pipeline_status_counts()
        if not pending:
            return {
                "processed": 0,
                "results": [],
                "gpu_batch": False,
                "recovered": recovered,
                "pipeline": pipeline_counts,
            }

        prepared_items: list[_PreparedArticle] = []
        results: list[dict] = []
        for article_id in pending:
            row = self.repo.get_article_by_id(article_id)
            if not row:
                results.append({"article_id": article_id, "status": "missing"})
                continue
            self.repo.set_article_pipeline_status(article_id, "processing")
            try:
                title, summary, body = self._extract_article_content(article_id, row)
                row = self.repo.get_article_by_id(article_id) or row
                prepared_items.append(
                    _PreparedArticle(
                        article_id=article_id,
                        row=row,
                        title=title,
                        summary=summary,
                        body=body,
                        full_text=self._full_text(title, summary, body),
                    )
                )
            except Exception as exc:
                logger.exception("pipeline extract failed article_id=%s", article_id)
                self.repo.set_article_pipeline_status(article_id, "error")
                results.append({"article_id": article_id, "status": "error", "error": str(exc)})

        use_gpu_batch = gpu_batch_enabled(len(prepared_items))
        nlp_device = torch_device_for_batch(len(prepared_items))
        if use_gpu_batch:
            logger.info(
                "Using GPU batched NLP for %d articles (device=%s)",
                len(prepared_items),
                nlp_device,
            )

        texts = [item.full_text for item in prepared_items]
        finbert_results: list = [None] * len(prepared_items)
        if self.enable_finbert:
            finbert_results = analyze_finbert_batch(
                texts,
                use_gpu=use_gpu_batch,
                batch_size=16,
            )

        vectors: list[list[float] | None] = [None] * len(prepared_items)
        if self.enable_embeddings:
            missing_indices: list[int] = []
            missing_texts: list[str] = []
            for idx, prepared in enumerate(prepared_items):
                cached = self._cached_embedding_vector(prepared.row)
                if cached is not None:
                    vectors[idx] = cached
                else:
                    missing_indices.append(idx)
                    missing_texts.append(prepared.full_text)
            if missing_texts:
                if use_gpu_batch:
                    computed = embed_texts_batch(
                        missing_texts,
                        model_name=self.embedding_model,
                        device=nlp_device,
                        batch_size=32,
                    )
                else:
                    computed = [
                        embed_text(text, model_name=self.embedding_model, device="cpu")
                        for text in missing_texts
                    ]
                for idx, vector in zip(missing_indices, computed):
                    vectors[idx] = vector

        for prepared, finbert, vector in zip(prepared_items, finbert_results, vectors):
            try:
                if not self.enable_finbert:
                    finbert = None
                sentiment = build_sentiment_result(
                    prepared.title,
                    summary=prepared.summary,
                    body=prepared.body,
                    finbert=finbert,
                )
                results.append(
                    self._finalize_article(
                        prepared,
                        sentiment=sentiment,
                        vector=vector if self.enable_embeddings else None,
                        embed_device=nlp_device if use_gpu_batch else "cpu",
                    )
                )
            except Exception as exc:
                logger.exception("pipeline failed article_id=%s", prepared.article_id)
                self.repo.set_article_pipeline_status(prepared.article_id, "error")
                results.append(
                    {"article_id": prepared.article_id, "status": "error", "error": str(exc)}
                )

        pipeline_counts = self.repo.get_pipeline_status_counts()
        return {
            "processed": len(results),
            "results": results,
            "gpu_batch": use_gpu_batch,
            "nlp_device": nlp_device if use_gpu_batch else "cpu",
            "recovered": recovered,
            "pipeline": pipeline_counts,
        }

    def retag_batch(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        retag_all: bool = True,
    ) -> dict:
        """Re-run entity linking on already-complete articles (fast path)."""
        import time

        t0 = time.monotonic()
        only_missing = not retag_all
        article_ids = self.repo.list_articles_for_retag(
            limit=limit,
            offset=offset,
            only_missing_enrichment=only_missing,
        )
        total = self.repo.count_articles_for_retag(only_missing_enrichment=only_missing)
        if not article_ids:
            return {
                "processed": 0,
                "results": [],
                "mode": "retag",
                "offset": offset,
                "remaining_retag": max(0, total - offset),
                "retag_total": total,
                "elapsed_seconds": round(time.monotonic() - t0, 2),
                "pipeline": self.repo.get_pipeline_status_counts(),
            }

        linker = create_entity_linker(
            self.repo,
            enable_embedding_profiles=self.enable_embeddings,
            embedding_device="cpu",
            embedding_model=self.embedding_model,
        )
        rows_by_id = self.repo.get_articles_by_ids(article_ids)
        texts: list[str] = []
        ordered_rows: list[dict] = []
        for article_id in article_ids:
            row = rows_by_id.get(article_id)
            if not row:
                continue
            ordered_rows.append(row)
            texts.append(self._entity_link_text(row.get("title") or "", row.get("summary"), row.get("body_text")))

        vectors: list[list[float] | None] = [None] * len(ordered_rows)
        if self.enable_embeddings:
            missing_indices: list[int] = []
            missing_texts: list[str] = []
            for idx, row in enumerate(ordered_rows):
                article_id = row["id"]
                cached = self._cached_embedding_vector(row)
                if cached is not None:
                    vectors[idx] = cached
                elif texts[idx]:
                    missing_indices.append(idx)
                    missing_texts.append(texts[idx])
            if missing_texts:
                from .embeddings_service import embed_texts_batch

                computed = embed_texts_batch(
                    missing_texts,
                    model_name=self.embedding_model,
                    device="cpu",
                    batch_size=32,
                )
                for idx, vector in zip(missing_indices, computed):
                    vectors[idx] = vector

        results: list[dict] = []
        for row, full_text, vector in zip(ordered_rows, texts, vectors):
            article_id = row["id"]
            try:
                matches = linker.link_entities(
                    full_text,
                    stage="enrichment",
                    article_vector=vector,
                    enable_embeddings=self.enable_embeddings,
                )
                saved = self.repo.save_entity_matches(article_id, matches, merge=True, defer_commit=True)
                results.append(
                    {
                        "article_id": article_id,
                        "status": "retagged",
                        "tickers": [match.ticker for match in matches],
                        "matches_saved": saved,
                    }
                )
            except Exception as exc:
                logger.exception("retag failed article_id=%s", article_id)
                results.append({"article_id": article_id, "status": "error", "error": str(exc)})
        self.repo.conn.commit()

        missing_ids = [article_id for article_id in article_ids if article_id not in rows_by_id]
        for article_id in missing_ids:
            results.append({"article_id": article_id, "status": "missing"})

        next_offset = offset + len(article_ids)
        return {
            "processed": len(results),
            "results": results,
            "mode": "retag",
            "offset": offset,
            "next_offset": next_offset,
            "remaining_retag": max(0, total - next_offset),
            "retag_total": total,
            "elapsed_seconds": round(time.monotonic() - t0, 2),
            "embeddings_enabled": self.enable_embeddings,
            "pipeline": self.repo.get_pipeline_status_counts(),
        }
