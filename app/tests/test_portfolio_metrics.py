from __future__ import annotations

import math

from app.services.fundamentals import build_company_metrics


def test_build_company_metrics_mcd_golden_ratios():
    row = {
        "sharesbas": 712_630_000.0,
        "revenue": 26_885_000_000.0,
        "equity": -1_791_000_000.0,
        "ncfo": 2_418_600_000.0,
        "capex": 2_100_000_000.0,
        "eps": 11.95,
        "ebitda": 12_850_000_000.0,
        "debt": 40_698_000_000.0,
        "cashneq": 774_000_000.0,
    }
    price = 279.84
    metrics = build_company_metrics(row, price=price)

    assert metrics["marketCap"] == row["sharesbas"] * price
    assert math.isclose(metrics["sp"], row["revenue"] / row["sharesbas"], rel_tol=1e-9)
    assert math.isclose(metrics["bp"], row["equity"] / row["sharesbas"], rel_tol=1e-9)
    assert math.isclose(metrics["cfop"], row["ncfo"] / row["sharesbas"], rel_tol=1e-9)
    assert math.isclose(metrics["sfcfp"], (row["ncfo"] - row["capex"]) / row["sharesbas"], rel_tol=1e-9)
    enterprise_value = metrics["marketCap"] + row["debt"] - row["cashneq"]
    assert math.isclose(metrics["ebitdaEv"], row["ebitda"] / enterprise_value, rel_tol=1e-9)


def test_build_company_metrics_derives_fcf_and_ebitda_proxy():
    row = {
        "sharesbas": 10.0,
        "revenue": 100.0,
        "equity": 50.0,
        "ncfo": 20.0,
        "capex": 5.0,
        "opinc": 12.0,
        "eps": 1.0,
    }
    metrics = build_company_metrics(row, price=10.0)
    assert math.isclose(metrics["sfcfp"], 1.5, rel_tol=1e-9)
    assert metrics["ebitdaEv"] == 12.0 / (100.0 + 0 - 0)
