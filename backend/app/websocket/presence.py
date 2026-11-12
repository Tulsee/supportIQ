"""Who is online: staff with the app open.

The app keeps a WebSocket open to the organization while it's in use
(``app/api/v1/endpoints/realtime.py``). For staff, that connection marks them as seen
when it opens and every ``REFRESH_SECONDS`` after. Someone is online when seen in the
last ``TTL_SECONDS``, so a closed tab or a crashed browser goes offline on its own,
and two open tabs don't undo each other.

Storage: one Redis sorted set per organization (member = user id, score = last seen
in epoch seconds), trimmed on every write. What the agent *says* they're doing
(available, busy, away) lives in ``agent_profiles``; this only says whether they're
here at all.

Best effort: if Redis is down, nobody is reported online. Routing (phase 6) then
leaves tickets unassigned rather than handing them to someone who may have left.
"""

import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.security import utcnow

logger = logging.getLogger(__name__)

TTL_SECONDS = 90
REFRESH_SECONDS = 30
KEY_PREFIX = "supportiq:presence:"


class PresenceTracker(Protocol):
    async def touch(self, organization_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Mark the user as seen now in this organization."""
        ...

    async def online(self, organization_id: uuid.UUID) -> dict[uuid.UUID, datetime]:
        """Users seen within the TTL, with when they were last seen."""
        ...


def _key(organization_id: uuid.UUID) -> str:
    return f"{KEY_PREFIX}{organization_id}"


class RedisPresence:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def touch(self, organization_id: uuid.UUID, user_id: uuid.UUID) -> None:
        now = time.time()
        key = _key(organization_id)
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.zadd(key, {str(user_id): now})
                pipe.zremrangebyscore(key, "-inf", now - TTL_SECONDS)
                pipe.expire(key, TTL_SECONDS * 2)
                await pipe.execute()
        except (RedisError, OSError) as exc:
            logger.warning("Could not record presence: %s", exc)

    async def online(self, organization_id: uuid.UUID) -> dict[uuid.UUID, datetime]:
        now = time.time()
        try:
            rows = await self.redis.zrangebyscore(
                _key(organization_id), now - TTL_SECONDS, "+inf", withscores=True
            )
        except (RedisError, OSError) as exc:
            logger.warning("Could not read presence: %s", exc)
            return {}
        seen: dict[uuid.UUID, datetime] = {}
        for member, score in rows:
            raw = member.decode() if isinstance(member, bytes) else member
            try:
                seen[uuid.UUID(raw)] = datetime.fromtimestamp(score, UTC)
            except ValueError:
                continue
        return seen


class InMemoryPresence:
    """Single-process tracker for tests, on an injectable clock."""

    def __init__(self, clock: Callable[[], datetime] = utcnow) -> None:
        self.clock = clock
        self.seen: dict[uuid.UUID, dict[uuid.UUID, datetime]] = {}

    async def touch(self, organization_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.seen.setdefault(organization_id, {})[user_id] = self.clock()

    async def online(self, organization_id: uuid.UUID) -> dict[uuid.UUID, datetime]:
        cutoff = self.clock() - timedelta(seconds=TTL_SECONDS)
        return {user: at for user, at in self.seen.get(organization_id, {}).items() if at > cutoff}
