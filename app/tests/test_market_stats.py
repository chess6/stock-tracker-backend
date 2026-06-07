from __future__ import annotations

from app.services.prices import PricesService


def test_pct_change():
    assert PricesService._pct_change(110, 100) == 10.0
    assert PricesService._pct_change(100, 0) is None
