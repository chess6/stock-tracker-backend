from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass
class OrchestratorSettings:
    db_path: str = ""
    redis_url: str = ""
    ai_default_provider: str = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2"
    poll_interval_seconds: float = 2.0
    max_concurrent_agents: int = 3
    rate_limit_requests_per_minute: int = 30
    retry_max_attempts: int = 5
    retry_base_delay_seconds: float = 1.0
    min_confidence_auto_execute: float = 0.85
    orchestrator_api_host: str = "127.0.0.1"
    orchestrator_api_port: int = 5001

    @classmethod
    def from_env(cls) -> OrchestratorSettings:
        return cls(
            db_path=os.getenv("STOCK_TRACKER_DB_PATH", os.getenv("ORCHESTRATOR_DB_PATH", "")),
            redis_url=os.getenv("REDIS_URL", ""),
            ai_default_provider=os.getenv("AI_DEFAULT_PROVIDER", "openai"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2"),
            poll_interval_seconds=float(os.getenv("ORCHESTRATOR_POLL_INTERVAL", "2.0")),
            rate_limit_requests_per_minute=int(os.getenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "30")),
            retry_max_attempts=int(os.getenv("RETRY_MAX_ATTEMPTS", "5")),
            min_confidence_auto_execute=float(os.getenv("MIN_CONFIDENCE_AUTO_EXECUTE", "0.85")),
            orchestrator_api_host=os.getenv("ORCHESTRATOR_API_HOST", "127.0.0.1"),
            orchestrator_api_port=int(os.getenv("ORCHESTRATOR_API_PORT", "5001")),
        )

    def resolved_db_path(self) -> Path:
        if self.db_path:
            return Path(self.db_path)
        return Path(__file__).resolve().parent.parent / "data" / "stock_tracker.sqlite3"


@lru_cache
def get_settings() -> OrchestratorSettings:
    return OrchestratorSettings.from_env()
