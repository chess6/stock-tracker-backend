# Stock Tracker Backend

[![License](https://img.shields.io/github/license/chess6/stock-tracker-backend)](LICENSE)

Local-first Flask API and SQLite cache for a **deep-value research workstation**. Ingests **SEC EDGAR** fundamentals, **Stooq/yfinance** prices, **RSS** news with entity linking, and optional **Nasdaq Data Link** fallback. Pairs with the [frontend repo](https://github.com/chess6/stock-tracker-frontend).

| | |
|---|---|
| [Contributing](CONTRIBUTING.md) | Setup, tests, PR guidelines |
| [Security](SECURITY.md) | Report vulnerabilities privately |
| [Code of Conduct](CODE_OF_CONDUCT.md) | Community standards |

## Architecture

Local-data-first: admin/bootstrap endpoints fill SQLite; the UI reads from cache on every request.

| Component | Path |
|-----------|------|
| API entry | `api.py` |
| Routes & services | `app/` |
| SQLite DB | `data/stock_tracker.sqlite3` |
| Background worker | `worker.py` |
| Shell helpers | `start.sh`, `stop.sh`, `worker.sh`, `worker_stop.sh`, `bootstrap.sh`, `refresh_data.sh` |
| Capabilities reference | `scripts/capabilities.sh` — env vars, feature flags, worker jobs, research routes |
| CLI tester | `api_tester.py` |
| AI orchestrator | `orchestration/` — event-driven agents (see below) |
| Entity linking docs | `docs/ENTITY_LINKING.md` — ingest / enrich / retag pipeline |

## Entity linking

RSS articles are tagged to tickers through a multi-stage linker (cashtags, aliases, optional embeddings). Ingest uses rules-only tagging; enrichment and retag refine matches stored in `article_company`. The News API shows only high-confidence, high-trust strategies.

See **[docs/ENTITY_LINKING.md](docs/ENTITY_LINKING.md)** for match strategies, false-positive gates, API endpoints, and `enrich_articles.sh` usage.

```bash
./enrich_articles.sh                                    # enrich pending → auto retag
RETAG=1 RETAG_ALL=1 BATCH=100 ./enrich_articles.sh      # rules-only retag (~5s)
```

## AI orchestration layer

Event-driven agents propose actions; validators execute only safe, approved operations.

```bash
sh orchestrator_start.sh       # event loop (python -m orchestration.worker)
sh orchestrator_api_start.sh   # FastAPI dashboard http://127.0.0.1:5001
```

| Endpoint | Purpose |
|----------|---------|
| `GET /dashboard` | Active agents, failures, approvals, confidence scores |
| `GET /events` | Pending/failed events |
| `POST /approvals/{id}/approve` | Human approval gate |

Docs: `orchestration/ARCHITECTURE.md`, `orchestration/MIGRATION_PLAN.md`

Optional env: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `REDIS_URL`, `AI_DEFAULT_PROVIDER=ollama`

## Prerequisites

- Python 3.10+
- `SEC_USER_AGENT` in `.env` (required — use your email for SEC requests)
- `NASDAQ_API_KEY` (optional — live fallback when cache is empty)

## Setup

```bash
pip install -r requirements.txt
```

Create `.env` from the example file:

```bash
cp .env.example .env   # then edit SEC_USER_AGENT
```

## Configuration & feature flags

Feature flags default **OFF**. Resolution order:

**ENV (`STOCK_TRACKER_FF_<KEY>`) → SQLite `app_config` → default (False)**

ENV always wins over SQLite. After changing `.env`, restart the API and worker.

### Capabilities script

```bash
./scripts/capabilities.sh              # full reference + current resolved flag state
./scripts/capabilities.sh flags        # feature flags only
./scripts/capabilities.sh enable FLAG  # persist ON in SQLite app_config
./scripts/capabilities.sh disable FLAG
./scripts/capabilities.sh env          # print STOCK_TRACKER_FF_* export lines
```

Equivalent admin API (backend running):

```bash
curl http://localhost:5000/api/admin/config
curl -X POST http://localhost:5000/api/admin/config \
  -H "Content-Type: application/json" \
  -d '{"experimental_research_queue": true}'
```

### All flags

| Flag | What it does today |
|------|-------------------|
| `experimental_composite_rank` | Article enrichment computes `rank_score` during pipeline finalization |
| `embedding_heavy_retag` | Embeddings default ON for admin retag/enrich, pipeline refresh, and `enrich_metadata` worker job |
| `experimental_research_queue` | Gates `GET /api/research/queue`, dismiss route, and nightly `build_research_queue` worker job |
| `experimental_signal_ranking` | Orchestrator dispatches agents on `analysis_completed` events (requires orchestrator running) |
| `experimental_research_composite_rank` | Defined; rank routes are **not** gated in code yet |
| `experimental_thesis_versioning` | Defined; thesis skip logic exists but is **not** gated by this flag yet |
| `experimental_backtest_route` | Defined; no HTTP route — use `python scripts/backtest.py` instead |

Enable all flags in SQLite:

```bash
for flag in experimental_composite_rank experimental_research_composite_rank \
  embedding_heavy_retag experimental_signal_ranking experimental_thesis_versioning \
  experimental_research_queue experimental_backtest_route; do
  ./scripts/capabilities.sh enable "$flag"
done
./scripts/capabilities.sh flags
```

Or set all in `.env` (see `.env.example` for `STOCK_TRACKER_FF_*` keys).

### Other useful env vars

| Variable | Purpose |
|----------|---------|
| `SEC_USER_AGENT` | **Required** — real contact email for SEC fair-access |
| `STOCK_TRACKER_DB_PATH` | SQLite path (default: `data/stock_tracker.sqlite3`) |
| `STOCK_TRACKER_DEFAULT_TICKERS` | Comma-separated tickers for worker nightly ETL |
| `STOCK_TRACKER_REQUEST_TIMEOUT` | HTTP timeout for SEC/news (default: 20) |
| `STOCK_TRACKER_LOG_DIR` | Log directory (default: `logs/`) |
| `NLP_DEVICE` | `auto` (default), `cpu`, or `cuda` for FinBERT/embeddings |
| `ADMIN_API_KEY` | When set, admin routes require `X-Admin-Api-Key` header |
| `NASDAQ_API_KEY` | Optional Nasdaq Data Link fallback when cache is empty |
| `GUNICORN_BIND`, `GUNICORN_WORKERS`, `GUNICORN_THREADS`, `GUNICORN_TIMEOUT` | Production API tuning (`gunicorn.conf.py`) |

### Max capability checklist

Flags alone do not fully populate the system. For the fullest local setup:

1. `./scripts/capabilities.sh flags` — confirm resolved flag state
2. Enable desired flags (SQLite, `.env`, or `POST /api/admin/config`)
3. Restart API and worker: `sh stop.sh && sh start.sh` and `sh worker_stop.sh && sh worker.sh`
4. Load data: `sh bootstrap.sh` or `sh refresh_data.sh`
5. Enrich articles: `./enrich_articles.sh` (avoid `FAST=1` for full NLP)
6. Backfill market reactions for research narrative: `./backfill_market_reactions.sh AAPL,MSFT`
7. Verify research queue: `curl http://localhost:5000/api/research/queue?limit=10` (needs `experimental_research_queue`)
8. Run backtest CLI: `python scripts/backtest.py --help`
9. Optional orchestrator: `sh orchestrator_start.sh` (needs `experimental_signal_ranking` + AI provider env)

Trigger research queue build immediately (worker running):

```bash
curl -X POST http://localhost:5000/api/admin/enqueue-job \
  -H "Content-Type: application/json" \
  -d '{"job_type":"build_research_queue","payload":{"limit":50}}'
```

## Run & stop

```bash
sh start.sh      # → http://localhost:5000 (Gunicorn)
sh stop.sh       # uses .backend.pid
```

Production WSGI server: **Gunicorn** on Linux/macOS (`gunicorn.conf.py`, entry `wsgi:app`). On Windows use **Waitress** instead (`pip install waitress`, then `waitress-serve --listen=0.0.0.0:5000 wsgi:app`).

Direct production command (from `stock_tracker_backend/`):

```bash
gunicorn --config gunicorn.conf.py wsgi:app
```

Override workers/bind via env: `GUNICORN_BIND`, `GUNICORN_WORKERS`, `GUNICORN_THREADS`, `GUNICORN_TIMEOUT`.

Logs: `backend.out`

**Port 5000 busy but `stop.sh` says not running** (orphan process):

```bash
ps -ef | grep "python3 api.py"
kill <PID>              # or: fuser -k 5000/tcp
sh start.sh
```

Always use `start.sh` / `stop.sh` — running `python3 api.py` directly leaves no PID file.

## Background worker

Polls RSS every 45 minutes; nightly full ETL at 02:00.

```bash
sh worker.sh
sh worker_stop.sh     # uses .worker.pid
```

Jobs persist in the `ingestion_jobs` table. Requires the same `.env` / DB path as the API.

Nightly schedule (02:00) enqueues, in priority order:

| Job | Purpose |
|-----|---------|
| `sync_companies` | SEC company directory |
| `refresh_fundamentals` | SEC CompanyFacts |
| `refresh_prices` | Stooq/yfinance OHLCV |
| `refresh_company_scores` | Piotroski, Altman Z, Beneish, survivability |
| `snapshot_composite_ranks` | Composite rank snapshots (`deep_value`, `turnaround`, …) |
| `build_research_queue` | Rank/insider/narrative/catalyst queue (**flag-gated**: `experimental_research_queue`) |
| `snapshot_narrative_intelligence` | Narrative divergence snapshots |
| `enrich_metadata` | Company sector/industry metadata |
| `refresh_macro` | Benchmark ETF prices |
| `refresh_insiders` | SEC Form 4 |
| `ingest_default_feeds` | RSS poll |
| `enrich_articles` | Article NLP pipeline |

Other job types (`pipeline_refresh`, `bootstrap`, `refresh_edgar_events`, …) are available via `POST /api/admin/enqueue-job`.

## First-time data load

**CLI (recommended for headless setup):**

```bash
sh bootstrap.sh JPM,MCD
# lighter daily refresh:
sh refresh_data.sh JPM,MCD
```

**curl:**

```bash
curl -X POST "http://localhost:5000/api/admin/bootstrap?tickers=JPM,MCD"
```

**Admin UI:** start the frontend and open http://localhost:3000/admin (see frontend README).

## Admin API reference

All admin routes are unauthenticated — intended for localhost dev only.

### Bootstrap & refresh

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/admin/bootstrap?tickers=` | Full pipeline: sync companies → fundamentals → RSS (fast) → prices → insiders |
| POST | `/api/admin/sync-companies` | SEC `company_tickers.json` → `companies` (~10k rows) |
| POST | `/api/admin/refresh-fundamentals?tickers=` | SEC XBRL CompanyFacts → `fundamentals` (+ MRY/MRQ/MRT snapshots) |
| POST | `/api/admin/enrich-metadata?tickers=` or `?all=true` | SEC submissions → `companies.sector` / `industry` |
| POST | `/api/admin/refresh-prices?tickers=` | Stooq/yfinance OHLCV → `prices` |
| POST | `/api/admin/refresh-macro` | Benchmark ETF prices (SPY, QQQ, …) → `prices` |
| POST | `/api/admin/backfill-market-reactions?ticker=&limit=` | Recompute `article_market_reactions` (Research narrative / topEvents) |
| POST | `/api/admin/refresh-insiders?tickers=` | SEC Form 4 → `insider_transactions` |
| POST | `/api/admin/ingest-default-feeds` | Poll 53 RSS feeds; `forceRefresh=true` by default |
| POST | `/api/admin/dedup-articles` | Normalize dates, semantic dedup, keyword sentiment backfill |
| GET | `/api/admin/status` | Table counts, freshness timestamps, job queue stats |
| GET | `/api/admin/default-feeds` | Default RSS feed list |

### Jobs & custom feeds

```bash
# Queue RSS poll (requires worker.sh running)
curl -X POST http://localhost:5000/api/admin/enqueue-job \
  -H "Content-Type: application/json" \
  -d '{"job_type":"ingest_default_feeds","payload":{"force_refresh":true}}'

# Ingest a custom feed
curl -X POST http://localhost:5000/api/admin/ingest-feed \
  -H "Content-Type: application/json" \
  -d '{"feed_url":"https://example.com/rss.xml","name":"My Feed","category":"finance"}'
```

### Typical workflows

**New install:** `sh start.sh` → `sh bootstrap.sh JPM,MCD`

**Daily refresh:** `sh refresh_data.sh JPM,MCD` or run `sh worker.sh`

**Research narrative (sentiment vs price, topEvents):** macro benchmark must exist, then backfill reactions — not `FORCE=1 enrich`:

```bash
curl -X POST http://localhost:5000/api/admin/refresh-macro   # included in refresh_data.sh
./backfill_market_reactions.sh AAPL,MSFT                       # ~1s per ticker
```

**Stale news:** `curl -X POST http://localhost:5000/api/admin/ingest-default-feeds` then `dedup-articles`

### Feature flags

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/config` | Resolved flags, defaults, and stored `app_config` |
| POST | `/api/admin/config` | Set flags, e.g. `{"experimental_research_queue": true}` |

## Research API (`/api/research`)

Deep-value research endpoints. Most routes are always available; the research queue is flag-gated.

| Method | Path | Purpose |
|--------|------|---------|
| GET/POST | `/api/research/rank` | Composite ranking (`weight_overrides` on POST) |
| GET | `/api/research/rank/history/<ticker>` | Rank history from snapshots |
| GET | `/api/research/rank/validation` | Forward-return rank validator |
| GET | `/api/research/rank/baserate` | Gate base-rate validation |
| GET | `/api/research/queue` | Prioritized research events (**`experimental_research_queue`**) |
| POST | `/api/research/queue/<ticker>/dismiss` | Dismiss queue item(s) |
| POST | `/api/research/screen` | Composable screener |
| GET | `/api/research/gates/<ticker>` | Gate evaluation |
| GET | `/api/research/pillars/<ticker>` | Pillar scores |
| GET | `/api/research/thesis/<ticker>` | Full thesis assembly |
| GET | `/api/research/narrative/<ticker>` | Narrative divergence |
| GET | `/api/research/insiders/clusters` | Cross-ticker insider clusters |

## Public API routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/search?q=` | Ticker search |
| GET | `/api/ticker/<t>/financials` | Fundamentals + `metrics` |
| GET | `/api/tickers/top?tickers=` | Latest quotes |
| GET | `/api/tickers/daily-change?tickers=` | Prev/today close |
| GET | `/api/insiders/buying-sums?tickers=` | Insider buy aggregates |
| GET | `/api/ticker/<t>/news` | Ticker-linked articles |
| GET | `/api/news` | Deduped feed (`limit`, `offset`, `q`, `category`, `tickers`) |
| GET | `/api/macro/snapshot` | Macro dashboard tiles |
| GET | `/api/ticker/<t>/sf2` | Insider transactions (cache, then Nasdaq fallback) |

## API tester CLI

```bash
python api_tester.py portfolio --tickers JPM,MCD
python api_tester.py financials --tickers JPM --most-recent
python api_tester.py status
python api_tester.py bootstrap --tickers JPM,MCD
```

## CLI scripts

| Script | Purpose |
|--------|---------|
| `./scripts/capabilities.sh` | Config reference, feature flag enable/disable/env |
| `python scripts/backtest.py` | Point-in-time composite rank backtest (JSON/CSV) |
| `python scripts/validate_scores.py` | Score validation against verification tickers |
| `python scripts/benchmark_refresh.py` | Benchmark `company_scores` refresh latency |
| `./scripts/test_pipeline_modes.sh` | Smoke-test admin pipeline-refresh modes |
| `./enrich_articles.sh` | Batch enrichment (`FAST=1`, `RETAG=1`, `FORCE=1` env toggles) |
| `./backfill_market_reactions.sh` | Backfill `article_market_reactions` for research narrative |

## Tests

```bash
python -m pytest app/tests/ -q
```

## Known limitations

- Insider Form 4 parser is limited (many tickers show $0)
- Intraday endpoint returns empty
- Some SEC metrics missing per issuer (e.g. MCD cost of revenue)
- Admin routes have no authentication
- Embedding-based dedup not implemented (keyword sentiment only)

## License

[Apache License 2.0](LICENSE)
