#!/usr/bin/env python3
"""Validate scoring outputs against golden fixtures and seed-ticker ranges."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app import create_app
from app.db import get_db
from app.repositories import Repository
from app.services.fundamentals import pivot_fundamentals_rows
from app.services.scoring import compute_scores_for_periods
from app.services.verification_spec import (
    GOLDEN_CURRENT_ROW,
    GOLDEN_EXPECTED_LATEST,
    GOLDEN_PRICES_BY_PERIOD,
    GOLDEN_PRIOR_ROW,
    GOLDEN_TOLERANCE,
    SEED_TICKER_SPECS,
    score_within_range,
    score_within_tolerance,
)


def _format_diff(metric: str, actual, expected, detail: str) -> str:
    return f"{metric}: actual={actual!r} expected={expected!r} ({detail})"


def validate_fixture_golden() -> list[str]:
    records = compute_scores_for_periods(
        [GOLDEN_CURRENT_ROW, GOLDEN_PRIOR_ROW],
        prices_by_period=GOLDEN_PRICES_BY_PERIOD,
    )
    if not records:
        return ["fixture: no score records produced"]
    latest = records[0]
    failures: list[str] = []

    if latest.get("period_end") != GOLDEN_EXPECTED_LATEST["period_end"]:
        failures.append(
            _format_diff("period_end", latest.get("period_end"), GOLDEN_EXPECTED_LATEST["period_end"], "mismatch")
        )

    for metric, expected in GOLDEN_EXPECTED_LATEST.items():
        if metric == "period_end":
            continue
        actual = latest.get(metric)
        tol = GOLDEN_TOLERANCE.get(metric, {"rel": 0.01, "abs": 0.0})
        if not score_within_tolerance(
            actual,
            expected,
            rel_tol=tol.get("rel", 0.01),
            abs_tol=tol.get("abs", 0.0),
        ):
            failures.append(_format_diff(metric, actual, expected, "golden tolerance exceeded"))
    return failures


def validate_db_seeds(database_path: str | None = None) -> list[str]:
    config = None
    if database_path:
        from app.config import Config

        config = Config(database_path=database_path)

    app = create_app(config)
    failures: list[str] = []

    with app.app_context():
        repo = Repository(get_db())
        for spec in SEED_TICKER_SPECS:
            ticker = spec["ticker"]
            company = repo.get_company_by_ticker(ticker)
            if not company:
                failures.append(f"{ticker}: company not found in database")
                continue

            rows = repo.fetch_fundamentals_rows([ticker], dimension="ARY")
            annual_rows = pivot_fundamentals_rows(rows)
            if not annual_rows:
                failures.append(f"{ticker}: no ARY fundamentals")
                continue

            prices_by_period: dict[str, float | None] = {}
            for row in annual_rows:
                period_end = row.get("calendardate")
                if period_end:
                    prices_by_period[period_end] = repo.fetch_price_near_date(ticker, period_end)

            records = compute_scores_for_periods(annual_rows, prices_by_period=prices_by_period)
            if not records:
                failures.append(f"{ticker}: score recompute returned no records")
                continue

            latest = records[0]
            if spec.get("latest_period") and latest.get("period_end") != spec["latest_period"]:
                failures.append(
                    _format_diff(
                        f"{ticker}.period_end",
                        latest.get("period_end"),
                        spec["latest_period"],
                        "baseline period drift — refresh docs/VERIFICATION_TICKERS.md",
                    )
                )

            for metric, metric_spec in spec["metrics"].items():
                actual = latest.get(metric)
                if not score_within_range(actual, metric_spec):
                    failures.append(
                        _format_diff(
                            f"{ticker}.{metric}",
                            actual,
                            f"[{metric_spec.get('min')}, {metric_spec.get('max')}]",
                            "out of range",
                        )
                    )
                    continue
                pinned = metric_spec.get("value")
                if pinned is not None and not score_within_tolerance(actual, pinned, rel_tol=0.02, abs_tol=0.05):
                    failures.append(
                        _format_diff(
                            f"{ticker}.{metric}",
                            actual,
                            pinned,
                            "pinned baseline drift >2%",
                        )
                    )

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("fixture", "db", "all"),
        default="fixture",
        help="fixture=golden rows only; db=seed tickers from SQLite; all=both",
    )
    parser.add_argument(
        "--database",
        default=None,
        help="Override STOCK_TRACKER_DB_PATH / default data/stock_tracker.sqlite3",
    )
    args = parser.parse_args(argv)

    failures: list[str] = []
    if args.mode in ("fixture", "all"):
        failures.extend(validate_fixture_golden())
    if args.mode in ("db", "all"):
        failures.extend(validate_db_seeds(args.database))

    if failures:
        print("validate_scores: FAIL", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print(f"validate_scores: OK (mode={args.mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
