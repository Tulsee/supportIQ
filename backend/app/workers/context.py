"""What a background job runs with: its own database engine and session, Redis
(presence and real-time events) and the notification mailer. Workers run each job in
a fresh event loop, so nothing is shared with the API process."""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.websocket.broker import EventBroker
from app.websocket.presence import PresenceTracker
from app.workers.queue import NotificationMailer

T = TypeVar("T")
WorkerJob = Callable[[AsyncSession, PresenceTracker, EventBroker, NotificationMailer], Awaitable[T]]


async def with_worker_context(job: "WorkerJob[T]") -> T:
    from redis.asyncio import Redis

    from app.websocket.broker import RedisEventBroker
    from app.websocket.presence import RedisPresence
    from app.workers.queue import CeleryNotificationMailer

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    redis = Redis.from_url(settings.redis_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await job(
                session, RedisPresence(redis), RedisEventBroker(redis), CeleryNotificationMailer()
            )
    finally:
        await redis.aclose()
        await engine.dispose()
