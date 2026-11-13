import uuid
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import Organization, OrganizationMember, OrgRole
from app.models.organization import DEFAULT_AUTO_CLOSE_DAYS

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


AutoCloseDays = Annotated[int, Field(ge=0, le=90)]
# off: tickets are only routed when someone asks (reroute); shadow: new tickets get a
# recorded decision that isn't carried out (to compare with what people do); on:
# new tickets are routed automatically.
RoutingMode = Literal["off", "shadow", "on"]
TriageMode = Literal["off", "shadow", "on"]
# The customer assistant in the portal (Module 12), and which model answers it:
# "local" keeps customers' questions on the local model whatever the provider.
SelfServiceMode = Literal["off", "on"]
SelfServiceModel = Literal["default", "local"]


class OrganizationSettings(BaseModel):
    auto_close_after_days: AutoCloseDays = Field(
        default=DEFAULT_AUTO_CLOSE_DAYS,
        description="Resolved tickets with no reply for this many days are closed "
        "automatically. 0 turns it off.",
    )
    routing_mode: RoutingMode = Field(
        default="off",
        description="Automatic routing of new tickets: `off`, `shadow` (decide and record, "
        "but don't assign) or `on`.",
    )
    triage_mode: TriageMode = Field(
        default="off",
        description="AI triage of new tickets: `off`, `shadow` (predict and record silently, "
        "to measure accuracy) or `on` (apply confident categories, suggest the rest).",
    )
    self_service: SelfServiceMode = Field(
        default="off",
        description="The customer assistant in the portal: customers ask questions answered "
        "from public documents, and can hand over to a person.",
    )
    self_service_model: SelfServiceModel = Field(
        default="default",
        description="`local`: customers' questions are always answered by the local model, "
        "never sent to a hosted one.",
    )


class OrganizationSettingsUpdate(BaseModel):
    auto_close_after_days: AutoCloseDays | None = None
    routing_mode: RoutingMode | None = None
    triage_mode: TriageMode | None = None
    self_service: SelfServiceMode | None = None
    self_service_model: SelfServiceModel | None = None


class OrganizationUpdate(BaseModel):
    """Admin only. Only the fields provided are changed."""

    name: OrgName | None = None
    settings: OrganizationSettingsUpdate | None = None


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    settings: OrganizationSettings
    created_at: datetime


class OrganizationWithRole(OrganizationRead):
    role: OrgRole = Field(description="The current user's role in this organization.")

    @classmethod
    def build(cls, organization: Organization, role: OrgRole) -> Self:
        return cls(
            id=organization.id,
            name=organization.name,
            slug=organization.slug,
            settings=OrganizationSettings.model_validate(organization.settings or {}),
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
