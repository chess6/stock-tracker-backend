"""Admin route for EDGAR events refresh."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_refresh_edgar_events_requires_tickers(app, client):
    response = client.post("/api/admin/refresh-edgar-events")
    assert response.status_code == 400
    assert "tickers" in response.get_json()["error"].lower()


def test_refresh_edgar_events_returns_payload(app, client):
    mock_payload = {
        "tickers": ["AAPL"],
        "eventsWritten": 2,
        "flagsWritten": 0,
        "activistWritten": 1,
        "skipped": [],
        "errors": [],
    }
    mock_service = MagicMock()
    mock_service.refresh_edgar_events.return_value = mock_payload

    with patch("app.routes.api.get_edgar_service", return_value=mock_service):
        response = client.post(
            "/api/admin/refresh-edgar-events?tickers=AAPL&maxTotalRequests=100",
        )

    assert response.status_code == 200
    assert response.get_json()["eventsWritten"] == 2
    mock_service.refresh_edgar_events.assert_called_once_with(["AAPL"])
