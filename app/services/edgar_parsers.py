"""Pure parsers for SEC EDGAR filing content (Phase 4 critical ingestion)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

# 8-K items used as hard gate triggers / catalyst signals (B1)
TRACKED_8K_ITEMS: dict[str, str] = {
    "1.03": "bankruptcy",
    "2.01": "asset_sale",
    "4.01": "auditor_change",
    "4.02": "restatement",
    "5.02": "management_change",
    "8.01": "restructuring",
}

GOING_CONCERN_PATTERNS = (
    r"substantial doubt about its ability to continue as a going concern",
    r"substantial doubt exists about the company['\u2019]s ability to continue as a going concern",
    r"ability to continue as a going concern",
)

NT_FORM_TYPES = frozenset(
    {
        "NT 10-K",
        "NT 10-Q",
        "NT 10K",
        "NT 10Q",
        "NTN 10K",
        "NTN 10Q",
    }
)

ACTIVIST_FORM_TYPES = frozenset({"SC 13D", "SC 13D/A"})


def parse_8k_items(text: str) -> list[str]:
    """Extract Item numbers (e.g. 4.02) from 8-K document text."""
    if not text:
        return []
    normalized = text.replace("\xa0", " ")
    found: set[str] = set()
    for match in re.finditer(r"(?i)(?:item\s*)?(\d\.\d{2})", normalized):
        item = match.group(1)
        if item in TRACKED_8K_ITEMS:
            found.add(item)
    return sorted(found)


def map_8k_item_to_event_type(item_number: str) -> str:
    return TRACKED_8K_ITEMS.get(item_number, "other")


def detect_going_concern(audit_text: str) -> bool:
    if not audit_text:
        return False
    lowered = audit_text.lower()
    for pattern in GOING_CONCERN_PATTERNS:
        if re.search(pattern, lowered):
            return True
    return False


def is_nt_form(form_type: str | None) -> bool:
    if not form_type:
        return False
    normalized = form_type.strip().upper()
    return normalized in {f.upper() for f in NT_FORM_TYPES} or normalized.startswith("NT ")


def is_activist_filing(form_type: str | None) -> bool:
    if not form_type:
        return False
    return form_type.strip().upper() in {f.upper() for f in ACTIVIST_FORM_TYPES}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(node: ET.Element | None, path: str) -> str | None:
    if node is None:
        return None
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


def parse_form4_post_holdings(xml_text: str, filing_date: str, accession: str) -> list[dict]:
    """Extract post-transaction share holdings from Form 4 non-derivative table."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    owner_name = _text(root, ".//{*}reportingOwner/{*}reportingOwnerId/{*}rptOwnerName")
    records: list[dict] = []
    for node in root.iter():
        if _local(node.tag) != "nonDerivativeHolding":
            continue
        shares = _float(_text(node, ".//{*}postTransactionAmounts/{*}sharesOwnedFollowingTransaction/{*}value"))
        if shares is None:
            shares = _float(_text(node, ".//{*}sharesOwnedFollowingTransaction/{*}value"))
        title = _text(node, ".//{*}securityTitle/{*}value")
        if shares is None:
            continue
        records.append(
            {
                "owner_name": owner_name,
                "shares_held": shares,
                "security_title": title,
                "filing_date": filing_date,
                "accession": accession,
                "form": "4",
            }
        )
    return records


def parse_form3_initial_holdings(xml_text: str, filing_date: str, accession: str) -> list[dict]:
    """Extract initial beneficial ownership from Form 3."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    owner_name = _text(root, ".//{*}reportingOwner/{*}reportingOwnerId/{*}rptOwnerName")
    records: list[dict] = []
    for node in root.iter():
        if _local(node.tag) not in {"nonDerivativeHolding", "nonDerivativeSecurity"}:
            continue
        shares = _float(_text(node, ".//{*}sharesOwnedFollowingTransaction/{*}value"))
        if shares is None:
            shares = _float(_text(node, ".//{*}postTransactionAmounts/{*}sharesOwnedFollowingTransaction/{*}value"))
        if shares is None:
            shares = _float(_text(node, ".//{*}value"))
        title = _text(node, ".//{*}securityTitle/{*}value")
        if shares is None:
            continue
        records.append(
            {
                "owner_name": owner_name,
                "shares_held": shares,
                "security_title": title,
                "filing_date": filing_date,
                "accession": accession,
                "form": "3",
            }
        )
    return records


def aggregate_insider_ownership_pct(
    holdings: list[dict],
    shares_outstanding: float | None,
) -> dict[str, float | None]:
    """Sum insider holdings (common stock only) as % of shares outstanding."""
    if shares_outstanding in (None, 0):
        return {"ownership_pct": None, "shares_held": None, "shares_outstanding": shares_outstanding}
    by_owner: dict[str, float] = {}
    for row in holdings:
        title = (row.get("security_title") or "").lower()
        if title and "common" not in title and "ordinary" not in title and title.strip():
            if "preferred" in title or "option" in title or "warrant" in title:
                continue
        owner = row.get("owner_name") or "unknown"
        by_owner[owner] = max(by_owner.get(owner, 0.0), float(row.get("shares_held") or 0.0))
    total = sum(by_owner.values())
    return {
        "ownership_pct": total / shares_outstanding if total else 0.0,
        "shares_held": total,
        "shares_outstanding": shares_outstanding,
    }
