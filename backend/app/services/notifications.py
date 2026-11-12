"""Notifications (Module 9): who hears about what, in the app and by email.

Delivering a notification to *someone else* (the assignee hears about a customer
reply) is a system operation: it runs under the RLS bypass, in a small block,
because the notifications table only lets people read and write their own rows.
Rows are written in the caller's transaction; real-time pings and emails go out
after the caller commits (``flush``), and their failure never undoes anything.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.database import rls_bypass, set_rls_context
from app.core.email import EmailMessage, EmailSender, get_email_sender
from app.core.exceptions import AppError
from app.core.security import utcnow
from app.models.notification import (
    CUSTOMER_KINDS,
    DEFAULT_DELIVERY,
    STAFF_KINDS,
    Notification,
    NotificationDelivery,
    NotificationKind,
    NotificationPreference,
)
from app.models.organization import OrganizationMember
from app.models.user import User
from app.services.authorization import STAFF_ROLES
from app.websocket.broker import EventBroker
from app.websocket.broker import TicketEvent as RealtimeEvent
from app.workers.queue import NotificationMailer, QueueUnavailableError

logger = logging.getLogger(__name__)

MAX_PAGE = 100


@dataclass(frozen=True)
class NewNotification:
    user_id: uuid.UUID
    kind: NotificationKind
    title: str
    dedupe_key: str
    body: str = ""
    ticket_id: uuid.UUID | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Created:
    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    ticket_id: uuid.UUID | None
    email: bool


def kinds_for(member: OrganizationMember) -> tuple[NotificationKind, ...]:
    return STAFF_KINDS if member.role in STAFF_ROLES else CUSTOMER_KINDS


class NotificationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        events: EventBroker | None = None,
        mailer: NotificationMailer | None = None,
    ) -> None:
        self.session = session
        self.events = events
        self.mailer = mailer
        self._created: list[_Created] = []

    # --- Delivering -------------------------------------------------------------

    async def add(
        self,
        organization_id: uuid.UUID,
        items: Sequence[NewNotification],
        *,
        actor_id: uuid.UUID | None = None,
    ) -> int:
        """Write notifications in the caller's transaction (no commit). People are
        never notified of their own actions, nor twice for one ``dedupe_key``, nor
        about a kind they turned off. Returns how many were written."""
        items = [i for i in items if i.user_id != actor_id]
        if not items:
            return 0
        written = 0
        async with rls_bypass(self.session):
            deliveries = await self._deliveries(organization_id, items)
            for item in items:
                delivery = deliveries[(item.user_id, item.kind)]
                if delivery == NotificationDelivery.OFF:
                    continue
                email = delivery == NotificationDelivery.EMAIL
                stmt = (
                    insert(Notification)
                    .values(
                        id=uuid.uuid4(),
                        organization_id=organization_id,
                        user_id=item.user_id,
                        kind=item.kind,
                        ticket_id=item.ticket_id,
                        title=item.title[:200],
                        body=item.body,
                        data=item.data,
                        dedupe_key=item.dedupe_key[:200],
                        email=email,
                    )
                    .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
                    .returning(Notification.id)
                )
                notification_id = (await self.session.execute(stmt)).scalar_one_or_none()
                if notification_id is not None:
                    written += 1
                    self._created.append(
                        _Created(
                            notification_id, organization_id, item.user_id, item.ticket_id, email
                        )
                    )
        return written

    async def flush(self) -> None:
        """After the caller's commit: ping the recipients' open apps and queue the
        emails. Best effort; the notifications themselves are already saved."""
        created, self._created = self._created, []
        if self.events is not None:
            for c in created:
                await self.events.publish(
                    RealtimeEvent.build(
                        "notification.created",
                        organization_id=c.organization_id,
                        ticket_id=c.ticket_id or uuid.UUID(int=0),
                        customer_user_id=None,
                        recipient_user_id=c.user_id,
                    )
                )
        emails = [c.id for c in created if c.email]
        if emails and self.mailer is not None:
            try:
                await self.mailer.enqueue(emails)
            except QueueUnavailableError as exc:
                logger.warning("Couldn't queue %d notification emails: %s", len(emails), exc)

    async def _deliveries(
        self, organization_id: uuid.UUID, items: Sequence[NewNotification]
    ) -> dict[tuple[uuid.UUID, NotificationKind], NotificationDelivery]:
        users = {i.user_id for i in items}
        kinds = {i.kind for i in items}
        rows = await self.session.execute(
            select(
                NotificationPreference.user_id,
                NotificationPreference.kind,
                NotificationPreference.delivery,
            ).where(
                NotificationPreference.organization_id == organization_id,
                NotificationPreference.user_id.in_(users),
                NotificationPreference.kind.in_(kinds),
            )
        )
        saved = {(user_id, kind): delivery for user_id, kind, delivery in rows}
        return {
            (i.user_id, i.kind): saved.get((i.user_id, i.kind), DEFAULT_DELIVERY[i.kind])
            for i in items
        }

    # --- Reading (the signed-in user's own notifications) ------------------------

    async def list_notifications(
        self,
        member: OrganizationMember,
        user: User,
        *,
        unread_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[Notification], int, int]:
        mine = (
            Notification.organization_id == member.organization_id,
            Notification.user_id == user.id,
        )
        query = select(Notification).where(*mine)
        if unread_only:
            query = query.where(Notification.read_at.is_(None))
        items = (
            await self.session.scalars(
                query.order_by(Notification.seq.desc()).limit(min(limit, MAX_PAGE)).offset(offset)
            )
        ).all()
        total = await self.session.scalar(
            select(func.count()).select_from(query.order_by(None).subquery())
        )
        return list(items), int(total or 0), await self.unread_count(member, user)

    async def unread_count(self, member: OrganizationMember, user: User) -> int:
        count = await self.session.scalar(
            select(func.count()).where(
                Notification.organization_id == member.organization_id,
                Notification.user_id == user.id,
                Notification.read_at.is_(None),
            )
        )
        return int(count or 0)

    async def mark_read(
        self,
        member: OrganizationMember,
        user: User,
        ids: Sequence[uuid.UUID] | None,
    ) -> int:
        """Mark the given notifications (or, with ``ids=None``, all) as read."""
        stmt = (
            update(Notification)
            .where(
                Notification.organization_id == member.organization_id,
                Notification.user_id == user.id,
                Notification.read_at.is_(None),
            )
            .values(read_at=utcnow())
        )
        if ids is not None:
            stmt = stmt.where(Notification.id.in_(ids))
        await self.session.execute(stmt)
        await self.session.commit()
        return await self.unread_count(member, user)

    # --- Preferences -------------------------------------------------------------

    async def preferences(
        self, member: OrganizationMember, user: User
    ) -> dict[NotificationKind, NotificationDelivery]:
        kinds = kinds_for(member)
        rows = await self.session.execute(
            select(NotificationPreference.kind, NotificationPreference.delivery).where(
                NotificationPreference.organization_id == member.organization_id,
                NotificationPreference.user_id == user.id,
            )
        )
        saved = {kind: delivery for kind, delivery in rows.all()}
        return {kind: saved.get(kind, DEFAULT_DELIVERY[kind]) for kind in kinds}

    async def set_preference(
        self,
        member: OrganizationMember,
        user: User,
        kind: NotificationKind,
        delivery: NotificationDelivery,
    ) -> dict[NotificationKind, NotificationDelivery]:
        if kind not in kinds_for(member):
            raise AppError("That notification doesn't apply to your role", code="invalid_kind")
        stmt = (
            insert(NotificationPreference)
            .values(
                id=uuid.uuid4(),
                organization_id=member.organization_id,
                user_id=user.id,
                kind=kind,
                delivery=delivery,
            )
            .on_conflict_do_update(
                index_elements=["organization_id", "user_id", "kind"],
                set_={"delivery": delivery, "updated_at": func.now()},
            )
        )
        await self.session.execute(stmt)
        await self.session.commit()
        return await self.preferences(member, user)


# --- Email delivery (runs in the worker) -------------------------------------------


def build_notification_email(notification: Notification, to: str) -> EmailMessage:
    link = (
        f"{settings.frontend_url}/tickets/{notification.ticket_id}"
        if notification.ticket_id
        else settings.frontend_url
    )
    body = f"{notification.body}\n\n" if notification.body else ""
    return EmailMessage(
        to=to,
        subject=notification.title,
        text=(
            f"{notification.title}\n\n{body}Open it in SupportIQ: {link}\n\n"
            "You can change which notifications you receive by email on your account page."
        ),
    )


async def send_pending_emails(
    session: AsyncSession, sender: EmailSender, ids: Sequence[uuid.UUID]
) -> int:
    """Email the given notifications that asked for email and weren't sent yet.
    Raises on a delivery failure, so the job is retried; ones already sent are
    marked and skipped on the retry."""
    sent = 0
    await set_rls_context(session, bypass=True)
    rows = await session.execute(
        select(Notification, User.email)
        .join(User, User.id == Notification.user_id)
        .where(
            Notification.id.in_(ids),
            Notification.email.is_(True),
            Notification.emailed_at.is_(None),
        )
        .order_by(Notification.seq)
    )
    for notification, address in rows.all():
        await sender.send(build_notification_email(notification, address))
        notification.emailed_at = utcnow()
        await session.commit()  # the bypass is re-applied to the next transaction
        sent += 1
    return sent


async def run_email_job(ids: Sequence[uuid.UUID]) -> int:
    """Entry point for the worker: its own event loop and database engine."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await send_pending_emails(session, get_email_sender(), ids)
    finally:
        await engine.dispose()
