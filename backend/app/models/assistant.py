"""The customer assistant (Module 12): customers' conversations with the portal
assistant, as they saw them.

The full AI run behind each reply (retrieval, prompt, assessment) is an ``ai_runs``
row of kind ``self_service``, for staff; these tables hold what the customer was
shown, so the transcript stays readable to them under row-level security.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum_type
from app.models.customer import Customer
from app.models.ticket import Ticket


class ConversationStatus(enum.StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"  # the customer confirmed an answer helped
    HANDED_OFF = "handed_off"  # the customer opened a request from it (``ticket_id``)


class AssistantRole(enum.StrEnum):
    CUSTOMER = "customer"
    ASSISTANT = "assistant"


class AssistantOutcome(enum.StrEnum):
    """What a reply was, as the customer sees it (the AI run has the details)."""

    ANSWERED = "answered"  # a complete, cited, supported answer
    CLARIFY = "clarify"  # a clarifying question
    UNABLE = "unable"  # anything else: "I can't answer this reliably", offer a person


class AssistantRating(enum.StrEnum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"


class AssistantConversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "assistant_conversations"
    __table_args__ = (
        Index("ix_assistant_conversations_user", "organization_id", "user_id", "updated_at"),
        Index("ix_assistant_conversations_org_created", "organization_id", "created_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL")
    )
    status: Mapped[ConversationStatus] = mapped_column(
        str_enum_type(ConversationStatus, "assistant_conversation_status"),
        default=ConversationStatus.OPEN,
        server_default=ConversationStatus.OPEN.value,
    )
    title: Mapped[str] = mapped_column(String(300))  # the first question, shortened
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL"), index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer | None] = relationship(lazy="raise")
    ticket: Mapped[Ticket | None] = relationship(lazy="raise")
    messages: Mapped[list["AssistantMessage"]] = relationship(
        lazy="raise", order_by="AssistantMessage.seq", viewonly=True
    )


class AssistantMessage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "assistant_messages"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"), index=True
    )
    # Insertion order: messages written in one transaction share its now().
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    role: Mapped[AssistantRole] = mapped_column(str_enum_type(AssistantRole, "assistant_role"))
    body: Mapped[str] = mapped_column(Text)
    # Assistant replies only:
    outcome: Mapped[AssistantOutcome | None] = mapped_column(
        str_enum_type(AssistantOutcome, "assistant_outcome")
    )
    # The sources as shown: [{passage_number, document_id, title, page_number,
    # page_end, section_heading}], copied so the transcript doesn't change later.
    sources: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=sql_text("'[]'::jsonb")
    )
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="SET NULL")
    )
    rating: Mapped[AssistantRating | None] = mapped_column(
        str_enum_type(AssistantRating, "assistant_rating")
    )
    rated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
