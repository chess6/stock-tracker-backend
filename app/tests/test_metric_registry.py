"""Tests for canonical metric registry API contract."""

import json

from app.services.metric_registry import (
    METRIC_REGISTRY,
    api_key_for,
    canonical_key,
    registry_for_api,
)


def test_registry_for_api_covers_all_metrics():
    entries = registry_for_api()
    assert len(entries) == len(METRIC_REGISTRY)
    keys = [item["key"] for item in entries]
    assert keys == sorted(METRIC_REGISTRY.keys())


def test_registry_for_api_serializes_heatmap_fields():
    gross = next(item for item in registry_for_api() if item["key"] == "gross_margin")
    assert gross["api_key"] == "grossMargin"
    assert gross["heatmap_mode"] == "percentile"
    assert gross["higher_is_better"] is True
    assert gross["danger_threshold"] == 0.0
    assert gross["excellent_threshold"] == 0.50
    assert gross["format"] == "percent"
    assert gross["screener_supported"] is True


def test_registry_score_metrics_include_score_type():
    entries = {item["key"]: item for item in registry_for_api()}
    assert entries["piotroski_f"]["score_type"] == "piotroski"
    assert entries["altman_z"]["score_type"] == "altman"
    assert entries["beneish_m"]["score_type"] == "beneish"
    assert entries["survivability"]["score_type"] == "survivability"
    assert entries["piotroski_f"]["heatmap_mode"] == "score_tier"


def test_registry_valuation_thresholds():
    pe = next(item for item in registry_for_api() if item["key"] == "pe")
    assert pe["higher_is_better"] is False
    assert pe["danger_threshold"] == 40.0
    assert pe["excellent_threshold"] == 8.0


def test_registry_api_json_round_trip():
    payload = {"metrics": registry_for_api()}
    raw = json.dumps(payload)
    parsed = json.loads(raw)
    assert len(parsed["metrics"]) == len(METRIC_REGISTRY)


def test_canonical_key_mapping():
    assert canonical_key("grossMargin") == "gross_margin"
    assert canonical_key("de") == "debt_equity"
    assert canonical_key("unknown") is None
    assert api_key_for("net_margin") == "netMargin"


def test_metrics_registry_route_matches_registry(client):
    response = client.get("/api/research/metrics/registry")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["metrics"] == registry_for_api()
