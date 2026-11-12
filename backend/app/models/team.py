"""Teams and agents (Module 10, phase 5): who works on which tickets, how much each
agent can take on, what they're good at, and whether they're working right now.

Routing (phase 6) sends tickets to a team and picks an eligible agent from it; the
SLA monitor alerts a ticket's team leads. All of it is staff-only under RLS.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum_type
from app.models.user import User

# Active tickets (assigned or in progress) an agent takes before routing skips them.
DEFAULT_MAX_ACTIVE_TICKETS = 10


class TeamRole(enum.StrEnum):
    MEMBER = "member"
    LEAD = "lead"  # also receives the team's SLA alerts


class AgentStatus(enum.StrEnum):
    """What the agent says they're doing. Whether they're online at all comes from
    their open connection to the app (see ``app.websocket.presence``)."""

    AVAILABLE = "available"
    BUSY = "busy"
    AWAY = "away"


class TeamMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[TeamRole] = mapped_column(
        str_enum_type(TeamRole, "team_role"),
        default=TeamRole.MEMBER,
        server_default=TeamRole.MEMBER.value,
    )

    user: Mapped[User] = relationship(lazy="raise")


class Team(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A group of agents that handles a kind of ticket. Deactivated rather than
    deleted, so tickets and routing history keep their team."""

    __tablename__ = "teams"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(300), default="", server_default="")
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    members: Mapped[list[TeamMember]] = relationship(
        lazy="raise", cascade="all, delete-orphan", order_by=TeamMember.created_at
    )


# Names are unique per organization, ignoring case.
Index("uq_teams_organization_id_name", Team.organization_id, func.lower(Team.name), unique=True)


class AgentProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Routing settings of one staff member in one organization. Created on first
    use; an agent without a row is available with the default capacity."""

    __tablename__ = "agent_profiles"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id"),
        CheckConstraint("max_active_tickets BETWEEN 0 AND 200", name="capacity"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[AgentStatus] = mapped_column(
        str_enum_type(AgentStatus, "agent_status"),
        default=AgentStatus.AVAILABLE,
        server_default=AgentStatus.AVAILABLE.value,
    )
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 0 = takes no automatically routed tickets (manual assignment still works).
    max_active_tickets: Mapped[int] = mapped_column(
        SmallInteger,
        default=DEFAULT_MAX_ACTIVE_TICKETS,
        server_default=text(str(DEFAULT_MAX_ACTIVE_TICKETS)),
    )


class AgentSkill(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A skill (a slug such as ``refunds``) at a level from 1 (basic) to 5 (expert).
    Routing rules require skills at a minimum level."""

    __tablename__ = "agent_skills"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", "skill"),
        CheckConstraint("level BETWEEN 1 AND 5", name="level"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    skill: Mapped[str] = mapped_column(String(50))
    level: Mapped[int] = mapped_column(SmallInteger)
