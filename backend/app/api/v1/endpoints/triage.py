"""AI triage on tickets (Module 11), in the organization in ``X-Organization-Id``."""

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession, EventBrokerDep, NotificationsDep, TenantMembership
from app.schemas.common import error_responses
from app.schemas.triage import TriageRead
from app.services.triage import accept_suggestion, get_triage

router = APIRouter(
    prefix="/tickets", tags=["triage"], responses=error_responses(401, 403, 404, 409, 422)
)


@router.get("/{ticket_id}/triage", summary="The ticket's AI triage (staff)")
async def ticket_triage(
    ticket_id: uuid.UUID, member: TenantMembership, session: DbSession
) -> TriageRead | None:
    """The prediction made when the ticket was opened (null if triage didn't run): the
    category guess and how sure, both signals, suggested priority, language, urgency
    cues, and what a person did with it."""
    row, reviewer = await get_triage(session, member, ticket_id)
    return TriageRead.build(row, reviewer) if row else None


@router.post("/{ticket_id}/triage/accept", summary="Accept the suggested category (staff)")
async def accept_triage(
    ticket_id: uuid.UUID,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    events: EventBrokerDep,
    notifications: NotificationsDep,
) -> TriageRead:
    """Sets the suggested category (or confirms the one triage applied), records the
    review, and makes the ticket an example for future triage. 404 `no_suggestion` if
    there's no category guess."""
    row = await accept_suggestion(session, member, user, ticket_id, events, notifications)
    return TriageRead.build(row, user)
