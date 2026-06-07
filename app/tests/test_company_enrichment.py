from __future__ import annotations

from app.services.company_enrichment import metadata_from_submissions, sic_to_sector


def test_sic_to_sector_financials():
    assert sic_to_sector("6021") == "Financials"
    assert sic_to_sector("5812") == "Consumer Discretionary"


def test_metadata_from_submissions():
    meta = metadata_from_submissions({
        "sic": "7372",
        "sicDescription": "Services-Prepackaged Software",
        "exchanges": ["Nasdaq"],
    })
    assert meta["sector"] == "Technology & Services"
    assert meta["industry"] == "Services-Prepackaged Software"
    assert meta["exchange"] == "Nasdaq"
