"""Curated ticker universes for admin bulk refresh (free static lists, no paid APIs)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_UNIVERSE_FILES = {
    "sp500": _DATA_DIR / "sp500_constituents.json",
}


@lru_cache(maxsize=8)
def _load_universe_file(universe_id: str) -> dict | None:
    path = _UNIVERSE_FILES.get(universe_id)
    if not path or not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        return None
    return payload


def list_universes() -> list[dict]:
    """Summaries for admin UI (no full ticker arrays)."""
    summaries: list[dict] = []
    for universe_id in sorted(_UNIVERSE_FILES):
        payload = _load_universe_file(universe_id)
        if not payload:
            continue
        summaries.append(
            {
                "id": payload.get("id") or universe_id,
                "label": payload.get("label") or universe_id.upper(),
                "description": payload.get("description"),
                "count": payload.get("count") or len(payload.get("tickers") or []),
                "updatedAt": payload.get("updatedAt"),
                "source": payload.get("source"),
            }
        )
    return summaries


def get_universe(universe_id: str) -> dict | None:
    payload = _load_universe_file((universe_id or "").strip().lower())
    if not payload:
        return None
    tickers = [str(t).strip().upper() for t in (payload.get("tickers") or []) if str(t).strip()]
    return {
        **payload,
        "id": payload.get("id") or universe_id,
        "count": len(tickers),
        "tickers": tickers,
    }


def get_universe_tickers(universe_id: str) -> list[str]:
    payload = get_universe(universe_id)
    if not payload:
        return []
    return list(payload["tickers"])


def chunk_tickers(tickers: list[str], *, chunk_size: int = 75) -> list[list[str]]:
    size = max(int(chunk_size), 1)
    return [tickers[i : i + size] for i in range(0, len(tickers), size)]
