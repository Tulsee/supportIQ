"""AI ticket triage (Module 11): a prediction per ticket, and the labels the neighbour
vote learns from. Staff-only (RLS).

A prediction is ``confident`` (applied when the organization's triage mode is on),
``unsure`` (the top guess is only a suggestion), or ``failed`` (the model couldn't
be used; the neighbours alone can still be confident). When a person later sets,
changes, clears or confirms the category, the prediction records the outcome: that's
how accuracy is measured in use.

Labels are tickets whose category a person chose or confirmed, never ones only the
AI set, so the neighbour vote can't learn from its own mistakes.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import REAL, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, str_enum_type


class TriageDecisionKind(enum.StrEnum):
    CONFIDENT = "confident"
    UNSURE = "unsure"
    FAILED = "failed"


class TriageOutcome(enum.StrEnum):
    CONFIRMED = "confirmed"  # a person kept or accepted the predicted category
    CHANGED = "changed"  # a person chose another category
    CLEARED = "cleared"  # a person removed it (the ticket has no category)


class TicketTriage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ticket_triage"
    __table_args__ = (
        Index("ix_ticket_triage_organization_id_created_at", "organization_id", "created_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), unique=True
    )
    mode: Mapped[str] = mapped_column(String(16))  # "shadow" or "on" when it ran
    decision: Mapped[TriageDecisionKind] = mapped_column(
        str_enum_type(TriageDecisionKind, "triage_decision")
    )
    applied: Mapped[bool] = mapped_column()
    # The top guess (even when unsure); the key and name are copied.
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ticket_categories.id", ondelete="SET NULL")
    )
    category_key: Mapped[str | None] = mapped_column(String(50))
    category_name: Mapped[str | None] = mapped_column(String(100))
    confidence: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    alpha: Mapped[float] = mapped_column(Float)
    agree: Mapped[bool | None] = mapped_column()
    priority: Mapped[str | None] = mapped_column(String(16))  # suggested, never applied
    language: Mapped[str | None] = mapped_column(String(8))
    urgency_cues: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # {"llm": {...} | None, "neighbours": {...} | None, "scores": {...}}
    signals: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(String(200))
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    latency_ms: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Filled in when a person first sets, changes, clears or confirms the category.
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    final_category_key: Mapped[str | None] = mapped_column(String(50))
    outcome: Mapped[TriageOutcome | None] = mapped_column(
        str_enum_type(TriageOutcome, "triage_outcome")
    )


class TriageLabel(Base):
    """A ticket whose category a person set or confirmed: an example for the neighbour
    vote. The embedding (subject and opening message) is filled in by the triage job."""

    __tablename__ = "triage_labels"
    __table_args__ = (
        Index("ix_triage_labels_organization_id_updated_at", "organization_id", "updated_at"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    category_key: Mapped[str] = mapped_column(String(50))
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(REAL))
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
