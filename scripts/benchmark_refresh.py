#!/usr/bin/env python3
"""Benchmark refresh_company_scores_batch; reports per-ticker and aggregate p50/p95."""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app import create_app
from app.db import get_db
from app.repositories import Repository
from app.services.fundamentals import FundamentalsService


class _StubSec:
    def fetch_company_facts(self, cik):
        return {"facts": {}}

    def fetch_submissions(self, cik):
        return {"filings": {"recent": {}}}


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def run_benchmark(
    tickers: list[str],
    *,
    database_path: str | None = None,
    verbose: bool = False,
) -> dict:
    config = None
    if database_path:
        from app.config import Config

        config = Config(database_path=database_path)

    app = create_app(config)
    with app.app_context():
        repo = Repository(get_db())
        service = FundamentalsService(repo, _StubSec())
        result = service.refresh_company_scores_batch(tickers, verbose=verbose)
        timings = [item["elapsedSec"] for item in result.get("tickerTimings", []) if item.get("elapsedSec") is not None]
        return {
            "tickers": tickers,
            "refreshed": result.get("tickers", []),
            "skipped": result.get("skipped", []),
            "periodsWritten": result.get("periodsWritten", 0),
            "elapsedSec": result.get("elapsedSec", 0.0),
            "tickerTimings": result.get("tickerTimings", []),
            "p50Sec": round(_percentile(timings, 50), 3),
            "p95Sec": round(_percentile(timings, 95), 3),
            "meanSec": round(statistics.mean(timings), 3) if timings else 0.0,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tickers",
        default="AAPL,MSFT,GME",
        help="Comma-separated tickers (default: AAPL,MSFT,GME)",
    )
    parser.add_argument(
        "--database",
        default=None,
        help="Override STOCK_TRACKER_DB_PATH / default data/stock_tracker.sqlite3",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log per-ticker refresh timing at INFO",
    )
    args = parser.parse_args(argv)

    tickers = [item.strip().upper() for item in args.tickers.split(",") if item.strip()]
    if not tickers:
        print("benchmark_refresh: no tickers provided", file=sys.stderr)
        return 2

    summary = run_benchmark(tickers, database_path=args.database, verbose=args.verbose)
    print(f"benchmark_refresh: tickers={len(tickers)} refreshed={len(summary['refreshed'])}")
    print(f"  total_elapsed={summary['elapsedSec']:.3f}s periods={summary['periodsWritten']}")
    print(f"  per_ticker p50={summary['p50Sec']:.3f}s p95={summary['p95Sec']:.3f}s mean={summary['meanSec']:.3f}s")
    if args.verbose:
        for item in summary["tickerTimings"]:
            print(
                f"  {item['ticker']}: periods={item['periods']} elapsed={item['elapsedSec']:.3f}s"
            )
    if summary["skipped"]:
        print(f"  skipped={len(summary['skipped'])}", file=sys.stderr)
        for item in summary["skipped"]:
            print(f"    - {item['ticker']}: {item['reason']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
