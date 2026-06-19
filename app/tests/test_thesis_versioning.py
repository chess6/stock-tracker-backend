"""Thesis/pillar version columns and skip logic (P3)."""

from __future__ import annotations

from datetime import date, timedelta

from app.db import get_db
from app.repositories import Repository
from app.services.freshness import CURRENT_PILLAR_VERSION, CURRENT_SCORING_VERSION, CURRENT_THESIS_VERSION
from app.services.pipeline_refresh import should_skip_thesis_recompute


def test_should_skip_thesis_recompute_without_snapshot(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "SKIP1", "name": "Skip Test Co"}])
        company = repo.get_company_by_ticker("SKIP1")
        assert should_skip_thesis_recompute(
            repo,
            company["id"],
            CURRENT_SCORING_VERSION,
            CURRENT_THESIS_VERSION,
        ) is False


def test_should_skip_thesis_recompute_with_fresh_snapshot(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "SKIP2", "name": "Skip Fresh Co"}])
        company = repo.get_company_by_ticker("SKIP2")
        repo.upsert_thesis_snapshot(
            {
                "company_id": company["id"],
                "ticker": "SKIP2",
                "snapshot_date": date.today().isoformat(),
                "thesis_version": CURRENT_THESIS_VERSION,
                "pillar_version": CURRENT_PILLAR_VERSION,
                "scoring_version": CURRENT_SCORING_VERSION,
                "computed_at": date.today().isoformat(),
            }
        )
        assert should_skip_thesis_recompute(
            repo,
            company["id"],
            CURRENT_SCORING_VERSION,
            CURRENT_THESIS_VERSION,
        ) is True


def test_should_skip_thesis_recompute_when_version_bumped(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "SKIP3", "name": "Skip Bump Co"}])
        company = repo.get_company_by_ticker("SKIP3")
        repo.upsert_thesis_snapshot(
            {
                "company_id": company["id"],
                "ticker": "SKIP3",
                "snapshot_date": date.today().isoformat(),
                "thesis_version": CURRENT_THESIS_VERSION,
                "pillar_version": CURRENT_PILLAR_VERSION,
                "scoring_version": CURRENT_SCORING_VERSION,
                "computed_at": date.today().isoformat(),
            }
        )
        assert should_skip_thesis_recompute(
            repo,
            company["id"],
            CURRENT_SCORING_VERSION,
            CURRENT_THESIS_VERSION + 1,
        ) is False


def test_should_skip_thesis_recompute_when_stale(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "SKIP4", "name": "Skip Stale Co"}])
        company = repo.get_company_by_ticker("SKIP4")
        stale_date = (date.today() - timedelta(days=10)).isoformat()
        repo.upsert_thesis_snapshot(
            {
                "company_id": company["id"],
                "ticker": "SKIP4",
                "snapshot_date": stale_date,
                "thesis_version": CURRENT_THESIS_VERSION,
                "pillar_version": CURRENT_PILLAR_VERSION,
                "scoring_version": CURRENT_SCORING_VERSION,
                "computed_at": stale_date,
            }
        )
        assert should_skip_thesis_recompute(
            repo,
            company["id"],
            CURRENT_SCORING_VERSION,
            CURRENT_THESIS_VERSION,
        ) is False
