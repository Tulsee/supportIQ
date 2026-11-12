"""Automatic routing (Module 10, phase 6): ordered rules that send tickets to a team,
and a record of every routing decision with its explanation. Staff-only (RLS).

The matching and ranking logic is pure (``app/domain/routing.py``); applying it
(locks, assignment, notifications) is ``app/services/routing.py``.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.routing import Outcome, Strategy
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum_type
from app.models.team import Team

# least_loaded: lowest share of capacity in use; round_robin: assigned automatically
# least recently; skill_match: highest total level in the required skills; manual:
# only set the team, and someone in it picks the ticket up.
RoutingStrategy = Strategy
# assigned; team_only (manual strategy); no_agent (a rule matched but nobody in the
# team could take it); no_rule (no active rule matched).
RoutingOutcome = Outcome


class RoutingTrigger(enum.StrEnum):
    CREATED = "created"  # the job enqueued when the ticket was opened
    SWEEP = "sweep"  # the scheduler caught a ticket whose job never ran
    REROUTE = "reroute"  # a staff member asked for routing to run again


class RoutingRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """When a ticket matches ``conditions``, send it to ``team`` and pick an agent with
    ``strategy``. Rules are tried in ``position`` order; the first match wins.
    ``version`` goes up when what the rule does changes (decisions record it)."""

    __tablename__ = "routing_rules"
    __table_args__ = (
        Index("ix_routing_rules_organization_id_position", "organization_id", "position"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer)
    # {"all": [{"field", "operator", "value"}], "any": [...]}, validated by the API.
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB)
    team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    strategy: Mapped[RoutingStrategy] = mapped_column(
        str_enum_type(RoutingStrategy, "routing_strategy")
    )
    # [{"skill": "refunds", "level": 3}]: every one needed, at least at that level.
    required_skills: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))

    team: Mapped[Team] = relationship(lazy="raise")


# Rule names are unique per organization, ignoring case.
Index(
    "uq_routing_rules_organization_id_name",
    RoutingRule.organization_id,
    func.lower(RoutingRule.name),
    unique=True,
)


class RoutingDecision(UUIDPrimaryKeyMixin, Base):
    """One routing run on one ticket: what matched, who could and couldn't take it,
    what happened, and why, in words. Names are copied so the record still reads
    right after a rule, team or person changes. No message content is stored.

    ``applied`` is false in shadow mode (decided, not carried out). Automatic runs
    happen at most once per ticket (unique index); reroutes can repeat."""

    __tablename__ = "routing_decisions"
    __table_args__ = (
        Index("ix_routing_decisions_ticket_id_seq", "ticket_id", "seq"),
        Index("ix_routing_decisions_organization_id_created_at", "organization_id", "created_at"),
        Index(
            "uq_routing_decisions_automatic",
            "ticket_id",
            unique=True,
            postgresql_where=text("trigger <> 'reroute'"),
        ),
    )

    # Insertion order (decisions in one transaction share its now()).
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"))
    trigger: Mapped[RoutingTrigger] = mapped_column(
        str_enum_type(RoutingTrigger, "routing_trigger")
    )
    applied: Mapped[bool] = mapped_column()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("routing_rules.id", ondelete="SET NULL")
    )
    rule_name: Mapped[str | None] = mapped_column(String(100))
    rule_version: Mapped[int | None] = mapped_column(Integer)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    team_name: Mapped[str | None] = mapped_column(String(100))
    strategy: Mapped[RoutingStrategy | None] = mapped_column(
        str_enum_type(RoutingStrategy, "routing_strategy")
    )
    outcome: Mapped[RoutingOutcome] = mapped_column(
        str_enum_type(RoutingOutcome, "routing_outcome")
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    assignee_name: Mapped[str | None] = mapped_column(String(200))
    # [{"rule_id", "name", "matched", "detail"}] in the order they were tried.
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # [{"user_id", "name", "eligible", "reasons", "active_tickets", ...}], ranked.
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
