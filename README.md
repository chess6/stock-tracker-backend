# Stock Tracker Backend

Flask API and SQLite cache for the Stock Tracker app. Ingests **SEC EDGAR** fundamentals, **Stooq/yfinance** prices, **RSS** news, and optional **Nasdaq Data Link** fallback. Pairs with the [frontend repo](https://github.com/chess6/stock-tracker-frontend).

## Architecture

Local-data-first: admin/bootstrap endpoints fill SQLite; the UI reads from cache on every request.

| Component | Path |
|-----------|------|
| API entry | `api.py` |
| Routes & services | `app/` |
| SQLite DB | `data/stock_tracker.sqlite3` |
| Background worker | `worker.py` |
| Shell helpers | `start.sh`, `stop.sh`, `worker.sh`, `worker_stop.sh`, `bootstrap.sh`, `refresh_data.sh` |
| CLI tester | `api_tester.py` |

## Prerequisites

- Python 3.10+
- `SEC_USER_AGENT` in `.env` (required — use your email for SEC requests)
- `NASDAQ_API_KEY` (optional — live fallback when cache is empty)

## Setup

```bash
pip install -r requirements.txt
```

Create `.env`:

```env
SEC_USER_AGENT=you@example.com
# Optional:
# NASDAQ_API_KEY=your_key
# STOCK_TRACKER_DB_PATH=/path/to/stock_tracker.sqlite3
# STOCK_TRACKER_DEFAULT_TICKERS=JPM,MCD,AAPL
# STOCK_TRACKER_REQUEST_TIMEOUT=20
```

## Run & stop

```bash
sh start.sh      # → http://localhost:5000
sh stop.sh       # uses .backend.pid
```

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
| POST | `/api/admin/refresh-fundamentals?tickers=` | SEC XBRL CompanyFacts → `fundamentals` |
| POST | `/api/admin/refresh-prices?tickers=` | Stooq/yfinance OHLCV → `prices` |
| POST | `/api/admin/refresh-insiders?tickers=` | SEC Form 4 → `insider_transactions` |
| POST | `/api/admin/ingest-default-feeds` | Poll 18 RSS feeds; `forceRefresh=true` by default |
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

**Stale news:** `curl -X POST http://localhost:5000/api/admin/ingest-default-feeds` then `dedup-articles`

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

MIT
