"""Per-user signal triage state — read, snooze, last visit (stored in ui_prefs_json)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..repositories import Repository

SIGNAL_STATE_KEY = "signalState"
DEFAULT_STATE: dict[str, Any] = {
    "lastVisitedAt": None,
    "items": {},
}

FORWARD_CATALYST_TYPES = frozenset({"earnings", "earnings_upcoming", "earnings_today"})
EARNINGS_IMMINENT_PAST_DAYS = 3
EARNINGS_IMMINENT_FUTURE_DAYS = 14


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _parse_event_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _is_earnings_imminent(signal: dict[str, Any]) -> bool:
    signal_type = (signal.get("signalType") or "").lower()
    if signal_type not in FORWARD_CATALYST_TYPES:
        return False
    event_d = _parse_event_date(signal.get("eventDate"))
    if event_d is None:
        return False
    delta = (event_d - date.today()).days
    return -EARNINGS_IMMINENT_PAST_DAYS <= delta <= EARNINGS_IMMINENT_FUTURE_DAYS


def _detected_after_last_visit(signal: dict[str, Any], last_dt: datetime) -> bool:
    raw = signal.get("detectedAt") or signal.get("eventDate")
    if not raw:
        return True
    if len(str(raw)) <= 10:
        event_d = _parse_event_date(str(raw))
        if event_d is None:
            return True
        end_of_day = datetime(
            event_d.year,
            event_d.month,
            event_d.day,
            23,
            59,
            59,
            tzinfo=timezone.utc,
        )
        return end_of_day > last_dt
    detected = _parse_iso(str(raw))
    return detected is None or detected > last_dt


def get_signal_state(repo: Repository) -> dict[str, Any]:
    ui_prefs = repo._load_ui_prefs_dict()
    raw = ui_prefs.get(SIGNAL_STATE_KEY) or {}
    items = raw.get("items") if isinstance(raw.get("items"), dict) else {}
    return {
        "lastVisitedAt": raw.get("lastVisitedAt"),
        "items": items,
    }


def save_signal_state(repo: Repository, state: dict[str, Any]) -> dict[str, Any]:
    ui_prefs = repo._load_ui_prefs_dict()
    ui_prefs[SIGNAL_STATE_KEY] = {
        "lastVisitedAt": state.get("lastVisitedAt"),
        "items": state.get("items") or {},
    }
    repo._save_ui_prefs_dict(ui_prefs)
    repo.commit()
    return get_signal_state(repo)


def touch_last_visited(repo: Repository) -> dict[str, Any]:
    state = get_signal_state(repo)
    state["lastVisitedAt"] = _utc_now_iso()
    return save_signal_state(repo, state)


def update_signal_item_state(
    repo: Repository,
    dedup_key: str,
    *,
    read: bool | None = None,
    snooze_days: int | None = None,
    clear_snooze: bool = False,
) -> dict[str, Any]:
    key = (dedup_key or "").strip()
    if not key:
        return {"error": "dedup_key is required"}
    state = get_signal_state(repo)
    items: dict[str, Any] = dict(state.get("items") or {})
    entry = dict(items.get(key) or {})
    if read is not None:
        entry["read"] = bool(read)
        if read:
            entry["readAt"] = _utc_now_iso()
    if clear_snooze:
        entry.pop("snoozedUntil", None)
    elif snooze_days is not None:
        until = datetime.now(timezone.utc) + timedelta(days=max(1, int(snooze_days)))
        entry["snoozedUntil"] = until.replace(microsecond=0).isoformat()
    items[key] = entry
    state["items"] = items
    return save_signal_state(repo, state)


def is_signal_hidden(item_state: dict[str, Any] | None, *, include_snoozed: bool = False) -> bool:
    if not item_state:
        return False
    if item_state.get("read"):
        return True
    snoozed_until = item_state.get("snoozedUntil")
    if not include_snoozed and snoozed_until:
        parsed = _parse_iso(snoozed_until)
        if parsed and parsed > datetime.now(timezone.utc):
            return True
    return False


def apply_user_state_to_signals(
    signals: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    hide_read: bool = True,
    hide_snoozed: bool = True,
    since_last_visit: bool = False,
) -> list[dict[str, Any]]:
    items = state.get("items") or {}
    last_visited = state.get("lastVisitedAt")
    last_dt = _parse_iso(last_visited) if since_last_visit else None
    enriched: list[dict[str, Any]] = []
    for signal in signals:
        dedup_key = signal.get("dedupKey") or ""
        item_state = items.get(dedup_key) or {}
        merged = {
            **signal,
            "userState": {
                "read": bool(item_state.get("read")),
                "snoozedUntil": item_state.get("snoozedUntil"),
            },
        }
        if hide_read and item_state.get("read"):
            continue
        if hide_snoozed and is_signal_hidden(item_state, include_snoozed=False):
            if item_state.get("snoozedUntil"):
                continue
        if last_dt is not None:
            if not (_is_earnings_imminent(signal) or _detected_after_last_visit(signal, last_dt)):
                continue
        enriched.append(merged)
    return enriched


def get_morning_brief(
    repo: Repository,
    signals_payload: dict[str, Any],
    *,
    limit: int = 12,
) -> dict[str, Any]:
    state = get_signal_state(repo)
    items = list(signals_payload.get("items") or [])
    imminent = [s for s in items if _is_earnings_imminent(s)]
    new_items = apply_user_state_to_signals(
        items,
        state,
        hide_read=True,
        hide_snoozed=True,
        since_last_visit=True,
    )
    seen = {s.get("dedupKey") for s in new_items}
    for signal in imminent:
        key = signal.get("dedupKey")
        if key and key not in seen:
            item_state = (state.get("items") or {}).get(key) or {}
            if item_state.get("read"):
                continue
            new_items.append({
                **signal,
                "userState": {
                    "read": bool(item_state.get("read")),
                    "snoozedUntil": item_state.get("snoozedUntil"),
                },
            })
            seen.add(key)
    new_items.sort(
        key=lambda s: (
            0 if _is_earnings_imminent(s) else 1,
            -(float(s.get("researchImportance") or 0)),
            s.get("eventDate") or "",
        ),
    )
    trimmed = new_items[: max(1, int(limit))]
    return {
        "lastVisitedAt": state.get("lastVisitedAt"),
        "returned": len(trimmed),
        "limit": limit,
        "items": trimmed,
    }
