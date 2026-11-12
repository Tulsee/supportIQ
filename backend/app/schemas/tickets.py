import uuid
from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import BaseModel, Field, StringConstraints

from app.models import (
    MessageAuthorType,
    Ticket,
    TicketEvent,
    TicketEventType,
    TicketMessage,
    TicketPriority,
    TicketStatus,
    User,
)
from app.schemas.customers import CustomerSummary
from app.schemas.sla import TicketSlaSummary

Subject = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]


class PersonSummary(BaseModel):
    id: uuid.UUID
    full_name: str

    @classmethod
    def build(cls, user: User | None) -> Self | None:
        return cls(id=user.id, full_name=user.full_name) if user else None


# --- Tickets ------------------------------------------------------------------------


class TicketCreate(BaseModel):
    subject: Subject
    message: Body = Field(description="The first message: what the customer needs.")
    customer_id: uuid.UUID | None = Field(
        default=None,
        description="Required for staff. Customers always open tickets for themselves.",
    )
    priority: TicketPriority | None = Field(default=None, description="Staff only.")
    assignee_id: uuid.UUID | None = Field(default=None, description="Staff only.")
    category_id: uuid.UUID | None = Field(default=None, description="Staff only.")
    team_id: uuid.UUID | None = Field(default=None, description="Staff only.")


class TicketUpdate(BaseModel):
    """Staff only. Only the fields provided are changed; ``assignee_id: null``
    unassigns. Status changes must follow the ticket lifecycle."""

    subject: Subject | None = None
    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    assignee_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = Field(
        default=None, description="An active category of the organization; null clears it."
    )
    team_id: uuid.UUID | None = Field(
        default=None, description="An active team of the organization; null clears it."
    )
    assignment_reason: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
        | None
    ) = Field(
        default=None,
        description="Why the assignee changes (with `assignee_id`); shown in the timeline "
        "to staff.",
    )


class CategorySummary(BaseModel):
    id: uuid.UUID
    key: str
    name: str


class TeamSummary(BaseModel):
    id: uuid.UUID
    name: str


class TicketRead(BaseModel):
    id: uuid.UUID
    number: int = Field(description="Sequential per organization; display as #number.")
    subject: str
    status: TicketStatus
    priority: TicketPriority
    customer: CustomerSummary
    assignee: PersonSummary | None
    category: CategorySummary | None = Field(description="Staff only; null for customers.")
    team: TeamSummary | None = Field(description="Staff only; null for customers.")
    language: str | None = Field(
        default=None, description="ISO 639-1 code of the customer's language (from triage)."
    )
    sla: TicketSlaSummary | None = Field(
        default=None,
        description="Staff only: the SLA clock that matters most now. Null without SLA "
        "clocks, and for customers.",
    )
    allowed_statuses: list[TicketStatus] = Field(
        description="Statuses this ticket can move to next (lifecycle rules)."
    )
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None

    @classmethod
    def build(
        cls,
        ticket: Ticket,
        allowed_statuses: list[TicketStatus],
        sla: TicketSlaSummary | None = None,
    ) -> Self:
        return cls(
            id=ticket.id,
            number=ticket.number,
            subject=ticket.subject,
            status=ticket.status,
            priority=ticket.priority,
            customer=CustomerSummary.build(ticket.customer),
            assignee=PersonSummary.build(ticket.assignee),
            category=(
                CategorySummary(
                    id=ticket.category.id, key=ticket.category.key, name=ticket.category.name
                )
                if ticket.category
                else None
            ),
            team=TeamSummary(id=ticket.team.id, name=ticket.team.name) if ticket.team else None,
            sla=sla,
            language=ticket.language,
            allowed_statuses=allowed_statuses,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
            resolved_at=ticket.resolved_at,
            closed_at=ticket.closed_at,
        )


class TicketCounts(BaseModel):
    by_status: dict[TicketStatus, int]
    unassigned: int = Field(description="Not closed or resolved, and nobody assigned.")
    assigned_to_me: int = Field(description="Not closed or resolved, assigned to you.")


# --- Messages and events ----------------------------------------------------------------


class MessageCreate(BaseModel):
    body: Body
    is_internal: bool = Field(
        default=False, description="Internal note, never shown to the customer (staff only)."
    )


class MessageRead(BaseModel):
    id: uuid.UUID
    ticket_id: uuid.UUID
    body: str
    is_internal: bool
    author_type: MessageAuthorType
    author: PersonSummary | None
    created_at: datetime

    @classmethod
    def build(cls, message: TicketMessage) -> Self:
        return cls(
            id=message.id,
            ticket_id=message.ticket_id,
            body=message.body,
            is_internal=message.is_internal,
            author_type=message.author_type,
            author=PersonSummary.build(message.author),
            created_at=message.created_at,
        )


class EventRead(BaseModel):
    id: uuid.UUID
    type: TicketEventType
    actor: PersonSummary | None
    data: dict[str, Any] = Field(
        description='What changed, e.g. {"from": "open", "to": "assigned"}. Assignment '
        'events hold {"id", "name"} snapshots of the people involved.'
    )
    created_at: datetime

    @classmethod
    def build(cls, event: TicketEvent) -> Self:
        return cls(
            id=event.id,
            type=event.type,
            actor=PersonSummary.build(event.actor),
            data=event.data,
            created_at=event.created_at,
        )
