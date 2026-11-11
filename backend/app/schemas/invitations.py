import uuid
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field

from app.models import OrganizationInvitation, OrgRole
from app.schemas.users import NormalizedEmail

InvitationStatus = Literal["pending", "accepted", "revoked", "expired"]

# Raw tokens are 64 url-safe characters; bound the input before hashing it.
InvitationToken = Annotated[str, Field(min_length=16, max_length=256)]


def invitation_status(invitation: OrganizationInvitation, now: datetime) -> InvitationStatus:
    if invitation.accepted_at is not None:
        return "accepted"
    if invitation.revoked_at is not None:
        return "revoked"
    if invitation.expires_at <= now:
        return "expired"
    return "pending"


class InvitationCreate(BaseModel):
    email: NormalizedEmail
    role: OrgRole


class InvitationRead(BaseModel):
    """An invitation as seen by the organization's admins (never includes the token)."""

    id: uuid.UUID
    email: str
    role: OrgRole
    status: InvitationStatus
    invited_by_name: str | None
    expires_at: datetime
    created_at: datetime

    @classmethod
    def build(cls, invitation: OrganizationInvitation, now: datetime) -> Self:
        return cls(
            id=invitation.id,
            email=invitation.email,
            role=invitation.role,
            status=invitation_status(invitation, now),
            invited_by_name=invitation.invited_by.full_name if invitation.invited_by else None,
            expires_at=invitation.expires_at,
            created_at=invitation.created_at,
        )


class InvitationTokenRequest(BaseModel):
    token: InvitationToken


class InvitationPreview(BaseModel):
    """What the invitee sees before accepting (the token is the credential)."""

    organization_name: str
    email: str = Field(description="The invitation is only valid for this account email.")
    role: OrgRole
    invited_by_name: str | None
    status: InvitationStatus
    expires_at: datetime
    account_exists: bool = Field(
        description="Whether to send the invitee to sign in or to create an account."
    )
