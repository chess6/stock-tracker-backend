"""Tests for per-user signal triage state."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.db import get_db
from app.repositories import Repository
from app.services.signal_state import (
    apply_user_state_to_signals,
    get_morning_brief,
    get_signal_state,
    touch_last_visited,
    update_signal_item_state,
)


def _enable_signals_flag(repo: Repository) -> None:
    repo.set_config("experimental_signals", True)


def test_touch_last_visited_sets_timestamp(app):
    with app.app_context():
        repo = Repository(get_db())
        _enable_signals_flag(repo)
        state = touch_last_visited(repo)
        assert state.get("lastVisitedAt")


def test_update_signal_item_read_and_snooze(app):
    with app.app_context():
        repo = Repository(get_db())
        _enable_signals_flag(repo)
        key = "AAPL:rank_up:2026-06-01"
        update_signal_item_state(repo, key, read=True)
        update_signal_item_state(repo, key, snooze_days=7)
        state = get_signal_state(repo)
        entry = state["items"][key]
        assert entry["read"] is True
        assert entry.get("snoozedUntil")


def test_apply_user_state_hides_read_and_snoozed(app):
    signals = [
        {"dedupKey": "AAPL:rank_up:2026-06-01", "researchImportance": 0.8},
        {"dedupKey": "MSFT:rank_up:2026-06-01", "researchImportance": 0.7},
    ]
    state = {
        "lastVisitedAt": None,
        "items": {
            "AAPL:rank_up:2026-06-01": {"read": True},
            "MSFT:rank_up:2026-06-01": {
                "snoozedUntil": (date.today() + timedelta(days=3)).isoformat(),
            },
        },
    }
    visible = apply_user_state_to_signals(signals, state, hide_read=True, hide_snoozed=True)
    assert visible == []


def test_morning_brief_includes_imminent_earnings_even_after_last_visit(app):
    with app.app_context():
        repo = Repository(get_db())
        _enable_signals_flag(repo)
        state = get_signal_state(repo)
        state["lastVisitedAt"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        from app.services.signal_state import save_signal_state

        save_signal_state(repo, state)
        today = date.today().isoformat()
        payload = {
            "items": [
                {
                    "dedupKey": f"MU:earnings_today:{today}",
                    "ticker": "MU",
                    "signalType": "earnings_today",
                    "researchImportance": 0.82,
                    "detectedAt": today,
                    "eventDate": today,
                },
                {
                    "dedupKey": "OLD1:rank_up:2026-06-01",
                    "ticker": "OLD1",
                    "researchImportance": 0.95,
                    "detectedAt": "2026-06-01T12:00:00+00:00",
                    "eventDate": "2026-06-01",
                    "signalType": "rank_up",
                },
            ]
        }
        brief = get_morning_brief(repo, payload, limit=5)
        tickers = {item["ticker"] for item in brief["items"]}
        assert "MU" in tickers
        assert "OLD1" not in tickers


def test_signals_state_routes(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _enable_signals_flag(repo)

    assert client.get("/api/signals/state").status_code == 200
    put = client.put(
        "/api/signals/state",
        json={"dedupKey": "TST:rank_up:2026-06-01", "read": True},
    )
    assert put.status_code == 200


def test_signals_morning_brief_route(app, client):
    with app.app_context():
        repo = Repository(get_db())
        _enable_signals_flag(repo)

    response = client.get("/api/signals/morning-brief?limit=5")
    assert response.status_code == 200
    payload = response.get_json()
    assert "items" in payload
    assert "lastVisitedAt" in payload
