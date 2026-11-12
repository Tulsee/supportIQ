"""The signed-in user's notifications in the organization in ``X-Organization-Id``
(Module 9), their delivery preferences, and the organization's audit log (admins)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession, Pagination, TenantMembership
from app.schemas.common import Page, error_responses
from app.schemas.notifications import (
    AuditEntryRead,
    MarkRead,
    NotificationPage,
    NotificationPreferences,
    NotificationPreferenceUpdate,
    NotificationRead,
    UnreadCount,
)
from app.services.audit import AuditService
from app.services.notifications import NotificationService

router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    responses=error_responses(401, 403, 404, 422),
)
audit_router = APIRouter(
    prefix="/audit-log", tags=["audit log"], responses=error_responses(401, 403, 404, 422)
)


@router.get("", summary="List your notifications")
async def list_notifications(
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    page: Pagination,
    unread_only: Annotated[bool, Query(description="Only unread ones.")] = False,
) -> NotificationPage:
    """Newest first, with the unread count for the badge."""
    items, total, unread = await NotificationService(session).list_notifications(
        member, user, unread_only=unread_only, limit=page.limit, offset=page.offset
    )
    return NotificationPage(
        items=[NotificationRead.build(n) for n in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
        unread=unread,
    )


@router.post("/read", summary="Mark notifications as read")
async def mark_read(
    body: MarkRead, member: TenantMembership, user: CurrentUser, session: DbSession
) -> UnreadCount:
    unread = await NotificationService(session).mark_read(
        member, user, None if body.all else body.ids
    )
    return UnreadCount(unread=unread)


@router.get("/preferences", summary="Your notification preferences")
async def get_preferences(
    member: TenantMembership, user: CurrentUser, session: DbSession
) -> NotificationPreferences:
    """Each kind that applies to your role: `off`, `in_app`, or `email` (in the app and
    by email)."""
    return NotificationPreferences.build(
        await NotificationService(session).preferences(member, user)
    )


@router.put("/preferences", summary="Change a notification preference")
async def set_preference(
    body: NotificationPreferenceUpdate,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
) -> NotificationPreferences:
    return NotificationPreferences.build(
        await NotificationService(session).set_preference(member, user, body.kind, body.delivery)
    )


EntityType = Literal["organization", "member", "invitation", "document"]


@audit_router.get("", summary="The organization's audit log (admin)")
async def list_audit_log(
    member: TenantMembership,
    session: DbSession,
    page: Pagination,
    entity_type: Annotated[
        EntityType | None, Query(description="Only this kind of entity.")
    ] = None,
) -> Page[AuditEntryRead]:
    """Administrative changes, newest first: who changed what, with a minimal
    before/after. Entries can't be edited or deleted."""
    items, total = await AuditService(session).list_entries(
        member, entity_type=entity_type, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[AuditEntryRead.build(e) for e in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
