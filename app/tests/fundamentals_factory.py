"""Test helpers for seeding fundamentals that survive the canonical-annual filter.

Annual wide rows need at least ``CANONICAL_ANNUAL_MIN_STATEMENT_METRICS`` statement
metrics (see app/services/fundamentals.py); sparse fixtures are dropped as interim
8-K orphans, which makes research endpoints return 404.
"""

from __future__ import annotations

from app.services.fundamentals import CANONICAL_ANNUAL_MIN_STATEMENT_METRICS

# Plausible filler values for a small company's 10-K; tests override what they assert on.
_FILLER = {
    "cor": 600.0,
    "gp": 400.0,
    "opex": 250.0,
    "sgna": 150.0,
    "opinc": 150.0,
    "ebit": 150.0,
    "ebitda": 190.0,
    "taxexp": 30.0,
    "assets": 2000.0,
    "liabilities": 900.0,
    "equity": 1100.0,
    "ncfo": 210.0,
    "capex": -60.0,
}


def annual_statement_rows(
    company_id: int,
    *,
    period_end: str = "2024-12-31",
    fiscal_year: int = 2024,
    filing_date: str = "2025-01-20",
    skip: set[str] | frozenset[str] = frozenset(),
) -> list[dict]:
    """Filler 10-K rows so a sparse fixture forms a complete annual statement.

    ``skip`` lists metrics the test seeds itself, so its values are not duplicated.
    """
    rows = [
        {
            "company_id": company_id,
            "metric": metric,
            "value": value,
            "unit": "USD",
            "period_end": period_end,
            "period_type": "annual",
            "dimension": "ARY",
            "fiscal_year": fiscal_year,
            "fiscal_quarter": "FY",
            "filing_date": filing_date,
            "form": "10-K",
            "accession": f"filler-{fiscal_year}",
            "source": "sec_companyfacts",
            "taxonomy": "us-gaap",
            "xbrl_concept": metric,
        }
        for metric, value in _FILLER.items()
        if metric not in skip
    ]
    assert len(rows) >= CANONICAL_ANNUAL_MIN_STATEMENT_METRICS
    return rows
