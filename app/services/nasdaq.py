from __future__ import annotations

from datetime import datetime, timedelta

import requests


class NasdaqService:
    def __init__(self, api_key: str | None, timeout: int = 20, session: requests.Session | None = None) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()
        self.base_url = "https://data.nasdaq.com/api/v3/datatables/SHARADAR"

    def is_enabled(self) -> bool:
        return bool(self.api_key)

    def _get(self, table: str, params: dict) -> dict:
        if not self.api_key:
            raise RuntimeError("NASDAQ_API_KEY is not configured")
        full_params = {**params, "api_key": self.api_key}
        response = self.session.get(f"{self.base_url}/{table}", params=full_params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def fetch_daily_change(self, tickers: list[str]) -> dict:
        today = datetime.utcnow().date()
        ten_days_ago = today - timedelta(days=10)
        payload = self._get(
            "SEP",
            {
                "ticker": ",".join(tickers),
                "date.gte": ten_days_ago.strftime("%Y-%m-%d"),
            },
        )
        rows = payload.get("datatable", {}).get("data", [])
        columns = payload.get("datatable", {}).get("columns", [])
        col_idx = {col["name"]: idx for idx, col in enumerate(columns)}
        by_ticker: dict[str, list[tuple[datetime.date, list]]] = {}
        for row in rows:
            ticker = row[col_idx["ticker"]]
            row_date = datetime.strptime(row[col_idx["date"]][:10], "%Y-%m-%d").date()
            by_ticker.setdefault(ticker, []).append((row_date, row))

        changes = {}
        for ticker in tickers:
            entries = sorted(by_ticker.get(ticker, []), key=lambda pair: pair[0], reverse=True)
            today_close = entries[0][1][col_idx["close"]] if entries else None
            prev_close = entries[1][1][col_idx["close"]] if len(entries) > 1 else None
            changes[ticker] = {"prevClose": prev_close, "todayClose": today_close}
        return changes

    def fetch_price_history(self, ticker: str, days: int = 3650) -> list[dict]:
        start = (datetime.utcnow().date() - timedelta(days=days)).strftime("%Y-%m-%d")
        payload = self._get("SEP", {"ticker": ticker.upper(), "date.gte": start})
        rows = payload.get("datatable", {}).get("data", [])
        columns = payload.get("datatable", {}).get("columns", [])
        col_idx = {col["name"]: idx for idx, col in enumerate(columns)}
        output = []
        for row in rows:
            output.append(
                {
                    "date": row[col_idx["date"]],
                    "close": row[col_idx["close"]],
                    "open": row[col_idx.get("open")],
                    "high": row[col_idx.get("high")],
                    "low": row[col_idx.get("low")],
                    "volume": row[col_idx.get("volume")],
                }
            )
        return sorted(output, key=lambda item: item["date"])

    def fetch_top_quotes(self, tickers: list[str]) -> dict:
        history_map = {}
        for ticker in tickers:
            history = self.fetch_price_history(ticker, days=20)
            latest = history[-1] if history else {}
            previous = history[-2] if len(history) > 1 else {}
            history_map[ticker] = {
                "last": latest.get("close"),
                "prevClose": previous.get("close"),
                "open": latest.get("open"),
                "high": latest.get("high"),
                "low": latest.get("low"),
                "timestamp": latest.get("date"),
            }
        return history_map

    def fetch_insider_buying(self, tickers: list[str] | None = None) -> list[dict]:
        params = {
            "securityadcode": "NA,ND",
            "transactionvalue.gte": 100000,
            "qopts.columns": "ticker,issuername,filingdate,ownername,transactiondate,transactionvalue,securityadcode,transactioncode,securitytitle",
        }
        if tickers:
            params["ticker"] = ",".join(tickers)
        payload = self._get("SF2", params)
        rows = payload.get("datatable", {}).get("data", [])
        columns = payload.get("datatable", {}).get("columns", [])
        col_idx = {col["name"]: idx for idx, col in enumerate(columns)}

        totals = {}
        for row in rows:
            ticker = row[col_idx["ticker"]]
            transaction_code = row[col_idx["transactioncode"]]
            if transaction_code not in ("P", "S"):
                continue
            security_title = row[col_idx["securitytitle"]] or ""
            if "Preferred" in str(security_title):
                continue
            try:
                value = float(row[col_idx["transactionvalue"]] or 0)
            except (TypeError, ValueError):
                value = 0.0
            if value <= 0:
                continue
            if row[col_idx["securityadcode"]] == "ND":
                value = -value
            aggregate = totals.setdefault(
                ticker,
                {"ticker": ticker, "company": row[col_idx["issuername"]], "buy6m": 0.0, "buy3m": 0.0, "buy1m": 0.0, "owners6m": set()},
            )
            aggregate["buy6m"] += value
            if transaction_code == "P":
                aggregate["owners6m"].add(row[col_idx["ownername"]])
        output = []
        for item in totals.values():
            output.append({**item, "owners6m": len(item["owners6m"])})
        output.sort(key=lambda row: row["buy6m"], reverse=True)
        return output

    def fetch_sf2(self, ticker: str) -> dict:
        return self._get("SF2", {"ticker": ticker.upper()})
