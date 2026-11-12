"""The SLA monitor (Module 10, phase 4), run by the scheduler every minute.

It finds running clocks whose warning time or deadline has passed and records the
transition once: ``at_risk_at`` (the warning time) or ``breached_at`` (the deadline),
a staff-only ticket event, and notifications. Due soon goes to the assignee (or the
organization's admins if nobody is assigned); a breach goes to the assignee and the
admins. Team leads replace admins once teams exist (phase 5).

Safe to run twice at once: clocks are claimed with ``FOR UPDATE SKIP LOCKED``, and
notifications carry a dedupe key per clock and transition. Notifications and
real-time pings go out after the commit; their failure never undoes a transition.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.database import set_rls_context
from app.core.security import utcnow
from app.models import (
    NotificationKind,
    OrganizationMember,
    OrgRole,
    SlaClockKind,
    SlaClockState,
    Ticket,
    TicketEvent,
    TicketEventType,
    TicketSlaClock,
)
from app.services.notifications import NewNotification, NotificationService
from app.websocket.broker import EventBroker
from app.websocket.broker import TicketEvent as RealtimeEvent
from app.workers.queue import NotificationMailer

logger = logging.getLogger(__name__)

BATCH = 500
LABELS = {SlaClockKind.FIRST_RESPONSE: "First response", SlaClockKind.RESOLUTION: "Resolution"}


@dataclass(frozen=True)
class MonitorResult:
    at_risk: int = 0
    breached: int = 0


async def _admin_ids(session: AsyncSession, organization_id: uuid.UUID) -> list[uuid.UUID]:
    rows = await session.scalars(
        select(OrganizationMember.user_id).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.role == OrgRole.ADMIN,
        )
    )
    return list(rows.all())


def _notices(
    clock: TicketSlaClock, ticket: Ticket, breached: bool, recipients: Sequence[uuid.UUID]
) -> list[NewNotification]:
    label = LABELS[clock.kind]
    zone = ZoneInfo(str(clock.calendar_snapshot["timezone"]))
    due = clock.due_at.astimezone(zone)
    if breached:
        kind, title = (
            NotificationKind.SLA_BREACHED,
            f"SLA breached: {label.lower()} on #{ticket.number}",
        )
    else:
        kind, title = NotificationKind.SLA_AT_RISK, f"{label} due soon on #{ticket.number}"
    body = f"{ticket.subject} · due {due:%a %d %b, %H:%M} ({zone.key})"
    transition = "breached" if breached else "at_risk"
    return [
        NewNotification(
            user_id=user_id,
            kind=kind,
            title=title,
            body=body,
            ticket_id=ticket.id,
            data={"clock_id": str(clock.id), "kind": clock.kind.value, "cycle": clock.cycle},
            dedupe_key=f"sla:{clock.id}:{transition}",
        )
        for user_id in dict.fromkeys(recipients)
    ]


async def evaluate_sla_clocks(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    events: EventBroker | None = None,
    mailer: NotificationMailer | None = None,
    batch: int = BATCH,
) -> MonitorResult:
    now = now or utcnow()
    await set_rls_context(session, bypass=True)
    at_risk = breached = 0
    while True:
        rows = (
            await session.execute(
                select(TicketSlaClock, Ticket)
                .join(Ticket, Ticket.id == TicketSlaClock.ticket_id)
                .where(
                    TicketSlaClock.state == SlaClockState.RUNNING,
                    or_(
                        and_(TicketSlaClock.breached_at.is_(None), TicketSlaClock.due_at <= now),
                        and_(TicketSlaClock.at_risk_at.is_(None), TicketSlaClock.warn_at <= now),
                    ),
                )
                .order_by(TicketSlaClock.due_at)
                .limit(batch)
                .with_for_update(of=TicketSlaClock, skip_locked=True)
            )
        ).all()
        if not rows:
            break
        notifications = NotificationService(session, events=events, mailer=mailer)
        admins: dict[uuid.UUID, list[uuid.UUID]] = {}
        for clock, ticket in rows:
            is_breach = clock.due_at <= now and clock.breached_at is None
            if is_breach:
                # Recorded at the deadline, not when the monitor noticed.
                clock.breached_at = clock.due_at
                clock.at_risk_at = clock.at_risk_at or clock.warn_at
                breached += 1
            else:
                clock.at_risk_at = clock.warn_at
                at_risk += 1
            session.add(
                TicketEvent(
                    organization_id=ticket.organization_id,
                    ticket_id=ticket.id,
                    actor_id=None,
                    type=TicketEventType.SLA_BREACHED if is_breach else TicketEventType.SLA_AT_RISK,
                    data={
                        "kind": clock.kind.value,
                        "cycle": clock.cycle,
                        "due_at": clock.due_at.isoformat(),
                        "automatic": True,
                    },
                )
            )
            if ticket.organization_id not in admins:
                admins[ticket.organization_id] = await _admin_ids(session, ticket.organization_id)
            org_admins = admins[ticket.organization_id]
            assignee = [ticket.assignee_id] if ticket.assignee_id else []
            recipients = assignee + org_admins if is_breach else (assignee or org_admins)
            await notifications.add(
                ticket.organization_id, _notices(clock, ticket, is_breach, recipients)
            )
        await session.commit()  # the bypass is re-applied to the next transaction
        if events is not None:
            for ticket_id, organization_id in {(t.id, t.organization_id) for _, t in rows}:
                await events.publish(
                    RealtimeEvent.build(
                        "ticket.updated",
                        organization_id=organization_id,
                        ticket_id=ticket_id,
                        customer_user_id=None,
                        staff_only=True,
                    )
                )
        await notifications.flush()
        if len(rows) < batch:
            break
    if at_risk or breached:
        logger.info("SLA monitor: %d due soon, %d breached", at_risk, breached)
    return MonitorResult(at_risk=at_risk, breached=breached)


async def run_sla_job() -> MonitorResult:
    """Entry point for the worker: its own event loop, database engine and broker."""
    from redis.asyncio import Redis

    from app.websocket.broker import RedisEventBroker
    from app.workers.queue import CeleryNotificationMailer

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    redis = Redis.from_url(settings.redis_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await evaluate_sla_clocks(
                session, events=RedisEventBroker(redis), mailer=CeleryNotificationMailer()
            )
    finally:
        await redis.aclose()
        await engine.dispose()
