from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..repositories import Repository

logger = logging.getLogger("stock_tracker.pipeline.macro")

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

MACRO_TICKERS = [entry["symbol"] for entry in MACRO_SYMBOLS if not entry["symbol"].startswith("^")]


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
    repo: Repository | None = field(default=None)
    prices_service: object | None = field(default=None)

    def refresh_macro_prices(self) -> dict:
        from .prices import PricesService

        svc: PricesService = self.prices_service  # type: ignore[assignment]
        if svc is None:
            if self.repo is None:
                return {"error": "no repository configured"}
            svc = PricesService(self.repo)
        logger.info("refresh_macro_prices start tickers=%d", len(MACRO_TICKERS))
        return svc.refresh_prices(MACRO_TICKERS)

    def snapshot(self) -> dict:
        items_by_id: dict[str, dict] = {}
        sqlite_hits = 0

        if self.repo is not None:
            price_batch = self.repo.fetch_prices_batch(MACRO_TICKERS, limit_per_ticker=2)
            for entry in MACRO_SYMBOLS:
                item = self._item_from_sqlite(entry, price_batch)
                if item.get("available"):
                    items_by_id[entry["id"]] = item
                    sqlite_hits += 1

        missing = [entry for entry in MACRO_SYMBOLS if entry["id"] not in items_by_id]
        yfinance_hits = 0
        if missing and yf is not None:
            symbols = [entry["symbol"] for entry in missing]
            batch = self._batch_history(symbols)
            for entry in missing:
                item = self._quote_from_batch(entry, batch, len(symbols)) or self._quote_single(entry)
                if item and item.get("available"):
                    items_by_id[entry["id"]] = item
                    yfinance_hits += 1

        items = []
        unavailable = 0
        for entry in MACRO_SYMBOLS:
            item = items_by_id.get(entry["id"]) or _empty_item(entry)
            if not item.get("available"):
                unavailable += 1
            items.append(item)

        if sqlite_hits == len(MACRO_SYMBOLS):
            source = "sqlite"
        elif sqlite_hits and yfinance_hits:
            source = "sqlite+yfinance"
        elif sqlite_hits:
            source = "sqlite"
        elif yfinance_hits:
            source = "yfinance"
        elif yf is None:
            source = "disabled"
        else:
            source = "unavailable"

        return {
            "items": items,
            "meta": {
                "source": source,
                "total": len(items),
                "unavailable": unavailable,
                "sqliteHits": sqlite_hits,
                "yfinanceHits": yfinance_hits,
            },
        }

    def _item_from_sqlite(self, entry: dict, batch: dict) -> dict:
        symbol = entry["symbol"]
        if symbol.startswith("^"):
            return _empty_item(entry)
        rows = batch.get(symbol.upper(), [])
        if len(rows) >= 2 and rows[0].get("close") is not None and rows[1].get("close") is not None:
            return _item_from_closes(entry, rows[0]["close"], rows[1]["close"])
        if rows and rows[0].get("close") is not None:
            item = _item_from_closes(entry, rows[0]["close"], rows[0]["close"])
            item["changePct"] = None
            return item
        return _empty_item(entry)

    def _batch_history(self, symbols: list[str] | None = None):
        if yf is None:
            return None
        symbols = symbols or [entry["symbol"] for entry in MACRO_SYMBOLS]
        if not symbols:
            return None
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
            logger.exception("macro batch yfinance download failed symbols=%d", len(symbols))
            return None

    def _history_frame(self, batch, symbol: str, symbol_count: int | None = None):
        if batch is None or getattr(batch, "empty", True):
            return None
        count = symbol_count if symbol_count is not None else len(MACRO_SYMBOLS)
        if count == 1:
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

    def _quote_from_batch(self, entry: dict, batch, symbol_count: int | None = None) -> dict | None:
        frame = self._history_frame(batch, entry["symbol"], symbol_count=symbol_count)
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
