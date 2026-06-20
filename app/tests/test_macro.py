from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.services.macro import MACRO_SYMBOLS, MacroSnapshotService


def test_snapshot_backfills_missing_symbols_from_yfinance():
    repo = MagicMock()
    repo.fetch_prices_batch.return_value = {
        "QQQ": [
            {"close": 450.0, "date": "2026-06-05"},
            {"close": 440.0, "date": "2026-06-04"},
        ],
    }

    with patch("app.services.macro.yf") as mock_yf:
        mock_yf.download.return_value = None
        service = MacroSnapshotService(repo=repo)
        with patch.object(service, "_quote_single", return_value={
            "id": "spy",
            "label": "S&P 500",
            "symbol": "SPY",
            "group": "indices",
            "price": 530.0,
            "changePct": 1.0,
            "available": True,
        }):
            payload = service.snapshot()

    qqq = next(item for item in payload["items"] if item["symbol"] == "QQQ")
    spy = next(item for item in payload["items"] if item["symbol"] == "SPY")
    assert qqq["available"] is True
    assert qqq["price"] == 450.0
    assert spy["available"] is True
    assert payload["meta"]["source"] == "sqlite+yfinance"
    assert payload["meta"]["sqliteHits"] == 1
    assert payload["meta"]["yfinanceHits"] >= 1
    assert payload["meta"]["unavailable"] < len(MACRO_SYMBOLS)


def test_snapshot_serializes_without_nan():
    repo = MagicMock()
    repo.fetch_prices_batch.return_value = {}

    with patch("app.services.macro.yf") as mock_yf:
        mock_yf.download.return_value = None
        service = MacroSnapshotService(repo=repo)
        with patch.object(
            service,
            "_quote_single",
            side_effect=lambda entry: {
                "id": entry["id"],
                "label": entry["label"],
                "symbol": entry["symbol"],
                "group": entry["group"],
                "price": float("nan"),
                "changePct": float("nan"),
                "available": True,
            },
        ):
            payload = service.snapshot()

    json.dumps(payload, allow_nan=False)
    vix = next(item for item in payload["items"] if item["symbol"] == "^VIX")
    assert vix["price"] is None
    assert vix["changePct"] is None
    assert vix["available"] is False
