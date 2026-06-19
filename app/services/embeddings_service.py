from __future__ import annotations

# Embedding inference touchpoint — storage migration options: docs/SCALING.md

import json
import logging
import math
from typing import Callable

logger = logging.getLogger("stock_tracker.pipeline.embeddings")

DEFAULT_MODEL = "all-MiniLM-L6-v2"
ALT_MODEL = "bge-small-en-v1.5"

_model_cache: dict[str, object] = {}


class EmbeddingsUnavailableError(RuntimeError):
    """Raised when embedding-heavy enrichment was requested but the model cannot load."""


def _cache_key(model_name: str, device: str) -> str:
    return f"{model_name}::{device}"


def _load_model(model_name: str, *, device: str = "cpu"):
    key = _cache_key(model_name, device)
    if key in _model_cache:
        return _model_cache[key]
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ImportError as exc:
        logger.warning("sentence-transformers not installed: %s", exc)
        return None
    try:
        model = SentenceTransformer(model_name, device=device)
        _model_cache[key] = model
        logger.info("Loaded embedding model %s on %s", model_name, device)
        return model
    except Exception as exc:
        logger.error("Failed to load embedding model %s on %s: %s", model_name, device, exc)
        return None


def embedding_model_status(
    model_name: str = DEFAULT_MODEL,
    *,
    device: str = "cpu",
) -> tuple[bool, str | None]:
    """Return whether the embedding model can load; second value is a user-facing error."""
    if not model_name.strip():
        return False, "embedding model name is empty"
    model = _load_model(model_name, device=device)
    if model is not None:
        return True, None
    return (
        False,
        "Embedding model failed to load. Install NLP deps: "
        "pip install -r requirements-nlp.txt "
        f"(model={model_name}, device={device})",
    )


def ensure_embedding_model_available(
    model_name: str = DEFAULT_MODEL,
    *,
    device: str = "cpu",
) -> None:
    ok, error = embedding_model_status(model_name, device=device)
    if not ok:
        raise EmbeddingsUnavailableError(error or "embedding model unavailable")


def embed_text(
    text: str,
    *,
    model_name: str = DEFAULT_MODEL,
    device: str = "cpu",
) -> list[float] | None:
    if not text.strip():
        return None
    model = _load_model(model_name, device=device)
    if model is None:
        return None
    try:
        vector = model.encode(text[:4000], normalize_embeddings=True)
        return [float(x) for x in vector.tolist()]
    except Exception as exc:
        logger.debug("embedding failed: %s", exc)
        return None


def embed_texts_batch(
    texts: list[str],
    *,
    model_name: str = DEFAULT_MODEL,
    device: str = "cpu",
    batch_size: int = 32,
) -> list[list[float] | None]:
    model = _load_model(model_name, device=device)
    if model is None:
        return [None for _ in texts]
    snippets = [text[:4000] if text and text.strip() else "" for text in texts]
    try:
        vectors = model.encode(
            snippets,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        output: list[list[float] | None] = []
        for snippet, vector in zip(snippets, vectors):
            if not snippet:
                output.append(None)
            else:
                output.append([float(x) for x in vector.tolist()])
        return output
    except Exception as exc:
        logger.debug("batch embedding failed: %s", exc)
        return [embed_text(text, model_name=model_name, device=device) for text in texts]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def vector_to_json(vector: list[float]) -> str:
    return json.dumps(vector, separators=(",", ":"))


def vector_from_json(payload: str) -> list[float]:
    return [float(x) for x in json.loads(payload)]


def make_embed_fn(
    model_name: str = DEFAULT_MODEL,
    *,
    device: str = "cpu",
) -> Callable[[str], list[float] | None] | None:
    model = _load_model(model_name, device=device)
    if model is None:
        return None

    def _fn(text: str) -> list[float] | None:
        return embed_text(text, model_name=model_name, device=device)

    return _fn
