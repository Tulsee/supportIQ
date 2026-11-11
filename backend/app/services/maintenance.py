"""Periodic housekeeping: purge expired refresh tokens and stale invitations.

Runs in-process on an interval (``MAINTENANCE_INTERVAL_MINUTES``) and on demand via
``uv run python -m app.cli purge-expired``. A Postgres advisory lock makes
concurrent runs from several API workers skip instead of doing the same work.
(Once Celery is introduced, this becomes a Celery beat task.)
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import rls_bypass
from app.core.security import utcnow
from app.repositories.invitations import InvitationRepository
from app.repositories.refresh_tokens import RefreshTokenRepository

logger = logging.getLogger(__name__)

# Keep expired/revoked invitations around briefly so admins can still see them.
INVITATION_RETENTION = timedelta(days=30)
# Arbitrary constant identifying this job's advisory lock.
_MAINTENANCE_LOCK_ID = 7_402_113_001


@dataclass(frozen=True)
class PurgeResult:
    refresh_tokens: int
    invitations: int


async def purge_expired(session: AsyncSession) -> PurgeResult:
    now = utcnow()
    tokens = await RefreshTokenRepository(session).delete_expired(now)
    async with rls_bypass(session):  # housekeeping spans all tenants
        invitations = await InvitationRepository(session).delete_stale(now - INVITATION_RETENTION)
    await session.commit()
    return PurgeResult(refresh_tokens=tokens, invitations=invitations)


async def run_maintenance_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> PurgeResult | None:
    """Purge unless another process is doing it right now (then return None)."""
    async with session_factory() as session:
        # Transaction-scoped lock: released by purge_expired's commit.
        acquired = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:lock_id)"), {"lock_id": _MAINTENANCE_LOCK_ID}
        )
        if not acquired:
            return None
        return await purge_expired(session)


async def maintenance_loop(
    session_factory: async_sessionmaker[AsyncSession], interval_minutes: int
) -> None:
    while True:
        try:
            result = await run_maintenance_once(session_factory)
            if result and (result.refresh_tokens or result.invitations):
                logger.info(
                    "Maintenance purged %d refresh tokens and %d invitations",
                    result.refresh_tokens,
                    result.invitations,
                )
        except Exception:
            logger.exception("Maintenance run failed")
        await asyncio.sleep(interval_minutes * 60)
