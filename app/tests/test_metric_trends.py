"""Tests for server-side metric trend helpers."""

import pytest

from app.services.metric_trends import (
    build_metric_trends,
    cagr_pct,
    margin_delta,
    trend_summary,
    yoy_pct,
)


def test_yoy_pct_basic():
    assert yoy_pct(110, 100) == 10.0
    assert yoy_pct(90, 100) == -10.0
    assert yoy_pct(100, 0) is None
    assert yoy_pct(None, 100) is None


def test_cagr_pct_basic():
    assert cagr_pct(100, 121, 2) == pytest.approx(10.0)
    assert cagr_pct(-100, -121, 2) == pytest.approx(10.0)
    assert cagr_pct(0, 100, 3) is None
    assert cagr_pct(100, -50, 3) is None


def test_margin_delta():
    assert margin_delta(0.25, 0.20) == pytest.approx(0.05)
    assert margin_delta(None, 0.2) is None


def test_trend_summary_newest_first():
    summary = trend_summary([150.0, 100.0, 80.0, 60.0, 50.0, 40.0])
    assert summary["yoy"] == 50.0
    assert summary["cagr3y"] is not None
    assert summary["cagr5y"] is not None


def test_trend_summary_mutual_inclusivity():
    summary = trend_summary([150.0, 100.0, 80.0, 60.0, -50.0])
    assert summary["yoy"] is None
    assert summary["cagr5y"] is None


def test_build_metric_trends_from_periods():
    periods = [
        {"metrics": {"grossMargin": 0.45, "revenue": 1200}},
        {"metrics": {"grossMargin": 0.40, "revenue": 1000}},
        {"metrics": {"grossMargin": 0.35, "revenue": 900}},
        {"metrics": {"grossMargin": 0.30, "revenue": 800}},
    ]
    trends = build_metric_trends(periods, ["grossMargin", "revenue"])
    assert trends["grossMargin"]["yoy"] == pytest.approx(12.5)
    assert trends["revenue"]["yoy"] == pytest.approx(20.0)
    assert "cagr3y" in trends["revenue"]
