import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import Organization, OrganizationMember, OrgRole

OrgName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]
OrgSlug = Annotated[
    str,
    StringConstraints(min_length=3, max_length=50, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
]


class OrganizationCreate(BaseModel):
    name: OrgName
    slug: OrgSlug | None = Field(
        default=None,
        description="Lower-case, URL-safe identifier. Generated from the name when omitted.",
    )


class OrganizationUpdate(BaseModel):
    name: OrgName


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime


class OrganizationWithRole(OrganizationRead):
    role: OrgRole = Field(description="The current user's role in this organization.")

    @classmethod
    def build(cls, organization: Organization, role: OrgRole) -> Self:
        return cls(
            id=organization.id,
            name=organization.name,
            slug=organization.slug,
            created_at=organization.created_at,
            role=role,
        )


class MemberRead(BaseModel):
    id: uuid.UUID = Field(description="Membership id (use it in /members/{member_id}).")
    user_id: uuid.UUID
    email: str
    full_name: str
    role: OrgRole
    created_at: datetime

    @classmethod
    def build(cls, member: OrganizationMember) -> Self:
        return cls(
            id=member.id,
            user_id=member.user_id,
            email=member.user.email,
            full_name=member.user.full_name,
            role=member.role,
            created_at=member.created_at,
        )


class MemberUpdate(BaseModel):
    role: OrgRole
