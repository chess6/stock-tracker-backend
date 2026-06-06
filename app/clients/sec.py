from __future__ import annotations

import time
from collections.abc import Iterable
from urllib.parse import urlparse

import requests


class SecClient:
    def __init__(self, user_agent: str, base_url: str, timeout: int = 20, session: requests.Session | None = None) -> None:
        self.user_agent = user_agent
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self._last_request_at = 0.0

    def headers_for(self, url: str) -> dict:
        host = urlparse(url).netloc
        return {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Host": host,
        }

    def _throttle(self) -> None:
        now = time.monotonic()
        wait_for = 0.6 - (now - self._last_request_at)
        if wait_for > 0:
            time.sleep(wait_for)
        self._last_request_at = time.monotonic()

    def get_json(self, url: str) -> dict:
        self._throttle()
        response = self.session.get(url, headers=self.headers_for(url), timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_text(self, url: str) -> str:
        self._throttle()
        response = self.session.get(url, headers=self.headers_for(url), timeout=self.timeout)
        response.raise_for_status()
        return response.text

    def fetch_company_tickers(self, url: str) -> list[dict]:
        data = self.get_json(url)
        rows: Iterable
        if isinstance(data, dict):
            rows = data.values()
        else:
            rows = data
        companies = []
        for item in rows:
            cik = str(item.get("cik_str") or item.get("cik") or "").strip()
            ticker = str(item.get("ticker") or "").strip().upper()
            name = str(item.get("title") or item.get("name") or "").strip()
            if not ticker or not name:
                continue
            padded_cik = cik.zfill(10) if cik else None
            companies.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "cik": padded_cik,
                    "sec_filings_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={padded_cik}" if padded_cik else None,
                    "source": "sec",
                }
            )
        return companies

    def fetch_company_facts(self, cik: str) -> dict:
        url = f"{self.base_url}/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json"
        return self.get_json(url)

    def fetch_submissions(self, cik: str) -> dict:
        url = f"{self.base_url}/submissions/CIK{cik.zfill(10)}.json"
        return self.get_json(url)

    def form4_document_url(self, cik: str, accession: str, primary_document: str) -> str:
        cik_numeric = str(int(cik))
        accession_path = accession.replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{cik_numeric}/{accession_path}/{primary_document}"
