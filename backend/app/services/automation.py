"""Scheduled ticket automation (run by Celery beat; also ``app.cli auto-close``).

Auto-close: a resolved ticket that nobody has replied to for the organization's
``auto_close_after_days`` (default 7, 0 = never) is closed. A customer reply to a
resolved ticket moves it back to in progress, so only tickets still resolved are
touched. Runs across organizations under the RLS bypass, in bounded batches with
``FOR UPDATE SKIP LOCKED``, so two schedulers never close the same ticket twice.
"""

import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import joinedload
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.database import set_rls_context
from app.core.security import utcnow
from app.models import Organization, Ticket, TicketStatus
from app.models.organization import DEFAULT_AUTO_CLOSE_DAYS
from app.services.tickets import TicketService
from app.websocket.broker import EventBroker

logger = logging.getLogger(__name__)

BATCH = 200


async def auto_close_resolved(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    events: EventBroker | None = None,
    batch: int = BATCH,
) -> int:
    """Close every resolved ticket past its organization's auto-close window.
    Returns how many were closed."""
    now = now or utcnow()
    await set_rls_context(session, bypass=True)
    days = func.coalesce(
        Organization.settings["auto_close_after_days"].as_integer(), DEFAULT_AUTO_CLOSE_DAYS
    )
    closed = 0
    while True:
        rows = (
            await session.execute(
                select(Ticket, days)
                .join(Organization, Organization.id == Ticket.organization_id)
                .where(
                    Ticket.status == TicketStatus.RESOLVED,
                    days > 0,
                    Ticket.resolved_at <= now - func.make_interval(0, 0, 0, days),
                )
                .options(
                    joinedload(Ticket.customer),
                    joinedload(Ticket.assignee),
                    joinedload(Ticket.category),
                )
                .order_by(Ticket.resolved_at)
                .limit(batch)
                .with_for_update(of=Ticket, skip_locked=True)
            )
        ).all()
        if not rows:
            break
        service = TicketService(session, events)
        for ticket, after_days in rows:
            service._set_status(
                ticket,
                None,
                TicketStatus.CLOSED,
                {"automatic": True, "after_days": int(after_days)},
            )
        await session.commit()  # the bypass is re-applied to the next transaction
        for ticket, _ in rows:
            await service._notify("ticket.updated", ticket)
        closed += len(rows)
        if len(rows) < batch:
            break
    if closed:
        logger.info("Auto-closed %d resolved tickets", closed)
    return closed


async def run_auto_close_job() -> int:
    """Entry point for the worker: its own event loop, database engine and broker."""
    from redis.asyncio import Redis

    from app.websocket.broker import RedisEventBroker

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    redis = Redis.from_url(settings.redis_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await auto_close_resolved(session, events=RedisEventBroker(redis))
    finally:
        await redis.aclose()
        await engine.dispose()
