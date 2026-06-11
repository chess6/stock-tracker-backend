"""Phase 4 critical EDGAR ingestion — 8-K events, audit flags, insider ownership, 13D."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import requests

from ..clients.sec import SecClient
from ..repositories import Repository
from .edgar_parsers import (
    aggregate_insider_ownership_pct,
    detect_going_concern,
    is_activist_filing,
    is_nt_form,
    map_8k_item_to_event_type,
    parse_8k_items,
    parse_form3_initial_holdings,
    parse_form4_post_holdings,
)
from .fundamentals import fetch_resolved_wide_rows, resolve_financial_dimension
from .sec_eligibility import should_skip_sec_fundamentals

logger = logging.getLogger("stock_tracker.pipeline.edgar")


class EdgarIngestionService:
    def __init__(
        self,
        repo: Repository,
        sec_client: SecClient,
        *,
        max_filings_per_company: int = 60,
        request_delay_seconds: float = 0.15,
        max_total_requests: int = 500,
    ) -> None:
        self.repo = repo
        self.sec_client = sec_client
        self.max_filings_per_company = max_filings_per_company
        self.request_delay_seconds = request_delay_seconds
        self.max_total_requests = max_total_requests

    def refresh_edgar_events(self, tickers: list[str]) -> dict:
        logger.info("refresh_edgar_events start tickers=%d", len(tickers))
        t0 = time.monotonic()
        refreshed: list[str] = []
        skipped: list[dict] = []
        errors: list[dict] = []
        events_written = 0
        flags_written = 0
        ownership_written = 0
        activist_written = 0
        total_requests = 0

        for ticker in [t.upper() for t in tickers if t]:
            if total_requests >= self.max_total_requests:
                break
            company = self.repo.get_company_by_ticker(ticker)
            if not company or not company.get("cik"):
                skipped.append({"ticker": ticker, "reason": "no_cik"})
                continue
            if should_skip_sec_fundamentals(company):
                skipped.append({"ticker": ticker, "reason": "non_operating_issuer"})
                continue
            try:
                result, requests_made = self._ingest_company(company)
            except requests.RequestException as exc:
                errors.append({"ticker": ticker, "reason": "sec_request_error", "message": str(exc)})
                continue
            total_requests += requests_made
            events_written += result.get("events", 0)
            flags_written += result.get("flags", 0)
            ownership_written += result.get("ownership", 0)
            activist_written += result.get("activist", 0)
            refreshed.append(ticker)

        elapsed = time.monotonic() - t0
        logger.info(
            "refresh_edgar_events done tickers=%d events=%d flags=%d ownership=%d activist=%d requests=%d elapsed=%.1fs",
            len(refreshed),
            events_written,
            flags_written,
            ownership_written,
            activist_written,
            total_requests,
            elapsed,
        )
        return {
            "tickers": refreshed,
            "eventsWritten": events_written,
            "flagsWritten": flags_written,
            "ownershipWritten": ownership_written,
            "activistWritten": activist_written,
            "skipped": skipped,
            "errors": errors,
        }

    def _ingest_company(self, company: dict) -> tuple[dict, int]:
        cik = company["cik"]
        company_id = company["id"]
        ticker = company["ticker"]
        submissions = self.sec_client.fetch_submissions(cik)
        requests_made = 1
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])

        cutoff = (datetime.utcnow() - timedelta(days=730)).strftime("%Y-%m-%d")
        events: list[dict] = []
        flags: list[dict] = []
        activist: list[dict] = []
        holdings: list[dict] = []
        filings_checked = 0
        request_budget = self.max_total_requests

        for idx, form in enumerate(forms):
            if filings_checked >= self.max_filings_per_company:
                break
            if requests_made >= request_budget:
                break
            filing_date = filing_dates[idx] if idx < len(filing_dates) else None
            if filing_date and filing_date < cutoff:
                continue

            accession = accessions[idx]
            primary_document = primary_docs[idx] if idx < len(primary_docs) else None
            form_upper = (form or "").upper()

            if is_nt_form(form):
                flags.append(
                    {
                        "flag_type": "nt_filing",
                        "filed_date": filing_date,
                        "accession": accession,
                        "details": form,
                        "active": 1,
                    }
                )

            if is_activist_filing(form):
                activist.append(
                    {
                        "filed_date": filing_date,
                        "form_type": form,
                        "accession": accession,
                        "filer_name": None,
                        "ownership_pct": None,
                        "summary": f"{form} filed",
                    }
                )

            if form_upper.startswith("8-K") and primary_document:
                url = self._document_url(cik, accession, primary_document)
                try:
                    text = self.sec_client.get_text(url)
                    requests_made += 1
                    filings_checked += 1
                except Exception:
                    continue
                for item in parse_8k_items(text):
                    events.append(
                        {
                            "form_type": form,
                            "item_number": item,
                            "filed_date": filing_date,
                            "event_type": map_8k_item_to_event_type(item),
                            "summary": f"Item {item} ({map_8k_item_to_event_type(item)})",
                            "accession": accession,
                        }
                    )
                if self.request_delay_seconds > 0:
                    time.sleep(self.request_delay_seconds)
                continue

            if form_upper in {"10-K", "10-K/A"} and primary_document:
                url = self._document_url(cik, accession, primary_document)
                try:
                    text = self.sec_client.get_text(url)
                    requests_made += 1
                    filings_checked += 1
                except Exception:
                    continue
                if detect_going_concern(text):
                    flags.append(
                        {
                            "flag_type": "going_concern",
                            "filed_date": filing_date,
                            "accession": accession,
                            "details": "going concern language detected in 10-K audit section",
                            "active": 1,
                        }
                    )
                if self.request_delay_seconds > 0:
                    time.sleep(self.request_delay_seconds)
                continue

            if form in {"3", "3/A", "4", "4/A", "5", "5/A"} and primary_document:
                url = self._document_url(cik, accession, primary_document)
                try:
                    xml_text = self.sec_client.get_text(url)
                    requests_made += 1
                    filings_checked += 1
                except Exception:
                    continue
                if form.startswith("3"):
                    holdings.extend(parse_form3_initial_holdings(xml_text, filing_date, accession))
                elif form.startswith("4"):
                    holdings.extend(parse_form4_post_holdings(xml_text, filing_date, accession))
                if self.request_delay_seconds > 0:
                    time.sleep(self.request_delay_seconds)

        events_written = self.repo.upsert_company_edgar_events(company_id, events)
        flags_written = self.repo.upsert_company_edgar_flags(company_id, flags)
        activist_written = self.repo.upsert_company_activist_filings(company_id, activist)

        ownership_written = 0
        if holdings:
            shares_out = self._latest_shares_outstanding(ticker)
            agg = aggregate_insider_ownership_pct(holdings, shares_out)
            if agg.get("ownership_pct") is not None:
                ownership_written = self.repo.upsert_company_insider_ownership(
                    company_id,
                    {
                        "as_of_date": datetime.utcnow().strftime("%Y-%m-%d"),
                        **agg,
                    },
                )

        return {
            "events": events_written,
            "flags": flags_written,
            "ownership": ownership_written,
            "activist": activist_written,
        }, requests_made

    def _latest_shares_outstanding(self, ticker: str) -> float | None:
        resolved = resolve_financial_dimension("MRY", most_recent=False)
        rows = fetch_resolved_wide_rows(self.repo, [ticker], gte=None, resolved=resolved)
        if not rows:
            return None
        shares = rows[0].get("sharesbas")
        return float(shares) if shares is not None else None

    def _document_url(self, cik: str, accession: str, primary_document: str) -> str:
        return self.sec_client.form4_document_url(cik, accession, primary_document)
