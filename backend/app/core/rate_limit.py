"""Fixed-window rate limiting.

``RedisRateLimiter`` is shared across all API processes. ``InMemoryRateLimiter``
is per-process, for tests and tooling only.

If Redis is unavailable the limiter *fails open* (allows the request and logs a
warning): an outage of the limiter should not lock every user out. The readiness
probe reports Redis, so the outage is still visible.
"""

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int  # seconds until the current window resets (0 if unknown)


class RateLimiter(Protocol):
    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        """Count one attempt. Allowed while the count stays within ``limit``."""
        ...

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        """Would one more attempt be allowed? Does not count anything."""
        ...

    async def reset(self, key: str) -> None: ...


class RedisRateLimiter:
    def __init__(self, client: Redis, prefix: str = "ratelimit:") -> None:
        self.client = client
        self.prefix = prefix

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        name = self.prefix + key
        try:
            async with self.client.pipeline(transaction=True) as pipe:
                pipe.incr(name)
                pipe.expire(name, window_seconds, nx=True)  # start the window on first hit
                pipe.ttl(name)
                count, _, ttl = await pipe.execute()
        except RedisError as exc:
            logger.warning("Rate limiter unavailable, allowing request: %s", exc)
            return RateLimitResult(allowed=True, retry_after=0)
        return RateLimitResult(allowed=count <= limit, retry_after=max(int(ttl), 1))

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        name = self.prefix + key
        try:
            async with self.client.pipeline(transaction=True) as pipe:
                pipe.get(name)
                pipe.ttl(name)
                raw_count, ttl = await pipe.execute()
        except RedisError as exc:
            logger.warning("Rate limiter unavailable, allowing request: %s", exc)
            return RateLimitResult(allowed=True, retry_after=0)
        allowed = int(raw_count or 0) < limit
        return RateLimitResult(allowed=allowed, retry_after=0 if allowed else max(int(ttl), 1))

    async def reset(self, key: str) -> None:
        try:
            await self.client.delete(self.prefix + key)
        except RedisError as exc:
            logger.warning("Rate limiter unavailable, could not reset %s: %s", key, exc)


class InMemoryRateLimiter:
    """Single-process limiter for tests and tooling. Not shared across workers."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._windows: dict[str, tuple[int, float]] = {}  # key -> (count, resets_at)

    def _current(self, key: str) -> tuple[int, float]:
        count, resets_at = self._windows.get(key, (0, 0.0))
        return (0, 0.0) if self._clock() >= resets_at else (count, resets_at)

    def _retry_after(self, resets_at: float) -> int:
        return max(math.ceil(resets_at - self._clock()), 1)

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        count, resets_at = self._current(key)
        if count == 0:
            resets_at = self._clock() + window_seconds
        count += 1
        self._windows[key] = (count, resets_at)
        return RateLimitResult(allowed=count <= limit, retry_after=self._retry_after(resets_at))

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        count, resets_at = self._current(key)
        allowed = count < limit
        return RateLimitResult(
            allowed=allowed, retry_after=0 if allowed else self._retry_after(resets_at)
        )

    async def reset(self, key: str) -> None:
        self._windows.pop(key, None)
