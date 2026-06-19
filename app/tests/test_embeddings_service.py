from __future__ import annotations

import pytest

from app.services.embeddings_service import (
    EmbeddingsUnavailableError,
    embedding_model_status,
    ensure_embedding_model_available,
)


def test_ensure_embedding_model_available_raises_when_load_fails(monkeypatch):
    monkeypatch.setattr(
        "app.services.embeddings_service._load_model",
        lambda *_args, **_kwargs: None,
    )
    with pytest.raises(EmbeddingsUnavailableError, match="failed to load"):
        ensure_embedding_model_available()


def test_embedding_model_status_reports_error_when_load_fails(monkeypatch):
    monkeypatch.setattr(
        "app.services.embeddings_service._load_model",
        lambda *_args, **_kwargs: None,
    )
    ok, error = embedding_model_status()
    assert ok is False
    assert error
    assert "requirements-nlp.txt" in error
