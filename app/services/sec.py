from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable

import requests


SEC_METRIC_CONFIG = {
    "revenue": {
        "taxonomy": "us-gaap",
        "concepts": [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "Revenues",
        ],
        "units": ("USD",),
    },
    "eps": {
        "taxonomy": "us-gaap",
        "concepts": [
            "EarningsPerShareDiluted",
            "EarningsPerShareBasicAndDiluted",
            "EarningsPerShareBasic",
        ],
        "units": ("USD/shares", "USD"),
    },
    "assets": {
        "taxonomy": "us-gaap",
        "concepts": ["Assets"],
        "units": ("USD",),
    },
    "liabilities": {
        "taxonomy": "us-gaap",
        "concepts": ["Liabilities"],
        "units": ("USD",),
    },
    "cashneq": {
        "taxonomy": "us-gaap",
        "concepts": [
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ],
        "units": ("USD",),
    },
    "ncfo": {
        "taxonomy": "us-gaap",
        "concepts": [
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ],
        "units": ("USD",),
    },
    "capex": {
        "taxonomy": "us-gaap",
        "concepts": ["PaymentsToAcquirePropertyPlantAndEquipment"],
        "units": ("USD",),
    },
    "sharesbas": {
        "taxonomy": "dei",
        "concepts": [
            "EntityCommonStockSharesOutstanding",
            "EntityPublicFloat",
        ],
        "units": ("shares",),
    },
    "netinc": {
        "taxonomy": "us-gaap",
        "concepts": ["NetIncomeLoss"],
        "units": ("USD",),
    },
}


class SecClient:
    def __init__(self, user_agent: str, base_url: str, timeout: int = 20, session: requests.Session | None = None) -> None:
        self.user_agent = user_agent
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self._last_request_at = 0.0

    @property
    def headers(self) -> dict:
        return {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov",
        }

    def _throttle(self) -> None:
        now = time.monotonic()
        wait_for = 0.6 - (now - self._last_request_at)
        if wait_for > 0:
            time.sleep(wait_for)
        self._last_request_at = time.monotonic()

    def get_json(self, url: str) -> dict:
        self._throttle()
        response = self.session.get(url, headers=self.headers, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

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


def infer_dimension(form: str | None, fp: str | None) -> tuple[str | None, str | None]:
    normalized_form = (form or "").upper()
    normalized_fp = (fp or "").upper()
    if normalized_form in {"10-K", "20-F", "40-F"}:
        return "annual", "ARY"
    if normalized_form in {"10-Q", "10-Q/A", "6-K"}:
        return "quarterly", "ARQ"
    if normalized_fp == "FY":
        return "annual", "ARY"
    if normalized_fp.startswith("Q"):
        return "quarterly", "ARQ"
    return None, None


def normalize_company_facts(company_id: int, payload: dict) -> list[dict]:
    facts = payload.get("facts", {})
    results_by_key: dict[tuple, dict] = {}

    for metric, config in SEC_METRIC_CONFIG.items():
        taxonomy_facts = facts.get(config["taxonomy"], {})
        for concept in config["concepts"]:
            concept_payload = taxonomy_facts.get(concept)
            if not concept_payload:
                continue
            units = concept_payload.get("units", {})
            for unit_name, observations in units.items():
                if config["units"] and unit_name not in config["units"]:
                    continue
                for observation in observations:
                    period_type, dimension = infer_dimension(observation.get("form"), observation.get("fp"))
                    if not period_type or not observation.get("end"):
                        continue
                    value = observation.get("val")
                    if value is None:
                        continue
                    key = (
                        metric,
                        observation.get("end"),
                        dimension,
                        observation.get("filed"),
                        observation.get("accn"),
                    )
                    results_by_key.setdefault(
                        key,
                        {
                            "company_id": company_id,
                            "metric": metric,
                            "value": value,
                            "unit": unit_name,
                            "period_end": observation.get("end"),
                            "period_type": period_type,
                            "dimension": dimension,
                            "fiscal_year": observation.get("fy"),
                            "fiscal_quarter": observation.get("fp"),
                            "filing_date": observation.get("filed"),
                            "form": observation.get("form"),
                            "accession": observation.get("accn"),
                            "source": "sec_companyfacts",
                            "taxonomy": config["taxonomy"],
                            "xbrl_concept": concept,
                        },
                    )

    derived_records: list[dict] = list(results_by_key.values())
    index: dict[tuple[int, str, str, str], dict] = {}
    for record in derived_records:
        metric_key = (record["company_id"], record["dimension"], record["period_end"], record["metric"])
        index[metric_key] = record

    derived_fcf = []
    for record in derived_records:
        if record["metric"] != "ncfo":
            continue
        capex = index.get((record["company_id"], record["dimension"], record["period_end"], "capex"))
        if not capex:
            continue
        derived_key = (record["company_id"], record["dimension"], record["period_end"], "fcf")
        if derived_key in index:
            continue
        derived_fcf.append(
            {
                **record,
                "metric": "fcf",
                "value": float(record["value"]) - float(capex["value"]),
                "xbrl_concept": "derived_fcf",
                "source": "sec_companyfacts_derived",
            }
        )
    return derived_records + derived_fcf


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
