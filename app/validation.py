"""Query-parameter parsing that answers 400 instead of crashing with a 500."""

from __future__ import annotations

import math
import re
from typing import Any

from flask import Flask, jsonify, request


class ParamError(ValueError):
    """Invalid request parameter; rendered as HTTP 400."""


def int_arg(name: str, default: int, *, min_value: int, max_value: int) -> int:
    """Integer query arg clamped to [min_value, max_value] (negative limits never reach SQL)."""
    raw = request.args.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ParamError(f"{name} must be an integer") from exc
    return max(min_value, min(max_value, value))


def float_arg(name: str, default: float | None, *, min_value: float | None = None, max_value: float | None = None) -> float | None:
    """Finite float query arg (rejects nan/inf), optionally clamped."""
    raw = request.args.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ParamError(f"{name} must be a number") from exc
    if not math.isfinite(value):
        raise ParamError(f"{name} must be a finite number")
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
MAX_TICKERS = 500


def json_object() -> dict[str, Any]:
    """Request JSON body as a dict; an absent body is {}, any other JSON type is a 400."""
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ParamError("Request body must be a JSON object")
    return body


def normalize_ticker(value: Any, *, field: str = "ticker") -> str:
    if not isinstance(value, str):
        raise ParamError(f"{field} must be a string")
    ticker = value.strip().upper()
    if not TICKER_RE.match(ticker):
        raise ParamError(f"{field} is not a valid ticker symbol")
    return ticker


def normalize_ticker_list(value: Any, *, field: str = "tickers") -> list[str]:
    """Validated, upper-cased, de-duplicated tickers (order kept). Strings are not iterated."""
    if not isinstance(value, list):
        raise ParamError(f"{field} must be an array of ticker strings")
    if len(value) > MAX_TICKERS:
        raise ParamError(f"{field} supports at most {MAX_TICKERS} tickers")
    seen: dict[str, None] = {}
    for item in value:
        if isinstance(item, str) and not item.strip():
            continue
        seen.setdefault(normalize_ticker(item, field=field), None)
    return list(seen)


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ParamError)
    def _param_error(exc: ParamError):
        return jsonify({"error": str(exc)}), 400
