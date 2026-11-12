from __future__ import annotations

import pytest
import requests

from app.services.nasdaq import NasdaqError, NasdaqService

FAKE_KEY = "TESTKEY_should_never_leak"


class _FakeSession:
    def __init__(self, *, status: int = 200, payload: dict | None = None, exc: Exception | None = None):
        self.status = status
        self.payload = payload or {}
        self.exc = exc
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params or {}))
        if self.exc:
            raise self.exc
        response = requests.Response()
        response.status_code = self.status
        response.url = requests.Request("GET", url, params=params).prepare().url
        response._content = b"{}"
        return response


def test_http_error_message_does_not_contain_key():
    service = NasdaqService(FAKE_KEY, session=_FakeSession(status=403))
    with pytest.raises(NasdaqError) as excinfo:
        service.fetch_price_history("AAPL", days=5)
    assert excinfo.value.status_code == 403
    assert FAKE_KEY not in str(excinfo.value)
    assert "api_key" not in str(excinfo.value)
    # The original requests error (whose message includes the full URL) must not be chained.
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None or FAKE_KEY not in str(excinfo.value.__context__)


def test_connection_error_message_does_not_contain_key():
    leaky = requests.ConnectionError(f"Max retries exceeded with url: /SEP?api_key={FAKE_KEY}")
    service = NasdaqService(FAKE_KEY, session=_FakeSession(exc=leaky))
    with pytest.raises(NasdaqError) as excinfo:
        service.fetch_price_history("AAPL", days=5)
    assert FAKE_KEY not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


def test_key_is_sent_as_request_param():
    session = _FakeSession(payload={})
    NasdaqService(FAKE_KEY, session=session).fetch_price_history("AAPL", days=5)
    assert session.calls[0][1]["api_key"] == FAKE_KEY
