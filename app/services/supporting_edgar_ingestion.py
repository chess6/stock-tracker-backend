"""Phase 5 supporting ingestion — debt maturities (B3), segments (B4)."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from ..clients.sec import SecClient
from ..repositories import Repository
from .sec_eligibility import should_skip_sec_fundamentals

logger = logging.getLogger("stock_tracker.pipeline.edgar_supporting")

# XBRL debt maturity concepts (best-effort; absent tags degrade gracefully)
DEBT_MATURITY_CONCEPTS: dict[str, str] = {
    "year_1": "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",
    "year_2": "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo",
    "year_3": "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree",
    "year_4": "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFour",
    "year_5": "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive",
    "after_year_5": "LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive",
}

SEGMENT_REVENUE_CONCEPTS = (
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
)


class SupportingEdgarIngestionService:
    def __init__(
        self,
        repo: Repository,
        sec_client: SecClient,
        *,
        max_total_requests: int = 200,
    ) -> None:
        self.repo = repo
        self.sec_client = sec_client
        self.max_total_requests = max_total_requests

    def refresh_supporting_edgar(self, tickers: list[str]) -> dict:
        logger.info("refresh_supporting_edgar start tickers=%d", len(tickers))
        t0 = time.monotonic()
        refreshed: list[str] = []
        skipped: list[dict] = []
        errors: list[dict] = []
        maturity_written = 0
        segment_written = 0
        requests_made = 0

        for ticker in [t.upper() for t in tickers if t]:
            if requests_made >= self.max_total_requests:
                break
            company = self.repo.get_company_by_ticker(ticker)
            if not company or not company.get("cik"):
                skipped.append({"ticker": ticker, "reason": "no_cik"})
                continue
            if should_skip_sec_fundamentals(company):
                skipped.append({"ticker": ticker, "reason": "non_operating_issuer"})
                continue
            try:
                facts = self.sec_client.fetch_company_facts(company["cik"])
                requests_made += 1
            except requests.RequestException as exc:
                errors.append({"ticker": ticker, "reason": "sec_request_error", "message": str(exc)})
                continue

            maturities = parse_debt_maturities_from_facts(facts)
            segments = parse_segments_from_facts(facts)

            if maturities:
                maturity_written += self.repo.upsert_company_debt_maturities(
                    company["id"],
                    maturities.get("period_end"),
                    maturities.get("rows", []),
                )
            if segments:
                segment_written += self.repo.upsert_company_segments(
                    company["id"],
                    segments.get("period_end"),
                    segments.get("rows", []),
                )
            refreshed.append(ticker)

        elapsed = time.monotonic() - t0
        logger.info(
            "refresh_supporting_edgar done tickers=%d maturities=%d segments=%d requests=%d elapsed=%.1fs",
            len(refreshed),
            maturity_written,
            segment_written,
            requests_made,
            elapsed,
        )
        return {
            "tickers": refreshed,
            "maturityRowsWritten": maturity_written,
            "segmentRowsWritten": segment_written,
            "skipped": skipped,
            "errors": errors,
        }


def _latest_annual_observation(facts: dict, concept: str) -> dict | None:
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    payload = us_gaap.get(concept)
    if not payload:
        return None
    units = payload.get("units", {}).get("USD") or []
    annual = [obs for obs in units if (obs.get("form") or "").upper().startswith("10-K")]
    if not annual:
        annual = units
    if not annual:
        return None
    return max(annual, key=lambda o: (o.get("end") or "", o.get("filed") or ""))


def parse_debt_maturities_from_facts(facts: dict) -> dict[str, Any] | None:
    rows: list[dict] = []
    period_end = None
    for maturity_year, concept in DEBT_MATURITY_CONCEPTS.items():
        obs = _latest_annual_observation(facts, concept)
        if not obs:
            continue
        period_end = period_end or obs.get("end")
        rows.append({"maturity_year": maturity_year, "amount": obs.get("val")})
    if not rows:
        return None
    return {"period_end": period_end, "rows": rows}


def parse_segments_from_facts(facts: dict) -> dict[str, Any] | None:
    """
    Best-effort segment revenue extraction from XBRL dimensional members.
    Returns None when segment tagging is absent (graceful degradation per plan).
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    rows: list[dict] = []
    period_end = None

    for concept in SEGMENT_REVENUE_CONCEPTS:
        payload = us_gaap.get(concept)
        if not payload:
            continue
        for unit_name, observations in (payload.get("units") or {}).items():
            if unit_name != "USD":
                continue
            for obs in observations:
                segment = obs.get("segment")
                if not segment:
                    continue
                member = segment.get("member") or segment.get("dimension")
                if not member or "ConsolidationItems" in str(member):
                    continue
                name = str(member).split(":")[-1].replace("Member", "").replace("_", " ").strip()
                if not name or name.lower() in {"all", "consolidated"}:
                    continue
                period_end = period_end or obs.get("end")
                rows.append(
                    {
                        "segment_name": name,
                        "revenue": obs.get("val"),
                        "operating_income": None,
                        "margin": None,
                    }
                )
        if rows:
            break

    if not rows:
        return None
    deduped: dict[str, dict] = {}
    for row in rows:
        key = row["segment_name"]
        existing = deduped.get(key)
        if existing is None or (row.get("revenue") or 0) > (existing.get("revenue") or 0):
            deduped[key] = row
    return {"period_end": period_end, "rows": list(deduped.values())}
