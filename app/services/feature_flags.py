"""Feature flags for experimental scoring, embeddings, and orchestration paths.

Resolution order: ENV (`STOCK_TRACKER_FF_<KEY>`) → SQLite `app_config` → default (False).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..repositories import Repository

FLAG_DEFAULTS: dict[str, bool] = {
    "experimental_composite_rank": False,
    "experimental_research_composite_rank": False,
    "embedding_heavy_retag": False,
    "experimental_signal_ranking": False,
}

KNOWN_FLAGS = frozenset(FLAG_DEFAULTS)


def _env_override(key: str) -> bool | None:
    env_key = f"STOCK_TRACKER_FF_{key.upper()}"
    raw = os.getenv(env_key)
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


def is_enabled(key: str, repo: Repository | None = None) -> bool:
    if key not in KNOWN_FLAGS:
        return False
    env_val = _env_override(key)
    if env_val is not None:
        return env_val
    if repo is not None:
        stored = repo.get_config(key)
        if stored is not None:
            return bool(stored)
    return FLAG_DEFAULTS[key]


def resolve_flags(repo: Repository | None = None) -> dict[str, bool]:
    return {key: is_enabled(key, repo) for key in sorted(KNOWN_FLAGS)}


def embeddings_default_enabled(repo: Repository | None = None) -> bool:
    """Default for worker/admin embedding-heavy paths when not explicitly set."""
    return is_enabled("embedding_heavy_retag", repo)
