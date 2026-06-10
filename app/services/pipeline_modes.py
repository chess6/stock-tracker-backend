"""Pipeline refresh mode parsing and ticker selection (Phase G3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .freshness import DEFAULT_STALE_FUNDAMENTALS_DAYS, DEFAULT_STALE_PRICES_DAYS

if TYPE_CHECKING:
    from ..repositories import Repository

PIPELINE_MODES = frozenset(
    {
        "force_refresh",
        "refresh_missing_only",
        "refresh_stale_only",
        "recompute_scores_only",
        "refresh_prices_only",
        "lightweight_daily_refresh",
        "full_rebuild",
    }
)

DEFAULT_PIPELINE_MODE = "force_refresh"

DEFAULT_PIPELINE_TICKERS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMD",
    "GOOGL",
    "AMZN",
    "META",
    "TSLA",
]


class UnknownPipelineModeError(ValueError):
    pass


def normalize_mode(value: str | None) -> str:
    mode = (value or DEFAULT_PIPELINE_MODE).strip().lower().replace("-", "_")
    if mode not in PIPELINE_MODES:
        raise UnknownPipelineModeError(f"Unknown pipeline mode: {value}")
    return mode


def _intersect_candidates(selected: list[str], candidates: list[str] | None) -> list[str]:
    if not candidates:
        return selected
    allowed = {item.upper() for item in candidates if item}
    return [item for item in selected if item.upper() in allowed]


def resolve_fundamentals_tickers(
    repo: Repository,
    mode: str,
    candidates: list[str] | None,
    *,
    stale_after_days: int = DEFAULT_STALE_FUNDAMENTALS_DAYS,
) -> list[str]:
    mode = normalize_mode(mode)
    if mode == "recompute_scores_only":
        return []
    if mode == "refresh_prices_only":
        return []
    if mode in {"force_refresh", "full_rebuild"}:
        if not candidates:
            if mode == "full_rebuild":
                return list(DEFAULT_PIPELINE_TICKERS)
            return []
        return [item.upper() for item in candidates if item]
    if mode == "refresh_missing_only":
        missing = repo.fetch_tickers_without_fundamentals(candidates)
        return missing
    if mode == "refresh_stale_only":
        return _intersect_candidates(
            repo.fetch_stale_fundamentals_tickers(stale_after_days),
            candidates,
        )
    if mode == "lightweight_daily_refresh":
        return _intersect_candidates(
            repo.fetch_stale_fundamentals_tickers(stale_after_days),
            candidates,
        )
    return []


def resolve_prices_tickers(
    repo: Repository,
    mode: str,
    candidates: list[str] | None,
    *,
    stale_after_days: int = DEFAULT_STALE_PRICES_DAYS,
) -> list[str]:
    mode = normalize_mode(mode)
    if mode == "recompute_scores_only":
        return []
    if mode in {"force_refresh", "full_rebuild"}:
        if not candidates:
            if mode == "full_rebuild":
                return list(DEFAULT_PIPELINE_TICKERS)
            return []
        return [item.upper() for item in candidates if item]
    if mode == "refresh_prices_only":
        if candidates:
            return [item.upper() for item in candidates if item]
        stale = repo.fetch_stale_prices_tickers(stale_after_days)
        missing = repo.fetch_tickers_without_prices(None)
        return sorted({*stale, *missing})
    if mode == "refresh_missing_only":
        return repo.fetch_tickers_without_prices(candidates)
    if mode in {"refresh_stale_only", "lightweight_daily_refresh"}:
        stale = repo.fetch_stale_prices_tickers(stale_after_days)
        if mode == "lightweight_daily_refresh" and candidates:
            stale = sorted({*stale, *[item.upper() for item in candidates if item]})
        return _intersect_candidates(stale, candidates) if mode == "refresh_stale_only" else stale
    return []


def resolve_scores_tickers(
    repo: Repository,
    mode: str,
    candidates: list[str] | None,
) -> list[str]:
    mode = normalize_mode(mode)
    if mode == "recompute_scores_only":
        tickers = repo.fetch_tickers_with_fundamentals(dimension="ARY")
        return _intersect_candidates(tickers, candidates)
    if mode == "full_rebuild":
        return repo.fetch_tickers_with_fundamentals(dimension="ARY")
    return []


def mode_requires_tickers(mode: str) -> bool:
    return normalize_mode(mode) == "force_refresh"
