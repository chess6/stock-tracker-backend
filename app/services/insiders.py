from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from ..clients.sec import SecClient
from ..repositories import Repository

logger = logging.getLogger("stock_tracker.pipeline.insiders")


class InsidersService:
    def __init__(
        self,
        repo: Repository,
        sec_client: SecClient,
        max_filings_per_company: int = 40,
        request_delay_seconds: float = 0.15,
        max_total_requests: int = 400,
    ) -> None:
        self.repo = repo
        self.sec_client = sec_client
        self.max_filings_per_company = max_filings_per_company
        self.request_delay_seconds = request_delay_seconds
        self.max_total_requests = max_total_requests

    def refresh_insiders(self, tickers: list[str], *, max_filings_per_company: int | None = None) -> dict:
        filing_limit = (
            max_filings_per_company
            if max_filings_per_company is not None
            else self.max_filings_per_company
        )
        logger.info("refresh_insiders start tickers=%d max_filings=%d", len(tickers), filing_limit)
        t0 = time.monotonic()
        refreshed = []
        records_written = 0
        total_sec_requests = 0
        for ticker in [item.upper() for item in tickers if item]:
            if total_sec_requests >= self.max_total_requests:
                logger.warning(
                    "refresh_insiders stopping early — total SEC requests reached %d (limit %d)",
                    total_sec_requests, self.max_total_requests,
                )
                break
            company = self.repo.get_company_by_ticker(ticker)
            if not company or not company.get("cik"):
                logger.debug("refresh_insiders skip ticker=%s (no CIK)", ticker)
                continue
            transactions, requests_made = self._fetch_form4_transactions(
                company["cik"],
                filing_limit=filing_limit,
                remaining_budget=self.max_total_requests - total_sec_requests,
            )
            total_sec_requests += requests_made
            if transactions:
                records_written += self.repo.upsert_insider_transactions(company["id"], transactions)
                logger.debug("refresh_insiders ticker=%s transactions=%d", ticker, len(transactions))
                refreshed.append(ticker)
        if total_sec_requests > 200:
            logger.warning(
                "refresh_insiders high request count: %d SEC requests across %d tickers",
                total_sec_requests, len(refreshed),
            )
        elapsed = time.monotonic() - t0
        logger.info("refresh_insiders done tickers=%d records=%d requests=%d elapsed=%.1fs",
                    len(refreshed), records_written, total_sec_requests, elapsed)
        return {"tickers": refreshed, "recordsWritten": records_written}

    def _fetch_form4_transactions(
        self, cik: str, *, filing_limit: int | None = None, remaining_budget: int | None = None,
    ) -> tuple[list[dict], int]:
        """Returns (transactions, sec_requests_made)."""
        max_filings = filing_limit if filing_limit is not None else self.max_filings_per_company
        submissions = self.sec_client.fetch_submissions(cik)
        sec_requests = 1  # the submissions fetch above
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])
        transactions = []
        filings_checked = 0
        for idx, form in enumerate(forms):
            if form not in {"4", "4/A"}:
                continue
            if filings_checked >= max_filings:
                break
            if remaining_budget is not None and sec_requests >= remaining_budget:
                logger.debug("_fetch_form4_transactions budget exhausted cik=%s after %d requests", cik, sec_requests)
                break
            accession = accessions[idx]
            filing_date = filing_dates[idx]
            primary_document = primary_docs[idx]
            url = self.sec_client.form4_document_url(cik, accession, primary_document)
            try:
                xml_text = self.sec_client.get_text(url)
            except Exception:
                sec_requests += 1
                filings_checked += 1
                continue
            sec_requests += 1
            transactions.extend(parse_form4_xml(xml_text, filing_date, accession))
            filings_checked += 1
            if self.request_delay_seconds > 0:
                time.sleep(self.request_delay_seconds)
        return transactions, sec_requests

    def buying_sums(self, tickers: list[str] | None = None, min_buy6m: float | None = None) -> list[dict]:
        return self.repo.fetch_insider_buying_sums(tickers, min_buy6m=min_buy6m)

    def sf2_payload(self, ticker: str) -> dict:
        rows = self.repo.fetch_insider_transactions(ticker)
        columns = [
            {"name": "ticker", "type": "text"},
            {"name": "issuername", "type": "text"},
            {"name": "filingdate", "type": "Date"},
            {"name": "ownername", "type": "text"},
            {"name": "transactiondate", "type": "Date"},
            {"name": "transactionvalue", "type": "double"},
            {"name": "securityadcode", "type": "text"},
            {"name": "transactioncode", "type": "text"},
            {"name": "securitytitle", "type": "text"},
        ]
        data = [
            [
                row["ticker"],
                row["company_name"],
                row["filing_date"],
                row["owner_name"],
                row["transaction_date"],
                row["transaction_value"],
                row["security_ad_code"],
                row["transaction_code"],
                row["security_title"],
            ]
            for row in rows
        ]
        return {"datatable": {"columns": columns, "data": data}, "meta": {"source": "sec_edgar"}}


def parse_form4_xml(xml_text: str, filing_date: str, accession: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.debug("parse_form4_xml ParseError accession=%s len=%d", accession, len(xml_text))
        return []

    root_tag = root.tag.rsplit("}", 1)[-1] if "}" in root.tag else root.tag
    if root_tag.lower() in ("html", "document"):
        logger.debug("parse_form4_xml got HTML instead of XML accession=%s", accession)
        return []

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    owner_name = _text(root, ".//{*}reportingOwner/{*}reportingOwnerId/{*}rptOwnerName")
    transactions = []
    for node in root.iter():
        if local(node.tag) != "nonDerivativeTransaction":
            continue
        code = _text(node, ".//{*}transactionCoding/{*}transactionCode")
        if not code:
            continue
        security_title = _text(node, ".//{*}securityTitle/{*}value")
        if security_title and "preferred" in security_title.lower():
            continue
        shares = _float(_text(node, ".//{*}transactionAmounts/{*}transactionShares/{*}value"))
        price = _float(_text(node, ".//{*}transactionAmounts/{*}transactionPricePerShare/{*}value"))
        transaction_date = _text(node, ".//{*}transactionDate/{*}value")
        value = shares * price if shares is not None and price is not None else None
        if code in ("S", "D", "F"):
            ad_code = "ND"
        else:
            ad_code = "NA"
        transactions.append(
            {
                "filing_date": filing_date,
                "transaction_date": transaction_date,
                "owner_name": owner_name,
                "transaction_code": code,
                "shares": shares,
                "price_per_share": price,
                "transaction_value": value,
                "security_title": security_title,
                "form": "4",
                "accession": accession,
                "security_ad_code": ad_code,
            }
        )
    return transactions


def _text(node: ET.Element, path: str) -> str | None:
    found = node.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip()


def _float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(re.sub(r"[^0-9.\-]", "", value))
    except (TypeError, ValueError):
        return None
