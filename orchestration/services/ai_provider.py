from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from ..config import get_settings
from ..middleware.retry import RateLimiter, RetryPolicy, with_retry

logger = logging.getLogger(__name__)

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = JSON_BLOCK_RE.search(text)
        if match:
            return json.loads(match.group(1))
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise ValueError(f"Model did not return valid JSON: {text[:200]}...")


class AIProvider(ABC):
    @abstractmethod
    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        ...


class OpenAIProvider(AIProvider):
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.openai_api_key
        self.model = settings.openai_model
        self._limiter = RateLimiter(settings.rate_limit_requests_per_minute)
        self._policy = RetryPolicy(max_attempts=settings.retry_max_attempts)

    @with_retry(RetryPolicy(max_attempts=3))
    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")
        self._limiter.acquire()
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return extract_json(content)


class AnthropicProvider(AIProvider):
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.anthropic_api_key
        self.model = settings.anthropic_model
        self._limiter = RateLimiter(settings.rate_limit_requests_per_minute)

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured")
        self._limiter.acquire()
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 2048,
                "system": system + "\nRespond with JSON only.",
                "messages": [{"role": "user", "content": user}],
            },
            timeout=60.0,
        )
        response.raise_for_status()
        content = response.json()["content"][0]["text"]
        return extract_json(content)


class OllamaProvider(AIProvider):
    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model
        self._limiter = RateLimiter(settings.rate_limit_requests_per_minute)

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        self._limiter.acquire()
        response = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=120.0,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        return extract_json(content)


class DeterministicProvider(AIProvider):
    """Fallback when no API keys — rule-based JSON for tests and offline dev."""

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        logger.warning("Using DeterministicProvider — configure AI keys for real analysis")
        return {
            "agent": "deterministic",
            "confidence": 0.5,
            "summary": "Deterministic fallback analysis (no AI provider configured).",
            "findings": [],
            "proposed_actions": [],
            "follow_up_events": [],
            "memory_updates": [],
        }


def get_ai_provider(provider: str | None = None) -> AIProvider:
    name = (provider or get_settings().ai_default_provider).lower()
    if name == "openai" and get_settings().openai_api_key:
        return OpenAIProvider()
    if name == "anthropic" and get_settings().anthropic_api_key:
        return AnthropicProvider()
    if name == "ollama":
        return OllamaProvider()
    if name == "openai":
        return OpenAIProvider()
    if name == "anthropic":
        return AnthropicProvider()
    return DeterministicProvider()
