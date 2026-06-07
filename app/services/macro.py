from __future__ import annotations

from dataclasses import dataclass

try:
    import yfinance as yf  # type: ignore
except ImportError:  # pragma: no cover
    yf = None


MACRO_SYMBOLS = [
    {"id": "spy", "label": "S&P 500", "symbol": "SPY", "group": "indices"},
    {"id": "qqq", "label": "NASDAQ 100", "symbol": "QQQ", "group": "indices"},
    {"id": "dia", "label": "Dow", "symbol": "DIA", "group": "indices"},
    {"id": "iwm", "label": "Russell 2000", "symbol": "IWM", "group": "indices"},
    {"id": "eem", "label": "Emerging Mkts", "symbol": "EEM", "group": "indices"},
    {"id": "gld", "label": "Gold", "symbol": "GLD", "group": "commodities"},
    {"id": "slv", "label": "Silver", "symbol": "SLV", "group": "commodities"},
    {"id": "uso", "label": "Oil", "symbol": "USO", "group": "commodities"},
    {"id": "tlt", "label": "20Y Treasuries", "symbol": "TLT", "group": "rates"},
    {"id": "hyg", "label": "High Yield", "symbol": "HYG", "group": "risk"},
    {"id": "xlk", "label": "Technology", "symbol": "XLK", "group": "industries"},
    {"id": "xlf", "label": "Financials", "symbol": "XLF", "group": "industries"},
    {"id": "xle", "label": "Energy", "symbol": "XLE", "group": "industries"},
    {"id": "xlv", "label": "Health Care", "symbol": "XLV", "group": "industries"},
    {"id": "xli", "label": "Industrials", "symbol": "XLI", "group": "industries"},
    {"id": "xlp", "label": "Consumer Staples", "symbol": "XLP", "group": "industries"},
    {"id": "xly", "label": "Consumer Disc.", "symbol": "XLY", "group": "industries"},
    {"id": "xlu", "label": "Utilities", "symbol": "XLU", "group": "industries"},
    {"id": "xlre", "label": "Real Estate", "symbol": "XLRE", "group": "industries"},
    {"id": "xlc", "label": "Communication", "symbol": "XLC", "group": "industries"},
    {"id": "vix", "label": "VIX", "symbol": "^VIX", "group": "risk"},
]


def _empty_item(entry: dict) -> dict:
    return {
        "id": entry["id"],
        "label": entry["label"],
        "symbol": entry["symbol"],
        "group": entry["group"],
        "price": None,
        "changePct": None,
        "available": False,
    }


def _item_from_closes(entry: dict, close: float, prev_close: float) -> dict:
    if prev_close == 0:
        change_pct = None
    else:
        change_pct = ((close - prev_close) / prev_close) * 100
    return {
        "id": entry["id"],
        "label": entry["label"],
        "symbol": entry["symbol"],
        "group": entry["group"],
        "price": close,
        "changePct": change_pct,
        "available": True,
    }


@dataclass
class MacroSnapshotService:
    def snapshot(self) -> dict:
        if yf is None:
            return {
                "items": [_empty_item(entry) for entry in MACRO_SYMBOLS],
                "meta": {"source": "disabled", "reason": "yfinance unavailable", "unavailable": len(MACRO_SYMBOLS)},
            }

        batch = self._batch_history()
        items = []
        unavailable = 0
        for entry in MACRO_SYMBOLS:
            item = self._quote_from_batch(entry, batch) or self._quote_single(entry)
            if not item:
                item = _empty_item(entry)
                unavailable += 1
            elif not item.get("available"):
                unavailable += 1
            items.append(item)

        return {
            "items": items,
            "meta": {
                "source": "yfinance",
                "total": len(items),
                "unavailable": unavailable,
            },
        }

    def _batch_history(self):
        symbols = [entry["symbol"] for entry in MACRO_SYMBOLS]
        try:
            return yf.download(
                symbols,
                period="5d",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
            )
        except Exception:
            return None

    def _history_frame(self, batch, symbol: str):
        if batch is None or getattr(batch, "empty", True):
            return None
        if len(MACRO_SYMBOLS) == 1:
            return batch
        try:
            if symbol in batch.columns.get_level_values(0):
                frame = batch[symbol]
                return frame if not getattr(frame, "empty", True) else None
        except Exception:
            pass
        if "Close" in batch.columns and symbol not in getattr(batch.columns, "names", []):
            return batch
        return None

    def _quote_from_batch(self, entry: dict, batch) -> dict | None:
        frame = self._history_frame(batch, entry["symbol"])
        if frame is None or frame.empty or len(frame) < 2:
            return None
        if "Close" not in frame.columns:
            return None
        close = float(frame["Close"].iloc[-1])
        prev_close = float(frame["Close"].iloc[-2])
        return _item_from_closes(entry, close, prev_close)

    def _quote_single(self, entry: dict) -> dict | None:
        symbol = entry["symbol"]
        try:
            ticker = yf.Ticker(symbol)
            history = ticker.history(period="5d", auto_adjust=False)
            if history is not None and not history.empty and len(history) >= 2:
                close = float(history["Close"].iloc[-1])
                prev_close = float(history["Close"].iloc[-2])
                return _item_from_closes(entry, close, prev_close)
            fast = getattr(ticker, "fast_info", None)
            if fast:
                last = getattr(fast, "last_price", None) or getattr(fast, "lastPrice", None)
                prev = getattr(fast, "previous_close", None) or getattr(fast, "previousClose", None)
                if last is not None and prev is not None:
                    return _item_from_closes(entry, float(last), float(prev))
        except Exception:
            return None
        return None
