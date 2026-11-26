"""Document freshness (Module 13): owners, review dates and citation counts.

- **Owner and review date.** An admin gives a document an owner (a staff member) and a
  date to review it by, optionally with an interval: marking it reviewed then moves the
  date that many days ahead (without one, the date is cleared).
- **Reminders.** A periodic job finds documents whose review date has come (UTC) and
  notifies the owner, or the organization's admins when there is no owner or the owner
  is no longer active staff. Each due date reminds each person once.
- **Citations.** How often answers people were given cited the document recently, and
  when last. A document indexed long enough to judge, that no such answer cited in the
  window, is flagged "not cited": a candidate for review or removal, never removed
  automatically. Evaluation probes and gap rechecks don't count.
"""

import logging
import uuid
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.database import set_rls_context
from app.core.security import utcnow
from app.models import Document, NotificationKind, OrganizationMember, OrgRole, User
from app.repositories.documents import UsageWindow
from app.services.authorization import STAFF_ROLES
from app.services.notifications import NewNotification, NotificationService
from app.websocket.broker import EventBroker
from app.workers.queue import NotificationMailer

logger = logging.getLogger(__name__)

BATCH = 500


def usage_window(now: datetime) -> UsageWindow:
    return UsageWindow(
        since=now - timedelta(days=settings.document_citation_window_days),
        judged_before=now - timedelta(days=settings.document_new_days),
    )


def next_review(reviewed_on: date, interval_days: int | None) -> date | None:
    """The next review date after a review on ``reviewed_on``."""
    return reviewed_on + timedelta(days=interval_days) if interval_days else None


async def active_staff(
    session: AsyncSession, organization_id: uuid.UUID
) -> dict[uuid.UUID, OrgRole]:
    """Staff members of the organization whose accounts are active, with their role."""
    rows = await session.execute(
        select(OrganizationMember.user_id, OrganizationMember.role)
        .join(User, User.id == OrganizationMember.user_id)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.role.in_(STAFF_ROLES),
            User.is_active.is_(True),
        )
    )
    return {user_id: role for user_id, role in rows}


def reminder_recipients(document: Document, staff: dict[uuid.UUID, OrgRole]) -> list[uuid.UUID]:
    """The owner if they are still active staff here, otherwise every admin."""
    if document.owner_id is not None and document.owner_id in staff:
        return [document.owner_id]
    return sorted((user_id for user_id, role in staff.items() if role == OrgRole.ADMIN), key=str)


def review_reminders(
    documents: Sequence[Document], staff: dict[uuid.UUID, OrgRole]
) -> list[NewNotification]:
    items = []
    for document in documents:
        assert document.review_due_on is not None
        due = document.review_due_on.isoformat()
        for user_id in reminder_recipients(document, staff):
            items.append(
                NewNotification(
                    user_id=user_id,
                    kind=NotificationKind.DOCUMENT_REVIEW_DUE,
                    title=f"Review due: {document.title}",
                    body=(
                        f"{document.title} was due for review on {due}. Check that it is "
                        "still right, then mark it reviewed."
                    ),
                    data={"document_id": str(document.id), "review_due_on": due},
                    dedupe_key=f"document_review_due:{document.id}:{due}",
                )
            )
    return items


async def remind_due_reviews(
    session: AsyncSession,
    *,
    today: date | None = None,
    events: EventBroker | None = None,
    mailer: NotificationMailer | None = None,
) -> int:
    """Notify about every document whose review date has come. Safe to run again: a
    due date reminds each person once (the notification's dedupe key)."""
    today = today or utcnow().date()
    await set_rls_context(session, bypass=True)
    staff_by_organization: dict[uuid.UUID, dict[uuid.UUID, OrgRole]] = {}
    written = due_count = 0
    after: uuid.UUID | None = None
    while True:  # every due document, a batch at a time (by id)
        query = select(Document).where(Document.review_due_on <= today)
        if after is not None:
            query = query.where(Document.id > after)
        documents = list((await session.scalars(query.order_by(Document.id).limit(BATCH))).all())
        if not documents:
            break
        after = documents[-1].id
        due_count += len(documents)
        by_organization: dict[uuid.UUID, list[Document]] = {}
        for document in documents:
            by_organization.setdefault(document.organization_id, []).append(document)
        notifications = NotificationService(session, events=events, mailer=mailer)
        for organization_id, due in by_organization.items():
            if organization_id not in staff_by_organization:
                staff_by_organization[organization_id] = await active_staff(
                    session, organization_id
                )
            staff = staff_by_organization[organization_id]
            written += await notifications.add(organization_id, review_reminders(due, staff))
        await session.commit()
        await notifications.flush()
        await set_rls_context(session, bypass=True)  # the commit ended the transaction
    if written:
        logger.info("Document reviews: %d reminders for %d due documents", written, due_count)
    return written


async def run_review_reminder_job() -> int:
    """Entry point for the worker: its own event loop, database engine and broker."""
    from redis.asyncio import Redis

    from app.websocket.broker import RedisEventBroker
    from app.workers.queue import CeleryNotificationMailer

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    redis = Redis.from_url(settings.redis_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await remind_due_reviews(
                session, events=RedisEventBroker(redis), mailer=CeleryNotificationMailer()
            )
    finally:
        await redis.aclose()
        await engine.dispose()
