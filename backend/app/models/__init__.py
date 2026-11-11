"""ORM models.

Import every model module here: Alembic's env.py imports this package, and
autogenerate can only detect tables whose models have been imported.
"""

from app.models.ai_run import AIRun, AIRunKind, RetrievalResult
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.customer import Customer
from app.models.document import ACTIVE_INGESTION, Document, DocumentChunk, DocumentStatus
from app.models.invitation import OrganizationInvitation
from app.models.organization import Organization, OrganizationMember, OrgRole
from app.models.refresh_token import RefreshToken
from app.models.ticket import (
    CUSTOMER_VISIBLE_EVENTS,
    MessageAuthorType,
    Ticket,
    TicketCounter,
    TicketEvent,
    TicketEventType,
    TicketMessage,
    TicketPriority,
    TicketStatus,
)
from app.models.user import User

__all__ = [
    "ACTIVE_INGESTION",
    "CUSTOMER_VISIBLE_EVENTS",
    "AIRun",
    "AIRunKind",
    "Base",
    "Customer",
    "Document",
    "DocumentChunk",
    "DocumentStatus",
    "MessageAuthorType",
    "OrgRole",
    "Organization",
    "OrganizationInvitation",
    "OrganizationMember",
    "RefreshToken",
    "RetrievalResult",
    "Ticket",
    "TicketCounter",
    "TicketEvent",
    "TicketEventType",
    "TicketMessage",
    "TicketPriority",
    "TicketStatus",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "User",
]
