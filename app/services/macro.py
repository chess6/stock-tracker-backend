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
]


@dataclass
class MacroSnapshotService:
    def snapshot(self) -> dict:
        if yf is None:
            return {"items": [], "meta": {"source": "disabled", "reason": "yfinance unavailable"}}
        items = []
        for entry in MACRO_SYMBOLS:
            item = self._quote(entry)
            if item:
                items.append(item)
        return {"items": items, "meta": {"source": "yfinance"}}

    def _quote(self, entry: dict) -> dict | None:
        try:
            history = yf.Ticker(entry["symbol"]).history(period="5d", auto_adjust=False)
        except Exception:
            return None
        if history is None or history.empty or len(history) < 2:
            return None
        latest = history.iloc[-1]
        previous = history.iloc[-2]
        close = float(latest["Close"])
        prev_close = float(previous["Close"])
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
        }
