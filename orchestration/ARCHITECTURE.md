# AI Orchestration Layer — Architecture

Event-driven autonomous agents that **propose** actions; deterministic validators **execute** only approved, safe operations.

## Components

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Existing App (Flask API + ingestion worker)                            │
│  news ingest · fundamentals · prices · insiders                         │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ emit events (news_ingested, fetch_failed)
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Event Bus (Redis list OR SQLite orchestrator_events table)             │
└───────────────────────────────┬─────────────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Orchestrator Loop (orchestrator/loop.py)                               │
│  prioritize → dispatch → persist → retry → approval gate                │
└───────┬──────────┬──────────┬──────────┬──────────┬───────────────────┘
        ▼          ▼          ▼          ▼          ▼
   NewsAnalysis SignalRank Watchlist  Portfolio  Repair
        │          │          │          │          │
        └──────────┴──────────┴────┬─────┴──────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │  AI Provider (multi-model)   │
                    │  OpenAI · Claude · Ollama    │
                    └──────────────────────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │  Memory Store (SQLite)       │
                    │  analyses · decisions · emb│
                    └──────────────────────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │  Action Validators           │
                    │  (no direct side effects)    │
                    └──────────────────────────────┘
```

## Event types

| Event | Priority | Agent |
|-------|----------|-------|
| `news_ingested` | 50 | NewsAnalysisAgent |
| `analysis_completed` | 40 | SignalRankingAgent |
| `high_priority_signal` | 10 | PortfolioMonitoringAgent |
| `watchlist_candidate` | 30 | WatchlistExpansionAgent |
| `fetch_failed` | 20 | RepairAgent |
| `repair_required` | 15 | RepairAgent |
| `portfolio_check` | 35 | PortfolioMonitoringAgent |

Lower priority number = higher urgency.

## Human approval

Actions with `requires_approval: true` in agent output are stored in `orchestrator_approvals` with status `pending`. FastAPI endpoints allow approve/reject. Validators run again on approval before execution.

## Data stores

All orchestration tables live in the **same SQLite file** as the stock tracker (`STOCK_TRACKER_DB_PATH`), managed by SQLAlchemy. Redis is optional for the hot event queue.

## Entry points

| Command | Purpose |
|---------|---------|
| `python -m orchestration.worker` | Orchestrator loop |
| `python -m orchestration.api` | FastAPI observability + approvals |
| Flask hooks | `orchestration.bridge.emit_*` from existing services |

## Safety rules

1. Agents return **JSON only** (Pydantic-validated).
2. Agents never call SEC/RSS/DB writes directly — they emit `proposed_actions`.
3. `ActionExecutor` validates tickers, job types, and confidence thresholds.
4. RepairAgent proposes strategies; retries go through existing `ingestion_jobs` queue.
