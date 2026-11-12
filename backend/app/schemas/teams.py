"""Teams, agent profiles, skills and presence (Module 10, phase 5)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, Field, StringConstraints, model_validator

from app.models import AgentStatus, OrgRole, Team, TeamRole

TeamName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


def _normalize_slug(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


SkillSlug = Annotated[
    str,
    BeforeValidator(_normalize_slug),  # before the pattern check: " Refunds " is fine
    StringConstraints(min_length=2, max_length=50, pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$"),
]
MAX_TEAM_MEMBERS = 200
MAX_SKILLS = 50


# --- Teams ------------------------------------------------------------------------------


class TeamCreate(BaseModel):
    name: TeamName = Field(description="Unique in the organization, ignoring case.")
    description: Description = ""


class TeamUpdate(BaseModel):
    """Only the fields provided are changed. Deactivate instead of deleting: tickets
    keep their team, and members are kept for when it's reactivated."""

    name: TeamName | None = None
    description: Description | None = None
    is_active: bool | None = None


class TeamMemberIn(BaseModel):
    user_id: uuid.UUID = Field(description="An admin or agent of the organization.")
    role: TeamRole = Field(
        default=TeamRole.MEMBER, description="Leads also receive the team's SLA alerts."
    )


class TeamMembersUpdate(BaseModel):
    """The team's complete membership: people left out are removed."""

    members: list[TeamMemberIn] = Field(max_length=MAX_TEAM_MEMBERS)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        ids = [m.user_id for m in self.members]
        if len(ids) != len(set(ids)):
            raise ValueError("Each person can be listed only once")
        return self


class TeamMemberRead(BaseModel):
    user_id: uuid.UUID
    full_name: str
    email: str
    role: TeamRole


class TeamRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    is_active: bool
    members: list[TeamMemberRead] = Field(description="Leads first, then by name.")

    @classmethod
    def build(cls, team: Team) -> Self:
        members = sorted(
            team.members, key=lambda m: (m.role != TeamRole.LEAD, m.user.full_name.lower())
        )
        return cls(
            id=team.id,
            name=team.name,
            description=team.description,
            is_active=team.is_active,
            members=[
                TeamMemberRead(
                    user_id=m.user_id, full_name=m.user.full_name, email=m.user.email, role=m.role
                )
                for m in members
            ],
        )


# --- Agents -----------------------------------------------------------------------------


class SkillLevel(BaseModel):
    skill: SkillSlug = Field(description="A short slug such as `refunds` (lower case).")
    level: int = Field(ge=1, le=5, description="1 = basic, 5 = expert.")


class AgentProfileUpdate(BaseModel):
    """Only the fields provided are changed; ``skills`` replaces the whole list."""

    max_active_tickets: int | None = Field(
        default=None,
        ge=0,
        le=200,
        description="Tickets assigned or in progress before routing skips this agent "
        "(0: no automatic assignments; manual assignment still works).",
    )
    skills: list[SkillLevel] | None = Field(default=None, max_length=MAX_SKILLS)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if self.skills is not None:
            names = [s.skill for s in self.skills]
            if len(names) != len(set(names)):
                raise ValueError("Each skill can be listed only once")
        return self


class PresenceUpdate(BaseModel):
    status: AgentStatus


class AgentTeam(BaseModel):
    id: uuid.UUID
    name: str
    role: TeamRole


class AgentRead(BaseModel):
    """A staff member as routing sees them."""

    user_id: uuid.UUID
    full_name: str
    email: str
    role: OrgRole = Field(description="Their role in the organization (admin or agent).")
    status: AgentStatus = Field(description="What they said they're doing.")
    status_changed_at: datetime | None
    online: bool = Field(description="Has the app open (seen in the last 90 seconds).")
    last_seen_at: datetime | None = Field(description="Only while online.")
    presence: Literal["available", "busy", "away", "offline"] = Field(
        description="`status` while online, otherwise `offline`."
    )
    max_active_tickets: int
    active_tickets: int = Field(description="Assigned to them and assigned or in progress.")
    skills: list[SkillLevel] = Field(description="By skill name.")
    teams: list[AgentTeam] = Field(description="Active teams only.")
