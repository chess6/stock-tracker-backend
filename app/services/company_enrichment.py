from __future__ import annotations


def sic_to_sector(sic: str | int | None) -> str | None:
    """Map SEC SIC code to a coarse sector label (GICS-inspired grouping)."""
    if sic is None or sic == "":
        return None
    try:
        code = int(str(sic).strip())
    except ValueError:
        return None
    if 100 <= code <= 999:
        return "Agriculture"
    if 1000 <= code <= 1499:
        return "Energy"
    if 1500 <= code <= 1799:
        return "Construction"
    if 2000 <= code <= 3999:
        return "Industrials"
    if 4000 <= code <= 4999:
        return "Transportation"
    if 5000 <= code <= 5199:
        return "Wholesale Trade"
    if 5200 <= code <= 5999:
        return "Consumer Discretionary"
    if 6000 <= code <= 6799:
        return "Financials"
    if 7000 <= code <= 8999:
        return "Technology & Services"
    if 9100 <= code <= 9729:
        return "Public Administration"
    if 9900 <= code <= 9999:
        return "Conglomerates"
    return "Other"


def metadata_from_submissions(payload: dict) -> dict:
    sic = payload.get("sic")
    return {
        "sector": sic_to_sector(sic),
        "industry": payload.get("sicDescription") or payload.get("category"),
        "exchange": (payload.get("exchanges") or [None])[0],
    }
