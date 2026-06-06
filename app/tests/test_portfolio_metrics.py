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


def test_build_company_metrics_bank_pretax_ebitda_proxy():
    row = {
        "sharesbas": 1_000_000_000.0,
        "revenue": None,
        "netinc": 50_000_000_000.0,
        "taxexp": 15_000_000_000.0,
        "interestexp": 20_000_000_000.0,
        "ncfo": 80_000_000_000.0,
        "debt": 200_000_000_000.0,
        "eps": 5.0,
        "cashneq": 50_000_000_000.0,
    }
    metrics = build_company_metrics(row, price=100.0)
    assert metrics["sfcfp"] == 80.0
    enterprise_value = 100_000_000_000.0 + 200_000_000_000.0 - 50_000_000_000.0
    assert math.isclose(metrics["ebitdaEv"], 85_000_000_000.0 / enterprise_value, rel_tol=1e-9)


def test_build_company_metrics_ncfo_proxy_when_capex_missing():
    row = {
        "sharesbas": 2_697_032_375.0,
        "revenue": 182_447_000_000.0,
        "ncfo": -147_782_000_000.0,
        "netinc": 57_048_000_000.0,
        "eps": 20.02,
    }
    metrics = build_company_metrics(row, price=312.37)
    assert math.isclose(
        metrics["sfcfp"],
        row["ncfo"] / row["sharesbas"],
        rel_tol=1e-9,
    )


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
