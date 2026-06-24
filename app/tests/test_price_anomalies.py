"""Tests for price/volume anomaly detection."""

from __future__ import annotations

from datetime import date, timedelta

from app.db import get_db
from app.repositories import Repository
from app.services.price_anomalies import detect_unusual_volume_signals


def _seed_prices(repo: Repository, ticker: str, volumes: list[int]) -> None:
    start = date.today() - timedelta(days=len(volumes))
    rows = []
    for idx, volume in enumerate(volumes):
        day = (start + timedelta(days=idx)).isoformat()
        rows.append((ticker, day, 100.0 + idx, volume, "test"))
    repo.conn.executemany(
        """
        INSERT INTO prices (ticker, date, close, volume, source)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(ticker, date, source) DO UPDATE SET close = excluded.close, volume = excluded.volume
        """,
        rows,
    )
    repo.commit()


def test_detect_unusual_volume_signals_spike(app):
    with app.app_context():
        repo = Repository(get_db())
        baseline = [100_000] * 20
        baseline.append(400_000)
        _seed_prices(repo, "VOL1", baseline)
        signals = detect_unusual_volume_signals(repo, limit=10, lookback_days=25)
        vol1 = [row for row in signals if row["ticker"] == "VOL1"]
        assert len(vol1) == 1
        assert vol1[0]["details"]["volumeRatio"] >= 2.5


def test_detect_unusual_volume_ignores_normal_volume(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_prices(repo, "VOL2", [120_000] * 21)
        signals = detect_unusual_volume_signals(repo, limit=10)
        assert all(row["ticker"] != "VOL2" for row in signals)
