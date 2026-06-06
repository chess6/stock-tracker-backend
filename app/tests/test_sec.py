from __future__ import annotations

from app.clients.sec import SecClient


def test_sec_client_headers_match_request_host():
    client = SecClient("TestApp test@example.com", "https://data.sec.gov")
    www_headers = client.headers_for("https://www.sec.gov/files/company_tickers.json")
    data_headers = client.headers_for("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")

    assert www_headers["Host"] == "www.sec.gov"
    assert data_headers["Host"] == "data.sec.gov"
    assert www_headers["User-Agent"] == "TestApp test@example.com"
