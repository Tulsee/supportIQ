"""Ingestion workers announce themselves in Redis.

Without a running worker, uploaded documents wait in the queue indefinitely; the
app checks for live workers so it can say so instead of showing a silent
"Queued". Each worker refreshes a key with a short expiry; a worker that stops
(or crashes) disappears within ``TTL_SECONDS``.
"""

import contextlib
import logging
import threading
from typing import Protocol

from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

KEY_PREFIX = "supportiq:workers:ingestion:"
TTL_SECONDS = 60
INTERVAL_SECONDS = 20


class Heartbeat:
    """Keeps ``<prefix><node>`` alive while the worker runs (a daemon thread)."""

    def __init__(self, redis: Redis, node: str, *, prefix: str = KEY_PREFIX) -> None:
        self.redis = redis
        self.key = f"{prefix}{node}"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="heartbeat", daemon=True)

    def start(self) -> None:
        self._beat()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(RedisError):  # otherwise it expires on its own
            self.redis.delete(self.key)

    def _beat(self) -> None:
        try:
            self.redis.set(self.key, "1", ex=TTL_SECONDS)
        except RedisError as exc:
            logger.warning("Worker heartbeat failed: %s", exc)

    def _run(self) -> None:
        while not self._stop.wait(INTERVAL_SECONDS):
            self._beat()


class WorkerRegistry(Protocol):
    async def live_workers(self) -> int | None:
        """Number of live ingestion workers, or None if it can't be determined."""
        ...


class RedisWorkerRegistry:
    def __init__(self, redis: AsyncRedis, *, prefix: str = KEY_PREFIX) -> None:
        self.redis = redis
        self.prefix = prefix

    async def live_workers(self) -> int | None:
        try:
            return len([key async for key in self.redis.scan_iter(match=f"{self.prefix}*")])
        except (RedisError, OSError) as exc:
            logger.warning("Couldn't check for ingestion workers: %s", exc)
            return None
