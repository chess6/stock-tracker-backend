from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


@dataclass
class RetryPolicy:
    max_attempts: int = 5
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 120.0
    jitter: float = 0.2


@dataclass
class RateLimiter:
    requests_per_minute: int = 30
    _timestamps: list[float] = field(default_factory=list)

    def acquire(self) -> None:
        now = time.monotonic()
        window = 60.0
        self._timestamps = [t for t in self._timestamps if now - t < window]
        if len(self._timestamps) >= self.requests_per_minute:
            sleep_for = window - (now - self._timestamps[0]) + 0.05
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._timestamps.append(time.monotonic())

    async def acquire_async(self) -> None:
        await asyncio.to_thread(self.acquire)


def compute_backoff(attempt: int, policy: RetryPolicy) -> float:
    delay = min(policy.base_delay_seconds * (2 ** max(attempt - 1, 0)), policy.max_delay_seconds)
    jitter = delay * policy.jitter * random.random()
    return delay + jitter


def with_retry(policy: RetryPolicy, rate_limiter: RateLimiter | None = None):
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args, **kwargs) -> T:
            last_exc: Exception | None = None
            for attempt in range(1, policy.max_attempts + 1):
                try:
                    if rate_limiter:
                        rate_limiter.acquire()
                    return fn(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt >= policy.max_attempts:
                        break
                    time.sleep(compute_backoff(attempt, policy))
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


async def run_with_retry_async(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    rate_limiter: RateLimiter | None = None,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            if rate_limiter:
                await rate_limiter.acquire_async()
            return await fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= policy.max_attempts:
                break
            await asyncio.sleep(compute_backoff(attempt, policy))
    raise last_exc  # type: ignore[misc]
