"""Smoke tests for scripts/validate_scores.py helpers."""

from scripts.validate_scores import validate_fixture_golden


def test_validate_fixture_golden_passes():
    assert validate_fixture_golden() == []
