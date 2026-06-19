#!/usr/bin/env python3
"""Print and manage Stock Tracker configuration, feature flags, and capabilities.

Usage:
  python scripts/show_capabilities.py              # full reference + current flag state
  python scripts/show_capabilities.py flags        # feature flags only (resolved from DB)
  python scripts/show_capabilities.py enable FLAG  # persist flag ON in SQLite app_config
  python scripts/show_capabilities.py disable FLAG # persist flag OFF in SQLite app_config
  python scripts/show_capabilities.py env FLAG     # print shell export for one flag
  python scripts/show_capabilities.py env          # print all flag export lines (commented)

Resolution order for feature flags:
  ENV (STOCK_TRACKER_FF_<KEY>) → SQLite app_config → default (False)

Equivalent admin API (when backend is running):
  GET  /api/admin/config
  POST /api/admin/config  {"experimental_research_queue": true}
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from textwrap import indent

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import Config
from app.db import connect_db, init_db
from app.repositories import Repository
from app.services.feature_flags import FLAG_DEFAULTS, KNOWN_FLAGS, is_enabled, resolve_flags

FLAG_NOTES: dict[str, str] = {
    "experimental_composite_rank": (
        "Article enrichment computes rank_score during pipeline finalization "
        "(article_pipeline.py)."
    ),
    "experimental_research_composite_rank": (
        "Reserved for gating /api/research/rank routes; flag is defined but routes "
        "are currently always available."
    ),
    "embedding_heavy_retag": (
        "Default ON for embedding-heavy paths: admin retag/enrich, pipeline_refresh, "
        "and enrich_metadata worker job (unless request body overrides)."
    ),
    "experimental_signal_ranking": (
        "Orchestrator dispatches agents on analysis_completed events "
        "(orchestration/orchestrator/dispatcher.py)."
    ),
    "experimental_thesis_versioning": (
        "Thesis snapshot skip logic (should_skip_thesis_recompute) is implemented; "
        "flag is defined for future pipeline gating."
    ),
    "experimental_research_queue": (
        "Enables GET /api/research/queue, POST /api/research/queue/<ticker>/dismiss, "
        "and nightly worker job build_research_queue (rank/insider/narrative/catalyst events)."
    ),
    "experimental_backtest_route": (
        "Reserved for a future HTTP backtest endpoint; point-in-time backtest is available "
        "now via: python scripts/backtest.py --help"
    ),
}

ENV_VARS: list[tuple[str, str, str]] = [
    ("SEC_USER_AGENT", "required", "SEC fair-access contact string (real email)."),
    ("STOCK_TRACKER_DB_PATH", "optional", "SQLite database path (default: data/stock_tracker.sqlite3)."),
    ("STOCK_TRACKER_DEFAULT_TICKERS", "optional", "Comma-separated tickers for worker nightly ETL."),
    ("STOCK_TRACKER_REQUEST_TIMEOUT", "optional", "HTTP timeout seconds for SEC/news fetches (default: 20)."),
    ("STOCK_TRACKER_LOG_DIR", "optional", "Log directory (default: stock_tracker_backend/logs)."),
    ("STOCK_TRACKER_FF_<FLAG>", "optional", "Override any feature flag; e.g. STOCK_TRACKER_FF_EXPERIMENTAL_RESEARCH_QUEUE=true"),
    ("NASDAQ_API_KEY", "optional", "Nasdaq Data Link fallback when SQLite company cache is empty."),
    ("ADMIN_API_KEY", "optional", "When set, admin routes require X-Admin-Api-Key header."),
    ("NLP_DEVICE", "optional", "FinBERT/embeddings device: auto (default), cpu, or cuda."),
    ("FLASK_DEBUG", "optional", "Flask dev server debug mode (api.py standalone only)."),
    ("BASE_URL", "optional", "Shell scripts' API base (default: http://localhost:5000)."),
    ("GUNICORN_BIND", "optional", "Gunicorn bind address (default: 0.0.0.0:5000)."),
    ("GUNICORN_WORKERS", "optional", "Gunicorn worker processes (default: 1)."),
    ("GUNICORN_THREADS", "optional", "Gunicorn threads per worker (default: CPU count, min 4)."),
    ("GUNICORN_TIMEOUT", "optional", "Gunicorn request timeout seconds (default: 300)."),
]

WORKER_JOBS: list[tuple[str, str]] = [
    ("sync_companies", "Refresh company directory from SEC."),
    ("refresh_fundamentals", "SEC CompanyFacts fundamentals ingest."),
    ("refresh_company_scores", "Recompute Piotroski, Altman Z, Beneish, survivability."),
    ("snapshot_composite_ranks", "Nightly composite rank snapshots (deep_value, turnaround, …)."),
    ("build_research_queue", "Build research queue from rank/insider/narrative/catalyst signals (flag-gated)."),
    ("snapshot_narrative_intelligence", "Nightly narrative divergence snapshots."),
    ("enrich_metadata", "Company metadata enrichment pass."),
    ("refresh_macro", "Macro snapshot (SPY benchmark, etc.)."),
    ("refresh_insiders", "SEC Form 4 insider transactions."),
    ("ingest_default_feeds", "RSS feed poll."),
    ("enrich_articles", "Article NLP pipeline (sentiment, events, entity linking)."),
    ("pipeline_refresh", "Admin pipeline modes (force_refresh, lightweight_daily_refresh, …)."),
    ("refresh_prices", "Stooq/yfinance price refresh."),
    ("refresh_edgar_events", "8-K / EDGAR event ingest."),
    ("bootstrap", "Full initial data load."),
]

CLI_TOOLS: list[tuple[str, str]] = [
    ("./bootstrap.sh", "POST /api/admin/bootstrap for first-time data load."),
    ("./refresh_data.sh", "Fundamentals, prices, insiders, feeds refresh."),
    ("./enrich_articles.sh", "Batch article enrichment (FAST=1, RETAG=1, FORCE=1 env toggles)."),
    ("./backfill_market_reactions.sh", "Backfill article market reaction metrics."),
    ("./worker.sh", "Background ingestion_jobs worker + nightly scheduler (02:00 ETL)."),
    ("python scripts/backtest.py", "Point-in-time composite rank backtest (JSON/CSV output)."),
    ("python scripts/validate_scores.py", "Score validation against verification tickers."),
    ("python scripts/benchmark_refresh.py", "Benchmark company_scores refresh latency."),
    ("./scripts/test_pipeline_modes.sh", "Smoke-test admin pipeline-refresh modes."),
    ("python scripts/show_capabilities.py", "This script — config reference and flag management."),
]

RESEARCH_ROUTES: list[tuple[str, str]] = [
    ("GET/POST /api/research/rank", "Composite opportunity ranking (custom weight_overrides on POST)."),
    ("GET /api/research/rank/history/<ticker>", "Rank history from company_rank_snapshots."),
    ("GET /api/research/rank/validation", "Forward-return rank validator."),
    ("GET /api/research/rank/baserate", "Gate base-rate validation."),
    ("GET /api/research/queue", "Research queue (requires experimental_research_queue)."),
    ("POST /api/research/queue/<ticker>/dismiss", "Dismiss queue item(s) for a ticker."),
    ("POST /api/research/screen", "Composable screener."),
    ("GET /api/research/gates|pillars|thesis/<ticker>", "Gate, pillar, and thesis evaluation."),
    ("GET /api/research/narrative/<ticker>", "Narrative divergence intelligence."),
    ("GET /api/research/insiders/clusters", "Cross-ticker insider cluster rankings."),
]


def _db_path(explicit: str | None) -> str:
    if explicit:
        return explicit
    return Config().database_path


def _repo(database_path: str | None) -> Repository:
    path = _db_path(database_path)
    init_db(path)
    return Repository(connect_db(path))


def _flag_env_key(flag: str) -> str:
    return f"STOCK_TRACKER_FF_{flag.upper()}"


def _truthy_env(flag: str) -> bool | None:
    raw = os.getenv(_flag_env_key(flag))
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _print_section(title: str, body: str) -> None:
    print(f"\n== {title} ==")
    print(body.rstrip())
    print()


def cmd_flags(repo: Repository) -> None:
    flags = resolve_flags(repo)
    lines = []
    for key in sorted(KNOWN_FLAGS):
        env_key = _flag_env_key(key)
        env_val = _truthy_env(key)
        stored = repo.get_config(key)
        resolved = flags[key]
        source = "default"
        if env_val is not None:
            source = "env"
        elif stored is not None:
            source = "sqlite"
        lines.append(
            f"{key}: {resolved!s:5}  (default={FLAG_DEFAULTS[key]!s:5}, source={source}, env={env_key})"
        )
    _print_section("Feature flags", "\n".join(lines))


def cmd_env_exports(flags: list[str] | None = None) -> None:
    keys = sorted(flags or KNOWN_FLAGS)
    for key in keys:
        env_key = _flag_env_key(key)
        active = os.getenv(env_key)
        if active is not None:
            print(f"export {env_key}={active!r}  # currently set in environment")
        else:
            print(f"# export {env_key}=true  # default={FLAG_DEFAULTS[key]}")


def cmd_enable(repo: Repository, flag: str) -> None:
    if flag not in KNOWN_FLAGS:
        raise SystemExit(f"Unknown flag: {flag}. Known: {', '.join(sorted(KNOWN_FLAGS))}")
    repo.set_config(flag, True)
    print(f"Enabled {flag} in SQLite app_config.")
    print(f"  Resolved now: {is_enabled(flag, repo)}")
    print(f"  Env override: export {_flag_env_key(flag)}=true")
    print("  Restart worker.sh if build_research_queue / nightly jobs should pick this up.")


def cmd_disable(repo: Repository, flag: str) -> None:
    if flag not in KNOWN_FLAGS:
        raise SystemExit(f"Unknown flag: {flag}. Known: {', '.join(sorted(KNOWN_FLAGS))}")
    repo.set_config(flag, False)
    print(f"Disabled {flag} in SQLite app_config.")
    print(f"  Resolved now: {is_enabled(flag, repo)}")


def cmd_show(repo: Repository) -> None:
    env_lines = []
    for name, kind, desc in ENV_VARS:
        env_lines.append(f"{name} ({kind})\n{indent(desc, '  ')}")
    _print_section("Environment variables", "\n\n".join(env_lines))

    flag_lines = []
    flags = resolve_flags(repo)
    for key in sorted(KNOWN_FLAGS):
        env_key = _flag_env_key(key)
        note = FLAG_NOTES.get(key, "")
        flag_lines.append(
            f"{key}\n"
            f"  default: {FLAG_DEFAULTS[key]}\n"
            f"  resolved: {flags[key]}\n"
            f"  env: {env_key}=true|false\n"
            f"  sqlite: repo.set_config({key!r}, True)  # or POST /api/admin/config\n"
            f"  {note}"
        )
    _print_section("Feature flags", "\n\n".join(flag_lines))

    worker_lines = [f"{name}: {desc}" for name, desc in WORKER_JOBS]
    _print_section("Worker job types (ingestion_jobs)", "\n".join(worker_lines))

    research_lines = [f"{route}: {desc}" for route, desc in RESEARCH_ROUTES]
    _print_section("Research API (/api/research)", "\n".join(research_lines))

    cli_lines = [f"{cmd}: {desc}" for cmd, desc in CLI_TOOLS]
    _print_section("CLI and shell scripts", "\n".join(cli_lines))

    _print_section(
        "Quick enable — research queue",
        "\n".join(
            [
                "export STOCK_TRACKER_FF_EXPERIMENTAL_RESEARCH_QUEUE=true",
                "# or persist in SQLite:",
                "python scripts/show_capabilities.py enable experimental_research_queue",
                "# or via API:",
                'curl -X POST http://localhost:5000/api/admin/config -H "Content-Type: application/json" \\',
                '  -d \'{"experimental_research_queue": true}\'',
                "# then verify:",
                "curl http://localhost:5000/api/research/queue?limit=10",
            ]
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stock Tracker configuration reference and feature-flag management.",
    )
    parser.add_argument(
        "--db",
        dest="database_path",
        help="SQLite path (default: STOCK_TRACKER_DB_PATH or data/stock_tracker.sqlite3)",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("show", help="Full capabilities reference (default)")
    sub.add_parser("flags", help="Show resolved feature flag state")
    env_parser = sub.add_parser("env", help="Print shell export lines for feature flags")
    env_parser.add_argument("flag", nargs="?", help="Single flag name (optional)")

    enable_parser = sub.add_parser("enable", help="Persist a feature flag ON in SQLite")
    enable_parser.add_argument("flag", help="Feature flag key")

    disable_parser = sub.add_parser("disable", help="Persist a feature flag OFF in SQLite")
    disable_parser.add_argument("flag", help="Feature flag key")

    args = parser.parse_args()
    command = args.command or "show"
    repo = _repo(args.database_path)

    if command == "show":
        cmd_show(repo)
    elif command == "flags":
        cmd_flags(repo)
    elif command == "env":
        if args.flag:
            if args.flag not in KNOWN_FLAGS:
                raise SystemExit(f"Unknown flag: {args.flag}")
            cmd_env_exports([args.flag])
        else:
            cmd_env_exports()
    elif command == "enable":
        cmd_enable(repo, args.flag)
    elif command == "disable":
        cmd_disable(repo, args.flag)
    else:
        parser.error(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
