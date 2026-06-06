# Migration Plan — AI Orchestration Integration

## Phase 1: Parallel deployment (current)

- Existing Flask API (`api.py`) and ingestion worker (`worker.py`) unchanged
- New orchestrator runs alongside:
  - `sh orchestrator_start.sh` — event loop
  - `sh orchestrator_api_start.sh` — FastAPI dashboard on :5001
- Shares SQLite file via `STOCK_TRACKER_DB_PATH`
- Optional Redis via `REDIS_URL` for hot queue fan-in

## Phase 2: Event hooks (implemented)

| Source | Hook | Event |
|--------|------|-------|
| `NewsService` after article upsert | `emit_news_ingested()` | `news_ingested` |
| `WorkerRunner` on job failure | `emit_fetch_failed()` | `fetch_failed` |
| Admin bootstrap complete | `emit_portfolio_check()` | `portfolio_check` |

## Phase 3: Portfolio sync

1. Add admin endpoint or script to sync `orchestrator_portfolio_positions` from frontend localStorage export
2. Schedule `portfolio_check` events nightly
3. Surface watchlist proposals in frontend `/admin` or new `/orchestrator` page

## Phase 4: Observability UI

1. Add React page calling `http://localhost:5001/dashboard`
2. Show pending approvals with approve/reject buttons
3. Display agent decisions, confidence, repair logs

## Phase 5: Optional FastAPI consolidation

- Gradually move read-heavy routes to FastAPI
- Keep Flask for backward compatibility during transition
- Single process option: mount Flask via WSGI middleware (not required initially)

## Phase 6: Production hardening

- Add API key auth on orchestrator FastAPI
- Deploy Redis for multi-worker orchestrator
- Configure `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` or local Ollama
- Tune `MIN_CONFIDENCE_AUTO_EXECUTE` (default 0.85)

## Environment variables

```env
# Existing
STOCK_TRACKER_DB_PATH=...
SEC_USER_AGENT=...

# Orchestrator
REDIS_URL=redis://localhost:6379/0
AI_DEFAULT_PROVIDER=openai          # openai | anthropic | ollama
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-sonnet-4-20250514
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.2
ORCHESTRATOR_API_PORT=5001
MIN_CONFIDENCE_AUTO_EXECUTE=0.85
RATE_LIMIT_REQUESTS_PER_MINUTE=30
```

## Rollback

Stop orchestrator processes; existing app continues without AI layer. Orchestrator tables are additive — no changes to existing schema.
