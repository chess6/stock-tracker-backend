"""Portfolio Watch digest — summary over held/watched tickers."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..repositories import Repository

EARNINGS_TYPES = frozenset({"earnings", "earnings_upcoming", "earnings_today"})
ALERT_TYPES = frozenset({
    "going_concern_8k",
    "guidance_cut",
    "earnings_miss",
    "activist_13d",
    "bankruptcy",
    "restatement",
})
HIGH_IMPORTANCE = 0.65


def build_portfolio_watch_digest(
    repo: Repository,
    signals: list[dict[str, Any]],
    *,
    portfolio_tickers: list[str] | None = None,
) -> dict[str, Any]:
    tickers = portfolio_tickers if portfolio_tickers is not None else repo.fetch_portfolio_tickers()
    held = {t.upper() for t in tickers if t}
    today = date.today()
    earnings_window_end = today + timedelta(days=7)

    covered = {s.get("ticker", "").upper() for s in signals if s.get("ticker")}
    uncovered = sorted(held - covered)

    earnings_imminent: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    for signal in signals:
        ticker = (signal.get("ticker") or "").upper()
        if ticker not in held:
            continue
        signal_type = (signal.get("signalType") or "").lower()
        importance = float(signal.get("researchImportance") or 0)
        event_date_raw = signal.get("eventDate")
        try:
            event_d = date.fromisoformat(str(event_date_raw)[:10]) if event_date_raw else None
        except ValueError:
            event_d = None
        if signal_type in EARNINGS_TYPES and event_d and today - timedelta(days=1) <= event_d <= earnings_window_end:
            earnings_imminent.append({
                "ticker": ticker,
                "eventDate": event_date_raw,
                "signalType": signal_type,
                "researchImportance": importance,
            })
        if signal_type in ALERT_TYPES and importance >= HIGH_IMPORTANCE:
            alerts.append({
                "ticker": ticker,
                "signalType": signal_type,
                "researchImportance": importance,
                "whyItMatters": signal.get("whyItMatters"),
            })

    earnings_imminent.sort(key=lambda row: (row.get("eventDate") or "", -(row.get("researchImportance") or 0)))
    alerts.sort(key=lambda row: -(row.get("researchImportance") or 0))

    summary_lines: list[str] = []
    if not held:
        summary_lines.append("Portfolio is empty — add tickers to enable watch digest.")
    else:
        summary_lines.append(
            f"{len(signals)} signal{'s' if len(signals) != 1 else ''} across {len(covered & held)} of {len(held)} held names."
        )
        if earnings_imminent:
            names = ', '.join(item['ticker'] for item in earnings_imminent[:5])
            summary_lines.append(f"Earnings window: {names}.")
        if alerts:
            summary_lines.append(f"{len(alerts)} high-severity alert{'s' if len(alerts) != 1 else ''} for held names.")
        elif held:
            summary_lines.append("No high-severity alerts for held names.")

    return {
        "portfolioCount": len(held),
        "signalCount": len(signals),
        "tickersWithSignals": len(covered & held),
        "tickersQuiet": uncovered[:20],
        "earningsImminent": earnings_imminent[:10],
        "alerts": alerts[:10],
        "summaryLines": summary_lines,
    }
