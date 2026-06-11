"""Tests for Phase 4/5 EDGAR and supporting ingestion."""

from app.db import connect_db, init_db
from app.repositories import Repository
from app.services.edgar_parsers import (
    aggregate_insider_ownership_pct,
    detect_going_concern,
    is_nt_form,
    parse_8k_items,
)
from app.services.finra_short_interest import parse_short_interest_file
from app.services.supporting_edgar_ingestion import parse_debt_maturities_from_facts, parse_segments_from_facts


def test_parse_8k_items_tracked_numbers():
    text = "Item 4.02 Non-Reliance on Previously Issued Financial Statements. Item 5.02 Departure of Directors."
    items = parse_8k_items(text)
    assert "4.02" in items
    assert "5.02" in items


def test_detect_going_concern_positive():
    audit = (
        "In our opinion, substantial doubt exists about the company's ability to continue as a going concern "
        "for the next twelve months."
    )
    assert detect_going_concern(audit) is True


def test_detect_going_concern_negative():
    assert detect_going_concern("The Company has adequate liquidity.") is False


def test_is_nt_form():
    assert is_nt_form("NT 10-K") is True
    assert is_nt_form("10-K") is False


def test_aggregate_insider_ownership_pct():
    holdings = [
        {"owner_name": "Alice", "shares_held": 1000.0, "security_title": "Common Stock"},
        {"owner_name": "Bob", "shares_held": 500.0, "security_title": "Common Stock"},
    ]
    agg = aggregate_insider_ownership_pct(holdings, 10000.0)
    assert agg["ownership_pct"] == 0.15
    assert agg["shares_held"] == 1500.0


def test_edgar_events_upsert_idempotent(tmp_path):
    db_path = tmp_path / "edgar.sqlite3"
    init_db(str(db_path))
    conn = connect_db(str(db_path))
    try:
        repo = Repository(conn)
        conn.execute(
            "INSERT INTO companies (ticker, name, cik) VALUES ('TEST', 'Test Co', '0000000001')"
        )
        conn.commit()
        company_id = conn.execute("SELECT id FROM companies WHERE ticker='TEST'").fetchone()["id"]
        events = [
            {
                "form_type": "8-K",
                "item_number": "4.02",
                "filed_date": "2024-01-15",
                "event_type": "restatement",
                "summary": "Item 4.02 restatement",
                "accession": "0000000001-24-000001",
            }
        ]
        first = repo.upsert_company_edgar_events(company_id, events)
        second = repo.upsert_company_edgar_events(company_id, events)
        count = conn.execute("SELECT COUNT(*) FROM company_edgar_events WHERE company_id=?", (company_id,)).fetchone()[0]
        assert first == 1
        assert second == 1
        assert count == 1
    finally:
        conn.close()


def test_edgar_flags_going_concern_idempotent(tmp_path):
    db_path = tmp_path / "flags.sqlite3"
    init_db(str(db_path))
    conn = connect_db(str(db_path))
    try:
        repo = Repository(conn)
        conn.execute(
            "INSERT INTO companies (ticker, name, cik) VALUES ('GC', 'Going Concern Co', '0000000002')"
        )
        conn.commit()
        company_id = conn.execute("SELECT id FROM companies WHERE ticker='GC'").fetchone()["id"]
        flags = [
            {
                "flag_type": "going_concern",
                "filed_date": "2024-03-01",
                "accession": "0000000002-24-000010",
                "details": "detected",
                "active": 1,
            }
        ]
        repo.upsert_company_edgar_flags(company_id, flags)
        repo.upsert_company_edgar_flags(company_id, flags)
        row = conn.execute(
            "SELECT flag_type, active FROM company_edgar_flags WHERE company_id=?",
            (company_id,),
        ).fetchone()
        assert row["flag_type"] == "going_concern"
        assert row["active"] == 1
    finally:
        conn.close()


def test_parse_debt_maturities_from_facts():
    facts = {
        "facts": {
            "us-gaap": {
                "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths": {
                    "units": {
                        "USD": [
                            {"val": 100.0, "end": "2024-12-31", "form": "10-K", "filed": "2025-02-01"},
                        ]
                    }
                },
                "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo": {
                    "units": {
                        "USD": [
                            {"val": 200.0, "end": "2024-12-31", "form": "10-K", "filed": "2025-02-01"},
                        ]
                    }
                },
            }
        }
    }
    parsed = parse_debt_maturities_from_facts(facts)
    assert parsed is not None
    assert parsed["period_end"] == "2024-12-31"
    assert len(parsed["rows"]) == 2


def test_parse_segments_from_facts_graceful_none():
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [{"val": 100.0, "end": "2024-12-31", "form": "10-K"}]}}}}}
    assert parse_segments_from_facts(facts) is None


def test_parse_short_interest_file():
    text = "Symbol|Company Name|Short Interest|Settlement Date|Market\nAAPL|Apple Inc|50000000|2024-01-15|Q\n"
    rows = parse_short_interest_file(text)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["short_interest_shares"] == 50000000.0


def test_company_market_data_upsert_idempotent(tmp_path):
    db_path = tmp_path / "market.sqlite3"
    init_db(str(db_path))
    conn = connect_db(str(db_path))
    try:
        repo = Repository(conn)
        repo.upsert_company_market_data("AAPL", "2024-01-15", "short_interest_pct", 1.5, source="finra_short_interest")
        repo.upsert_company_market_data("AAPL", "2024-01-15", "short_interest_pct", 1.5, source="finra_short_interest")
        count = conn.execute("SELECT COUNT(*) FROM company_market_data WHERE ticker='AAPL'").fetchone()[0]
        assert count == 1
    finally:
        conn.close()
