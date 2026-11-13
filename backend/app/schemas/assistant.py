"""The customer assistant's API (Module 12).

Customers see what they were shown: replies, their outcome and sources. Staff also
see the AI run behind each reply and why the assistant couldn't answer.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import BaseModel, Field, StringConstraints

from app.models import (
    AnswerStatus,
    AssistantConversation,
    AssistantMessage,
    AssistantOutcome,
    AssistantRating,
    AssistantRole,
    ConversationStatus,
)
from app.schemas.retrieval import Query


class AssistantQuestion(BaseModel):
    question: Query
    conversation_id: uuid.UUID | None = Field(
        default=None,
        description="Continue this conversation (it must be open); omit to start a new one.",
    )


class AssistantSource(BaseModel):
    passage_number: int = Field(description="The [n] marker in the reply.")
    document_id: uuid.UUID | None
    title: str
    page_number: int | None
    page_end: int | None
    section_heading: str | None


class AssistantMessageRead(BaseModel):
    id: uuid.UUID
    role: AssistantRole
    body: str
    outcome: AssistantOutcome | None = Field(
        description="Replies only. `answered`: a complete, cited answer. `clarify`: a "
        "clarifying question. `unable`: the assistant can't answer reliably; offer a person."
    )
    sources: list[AssistantSource]
    rating: AssistantRating | None
    created_at: datetime

    @classmethod
    def build(cls, message: AssistantMessage) -> Self:
        return cls(
            id=message.id,
            role=message.role,
            body=message.body,
            outcome=message.outcome,
            sources=[AssistantSource.model_validate(s) for s in message.sources],
            rating=message.rating,
            created_at=message.created_at,
        )


class TicketReference(BaseModel):
    id: uuid.UUID
    number: int


class AssistantConversationSummary(BaseModel):
    id: uuid.UUID
    status: ConversationStatus
    title: str
    ticket: TicketReference | None = Field(description="The request it was handed over as.")
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    handed_off_at: datetime | None

    @classmethod
    def build(cls, conversation: AssistantConversation) -> Self:
        ticket = conversation.ticket
        return cls(
            id=conversation.id,
            status=conversation.status,
            title=conversation.title,
            ticket=TicketReference(id=ticket.id, number=ticket.number) if ticket else None,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            resolved_at=conversation.resolved_at,
            handed_off_at=conversation.handed_off_at,
        )


class AssistantConversationRead(AssistantConversationSummary):
    messages: list[AssistantMessageRead]

    @classmethod
    def build(cls, conversation: AssistantConversation) -> Self:
        summary = AssistantConversationSummary.build(conversation)
        return cls(
            **summary.model_dump(),
            messages=[AssistantMessageRead.build(m) for m in conversation.messages],
        )


# --- Staff ---------------------------------------------------------------------------------------


class AssistantRunInfo(BaseModel):
    """Why a reply was what it was (staff only)."""

    ai_run_id: uuid.UUID
    answer_status: AnswerStatus | None
    reason: str | None = Field(description="The evidence policy's reason, e.g. `low_coverage`.")


class StaffAssistantMessageRead(AssistantMessageRead):
    run: AssistantRunInfo | None

    @classmethod
    def build_with(cls, message: AssistantMessage, run: AssistantRunInfo | None) -> Self:
        return cls(**AssistantMessageRead.build(message).model_dump(), run=run)


class StaffAssistantConversationRead(AssistantConversationSummary):
    customer_name: str | None
    messages: list[StaffAssistantMessageRead]


class FeedbackRequest(BaseModel):
    message_id: uuid.UUID = Field(description="The reply being rated.")
    helpful: bool = Field(
        description="`true`: it answered the question (the conversation is resolved). "
        "`false`: it didn't (offer a person)."
    )


Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class HandoffRequest(BaseModel):
    note: Note | None = Field(
        default=None, description="Anything the customer wants to add for the support team."
    )


def source_payload(source: AssistantSource) -> dict[str, Any]:
    return source.model_dump(mode="json")
