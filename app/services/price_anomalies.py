"""Price/volume anomaly detection from SQLite prices."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..repositories import Repository

VOLUME_SPIKE_RATIO = 2.5
MIN_AVG_VOLUME = 50_000.0


def detect_unusual_volume_signals(
    repo: Repository,
    *,
    limit: int = 50,
    lookback_days: int = 25,
) -> list[dict[str, Any]]:
    """Tickers with latest volume >= VOLUME_SPIKE_RATIO × trailing average."""
    cutoff = (date.today() - timedelta(days=lookback_days + 5)).isoformat()
    rows = repo.conn.execute(
        """
        WITH recent AS (
            SELECT ticker, date, volume, close,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
            FROM prices
            WHERE date >= ?
              AND volume IS NOT NULL
              AND volume > 0
        ),
        latest AS (
            SELECT ticker, date, volume, close
            FROM recent
            WHERE rn = 1
        ),
        stats AS (
            SELECT
                ticker,
                AVG(volume) AS avg_volume,
                COUNT(*) AS sample_days
            FROM recent
            WHERE rn BETWEEN 2 AND ?
            GROUP BY ticker
            HAVING sample_days >= 10
        )
        SELECT
            l.ticker,
            l.date AS event_date,
            l.volume,
            l.close,
            s.avg_volume,
            (l.volume / s.avg_volume) AS volume_ratio
        FROM latest l
        JOIN stats s ON s.ticker = l.ticker
        WHERE l.volume >= s.avg_volume * ?
          AND s.avg_volume >= ?
        ORDER BY volume_ratio DESC
        LIMIT ?
        """,
        (cutoff, lookback_days, VOLUME_SPIKE_RATIO, MIN_AVG_VOLUME, max(1, int(limit))),
    ).fetchall()

    signals: list[dict[str, Any]] = []
    for row in rows:
        ratio = float(row["volume_ratio"] or 0)
        signals.append(
            {
                "ticker": row["ticker"],
                "signal_type": "unusual_volume",
                "event_date": row["event_date"],
                "details": {
                    "volume": row["volume"],
                    "avgVolume": row["avg_volume"],
                    "volumeRatio": round(ratio, 2),
                    "close": row["close"],
                    "summary": f"Volume {ratio:.1f}× 20-day average",
                },
            }
        )
    return signals
