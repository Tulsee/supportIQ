"""Organization invitations.

Two routers:
- ``org_router``: admins manage invitations of their organization.
- ``public_router``: the invitee previews and accepts using the emailed token
  (sent in the request body, never in a URL).
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, status

from app.api.deps import CurrentUser, DbSession, EmailSenderDep, OrgMembership, Pagination
from app.core.email import send_email_safely
from app.core.security import utcnow
from app.schemas.common import Page, error_responses
from app.schemas.invitations import (
    InvitationCreate,
    InvitationPreview,
    InvitationRead,
    InvitationTokenRequest,
    invitation_status,
)
from app.schemas.organizations import OrganizationWithRole
from app.services.invitations import InvitationService

org_router = APIRouter(
    prefix="/organizations/{org_id}/invitations",
    tags=["invitations"],
    responses=error_responses(401, 403, 404, 409, 422),
)
public_router = APIRouter(
    prefix="/invitations",
    tags=["invitations"],
    responses=error_responses(401, 403, 404, 409, 410, 422),
)


@org_router.get("", summary="List open invitations (admin)")
async def list_invitations(
    membership: OrgMembership, session: DbSession, page: Pagination
) -> Page[InvitationRead]:
    invitations, total = await InvitationService(session).list_open(
        membership, limit=page.limit, offset=page.offset
    )
    now = utcnow()
    return Page(
        items=[InvitationRead.build(invitation, now) for invitation in invitations],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@org_router.post("", status_code=status.HTTP_201_CREATED, summary="Invite someone by email (admin)")
async def create_invitation(
    body: InvitationCreate,
    membership: OrgMembership,
    user: CurrentUser,
    session: DbSession,
    email_sender: EmailSenderDep,
    background_tasks: BackgroundTasks,
) -> InvitationRead:
    """Emails an invitation link. Inviting the same email again replaces the
    previous link. Works whether or not the person already has an account."""
    invitation, email = await InvitationService(session).create(membership, user, body)
    # Sent after the response, so a slow or failing mail server doesn't fail the request.
    background_tasks.add_task(send_email_safely, email_sender, email)
    return InvitationRead.build(invitation, utcnow())


@org_router.delete(
    "/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an invitation (admin)",
)
async def revoke_invitation(
    invitation_id: uuid.UUID, membership: OrgMembership, session: DbSession
) -> None:
    await InvitationService(session).revoke(membership, invitation_id)


@public_router.post("/lookup", summary="Preview an invitation from its token")
async def preview_invitation(body: InvitationTokenRequest, session: DbSession) -> InvitationPreview:
    """No authentication needed: possession of the emailed token is the credential."""
    invitation, account_exists = await InvitationService(session).preview(body.token)
    return InvitationPreview(
        organization_name=invitation.organization.name,
        email=invitation.email,
        role=invitation.role,
        invited_by_name=invitation.invited_by.full_name if invitation.invited_by else None,
        status=invitation_status(invitation, utcnow()),
        expires_at=invitation.expires_at,
        account_exists=account_exists,
    )


@public_router.post("/accept", summary="Accept an invitation")
async def accept_invitation(
    body: InvitationTokenRequest, user: CurrentUser, session: DbSession
) -> OrganizationWithRole:
    """The signed-in user's email must match the invitation's email."""
    membership = await InvitationService(session).accept(user, body.token)
    return OrganizationWithRole.build(membership.organization, membership.role)
