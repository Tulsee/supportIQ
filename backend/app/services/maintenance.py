"""Periodic housekeeping: purge expired refresh tokens and stale invitations, and
mark document ingestions that stalled as failed (so they can be retried).

Runs in-process on an interval (``MAINTENANCE_INTERVAL_MINUTES``) and on demand via
``uv run python -m app.cli purge-expired``. A Postgres advisory lock makes
concurrent runs from several API workers skip instead of doing the same work.
(Once Celery is introduced, this becomes a Celery beat task.)
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.database import rls_bypass
from app.core.security import utcnow
from app.models import ACTIVE_INGESTION, Document, DocumentStatus
from app.repositories.invitations import InvitationRepository
from app.repositories.refresh_tokens import RefreshTokenRepository

logger = logging.getLogger(__name__)

# Keep expired/revoked invitations around briefly so admins can still see them.
INVITATION_RETENTION = timedelta(days=30)
# Arbitrary constant identifying this job's advisory lock.
_MAINTENANCE_LOCK_ID = 7_402_113_001


STALLED_INGESTION_ERROR = (
    "Indexing didn't finish (the background worker may have stopped). Try again."
)


@dataclass(frozen=True)
class PurgeResult:
    refresh_tokens: int
    invitations: int
    stalled_ingestions: int = 0


async def fail_stalled_ingestions(session: AsyncSession, older_than: datetime) -> int:
    """Documents stuck queued/processing (lost job, dead worker) become retryable.
    A job still running for one of them notices and discards its own work."""
    result = await session.execute(
        update(Document)
        .where(Document.status.in_(ACTIVE_INGESTION), Document.updated_at < older_than)
        .values(status=DocumentStatus.FAILED, error=STALLED_INGESTION_ERROR)
    )
    return result.rowcount


async def purge_expired(session: AsyncSession) -> PurgeResult:
    now = utcnow()
    tokens = await RefreshTokenRepository(session).delete_expired(now)
    async with rls_bypass(session):  # housekeeping spans all tenants
        invitations = await InvitationRepository(session).delete_stale(now - INVITATION_RETENTION)
        stalled = await fail_stalled_ingestions(
            session, now - timedelta(minutes=settings.ingestion_stale_minutes)
        )
    await session.commit()
    return PurgeResult(refresh_tokens=tokens, invitations=invitations, stalled_ingestions=stalled)


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
            if result and (
                result.refresh_tokens or result.invitations or result.stalled_ingestions
            ):
                logger.info(
                    "Maintenance purged %d refresh tokens and %d invitations; "
                    "%d stalled ingestions marked failed",
                    result.refresh_tokens,
                    result.invitations,
                    result.stalled_ingestions,
                )
        except Exception:
            logger.exception("Maintenance run failed")
        await asyncio.sleep(interval_minutes * 60)
