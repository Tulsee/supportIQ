"""Shared async Redis client (one connection pool per process)."""

import asyncio
import logging
from functools import cache

from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)


@cache
def get_redis() -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


async def close_redis() -> None:
    if get_redis.cache_info().currsize:
        await get_redis().aclose()
        get_redis.cache_clear()


async def ping_redis() -> bool:
    try:
        async with asyncio.timeout(settings.health_check_timeout_seconds):
            return bool(await get_redis().ping())
    except Exception as exc:  # any failure means "not ready"; never crash the probe
        logger.warning("Redis ping failed: %s: %s", type(exc).__name__, exc)
        return False
