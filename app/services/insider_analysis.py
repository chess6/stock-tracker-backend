from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("stock_tracker.insider_analysis")

BUY_CODES = frozenset({"P", "A"})
SELL_CODES = frozenset({"S", "D"})
# Open-market purchases only; awards (A) often report $0 and are excluded from clusters.
CLUSTER_BUY_CODES = frozenset({"P"})
CLUSTER_WINDOW_DAYS = 30
CLUSTER_MIN_UNIQUE_BUYERS = 3


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:10]).date()
    except ValueError:
        return None


def _today() -> date:
    return datetime.now(timezone.utc).date()


def is_buy_transaction(code: str | None) -> bool:
    return (code or "").upper() in BUY_CODES


def is_sell_transaction(code: str | None) -> bool:
    return (code or "").upper() in SELL_CODES


def is_cluster_buy_transaction(txn: dict) -> bool:
    """Open-market purchase with a positive dollar value (cluster screener semantics)."""
    if (txn.get("transaction_code") or "").upper() not in CLUSTER_BUY_CODES:
        return False
    return transaction_value(txn) > 0


def transaction_value(txn: dict) -> float:
    raw = txn.get("transaction_value")
    if raw is not None:
        return abs(float(raw))
    shares = txn.get("shares")
    price = txn.get("price_per_share")
    if shares is not None and price is not None:
        return abs(float(shares) * float(price))
    return 0.0


def compute_intensity_score(buy_count: int, total_buy_value: float, days_active: int) -> float:
    """Normalized 0-1 buy intensity: (buy_count * ln(total_buy_value)) / days_active."""
    if buy_count <= 0 or days_active <= 0:
        return 0.0
    value_term = math.log(max(total_buy_value, 1.0))
    raw = (buy_count * value_term) / days_active
    # Soft cap around ~2.0 raw → 1.0 normalized
    return round(min(raw / 2.0, 1.0), 4)


def summarize_window(transactions: list[dict], *, days: int, as_of: date | None = None) -> dict[str, Any]:
    """Buy/sell summary for transactions within the last N days."""
    end = as_of or _today()
    start = end - timedelta(days=days)
    buys: list[dict] = []
    sells: list[dict] = []
    active_dates: set[date] = set()

    for txn in transactions:
        txn_date = _parse_date(txn.get("transaction_date") or txn.get("filing_date"))
        if txn_date is None or txn_date < start or txn_date > end:
            continue
        active_dates.add(txn_date)
        code = txn.get("transaction_code")
        if is_buy_transaction(code):
            buys.append(txn)
        elif is_sell_transaction(code):
            sells.append(txn)

    buy_value = sum(transaction_value(t) for t in buys)
    sell_value = sum(transaction_value(t) for t in sells)
    buy_count = len(buys)
    sell_count = len(sells)
    unique_buyers = len({t.get("owner_name") for t in buys if t.get("owner_name")})
    days_active = max(len(active_dates), 1)

    return {
        "days": days,
        "windowStart": start.isoformat(),
        "windowEnd": end.isoformat(),
        "buyCount": buy_count,
        "sellCount": sell_count,
        "uniqueBuyers": unique_buyers,
        "totalBuyValue": round(buy_value, 2),
        "totalSellValue": round(sell_value, 2),
        "buySellCountRatio": buy_count / sell_count if sell_count else (float(buy_count) if buy_count else None),
        "buySellValueRatio": buy_value / sell_value if sell_value else (float(buy_value) if buy_value else None),
        "intensityScore": compute_intensity_score(buy_count, buy_value, days_active),
    }


def detect_clusters(
    transactions: list[dict],
    *,
    window_days: int = CLUSTER_WINDOW_DAYS,
    min_unique_buyers: int = CLUSTER_MIN_UNIQUE_BUYERS,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Detect 30-day windows with concentrated insider buying."""
    end = as_of or _today()
    lookback_start = end - timedelta(days=365)
    buy_events: list[tuple[date, dict]] = []

    for txn in transactions:
        if not is_cluster_buy_transaction(txn):
            continue
        txn_date = _parse_date(txn.get("transaction_date") or txn.get("filing_date"))
        if txn_date is None or txn_date < lookback_start or txn_date > end:
            continue
        buy_events.append((txn_date, txn))

    if not buy_events:
        return []

    buy_events.sort(key=lambda item: item[0])
    clusters: list[dict[str, Any]] = []
    seen_windows: set[tuple[str, str]] = set()
    anchor_dates = sorted({d for d, _ in buy_events} | {end})

    for window_end in anchor_dates:
        window_start = window_end - timedelta(days=window_days - 1)
        if window_start < lookback_start:
            window_start = lookback_start
        window_key = (window_start.isoformat(), window_end.isoformat())
        if window_key in seen_windows:
            continue

        window_buys = [txn for d, txn in buy_events if window_start <= d <= window_end]
        window_sells = []
        for txn in transactions:
            if not is_sell_transaction(txn.get("transaction_code")):
                continue
            txn_date = _parse_date(txn.get("transaction_date") or txn.get("filing_date"))
            if txn_date is None or txn_date < window_start or txn_date > window_end:
                continue
            window_sells.append(txn)

        unique_buyers = len({t.get("owner_name") for t in window_buys if t.get("owner_name")})
        if unique_buyers < min_unique_buyers:
            continue

        buy_value = sum(transaction_value(t) for t in window_buys)
        sell_value = sum(transaction_value(t) for t in window_sells)
        buy_prices = [
            float(t["price_per_share"])
            for t in window_buys
            if t.get("price_per_share") is not None and float(t["price_per_share"]) > 0
        ]
        avg_buy_price = round(sum(buy_prices) / len(buy_prices), 4) if buy_prices else None
        active_dates = {d for d, _ in buy_events if window_start <= d <= window_end}
        intensity = compute_intensity_score(len(window_buys), buy_value, max(len(active_dates), 1))

        seen_windows.add(window_key)
        clusters.append(
            {
                "windowStart": window_start.isoformat(),
                "windowEnd": window_end.isoformat(),
                "buyCount": len(window_buys),
                "sellCount": len(window_sells),
                "uniqueBuyers": unique_buyers,
                "totalBuyValue": round(buy_value, 2),
                "totalSellValue": round(sell_value, 2),
                "avgBuyPrice": avg_buy_price,
                "intensityScore": intensity,
                "isCluster": True,
            }
        )

    clusters.sort(key=lambda c: (c["intensityScore"], c["totalBuyValue"]), reverse=True)
    return clusters


def analyze_insider_activity(transactions: list[dict]) -> dict[str, Any]:
    """Full insider analysis for one company."""
    summary_90 = summarize_window(transactions, days=90)
    return {
        "buyCount90d": summary_90["buyCount"],
        "sellCount90d": summary_90["sellCount"],
        "buySellRatio": summary_90["buySellCountRatio"],
        "totalBuyValue90d": summary_90["totalBuyValue"],
        "totalSellValue90d": summary_90["totalSellValue"],
        "uniqueBuyers90d": summary_90["uniqueBuyers"],
        "intensityScore90d": summary_90["intensityScore"],
        "ratios": {
            "90d": summarize_window(transactions, days=90),
            "180d": summarize_window(transactions, days=180),
            "365d": summarize_window(transactions, days=365),
        },
    }


def cluster_records_for_storage(company_id: int, transactions: list[dict]) -> list[dict[str, Any]]:
    """Build DB rows for insider_cluster_analysis upsert."""
    clusters = detect_clusters(transactions)
    return [
        {
            "company_id": company_id,
            "window_start": cluster["windowStart"],
            "window_end": cluster["windowEnd"],
            "buy_count": cluster["buyCount"],
            "sell_count": cluster["sellCount"],
            "unique_buyers": cluster["uniqueBuyers"],
            "total_buy_value": cluster["totalBuyValue"],
            "total_sell_value": cluster["totalSellValue"],
            "avg_buy_price": cluster["avgBuyPrice"],
            "intensity_score": cluster["intensityScore"],
        }
        for cluster in clusters
    ]


def format_transaction(txn: dict) -> dict[str, Any]:
    return {
        "filingDate": txn.get("filing_date"),
        "transactionDate": txn.get("transaction_date"),
        "ownerName": txn.get("owner_name"),
        "transactionCode": txn.get("transaction_code"),
        "shares": txn.get("shares"),
        "pricePerShare": txn.get("price_per_share"),
        "transactionValue": txn.get("transaction_value"),
        "securityTitle": txn.get("security_title"),
        "isBuy": is_buy_transaction(txn.get("transaction_code")),
        "isSell": is_sell_transaction(txn.get("transaction_code")),
    }
