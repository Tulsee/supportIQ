"""ORM models.

Import every model module here: Alembic's env.py imports this package, and
autogenerate can only detect tables whose models have been imported.
"""

from app.models.ai_run import (
    AIAnswer,
    AIDecision,
    AIFeedback,
    AIRun,
    AIRunKind,
    AnswerStatus,
    Citation,
    EvidenceAssessment,
    FeedbackRating,
    RetrievalResult,
)
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.customer import Customer
from app.models.document import ACTIVE_INGESTION, Document, DocumentChunk, DocumentStatus
from app.models.invitation import OrganizationInvitation
from app.models.notification import (
    AuditLogEntry,
    Notification,
    NotificationDelivery,
    NotificationKind,
    NotificationPreference,
)
from app.models.organization import Organization, OrganizationMember, OrgRole
from app.models.refresh_token import RefreshToken
from app.models.sla import (
    BusinessCalendar,
    BusinessHoliday,
    BusinessHours,
    SlaClockKind,
    SlaClockState,
    SlaPolicy,
    TicketSlaClock,
    TicketSlaPause,
)
from app.models.ticket import (
    CUSTOMER_VISIBLE_EVENTS,
    MessageAuthorType,
    Ticket,
    TicketCategory,
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
    "AIAnswer",
    "AIDecision",
    "AIFeedback",
    "AIRun",
    "AIRunKind",
    "AnswerStatus",
    "AuditLogEntry",
    "Base",
    "BusinessCalendar",
    "BusinessHoliday",
    "BusinessHours",
    "Citation",
    "Customer",
    "Document",
    "DocumentChunk",
    "DocumentStatus",
    "EvidenceAssessment",
    "FeedbackRating",
    "MessageAuthorType",
    "Notification",
    "NotificationDelivery",
    "NotificationKind",
    "NotificationPreference",
    "OrgRole",
    "Organization",
    "OrganizationInvitation",
    "OrganizationMember",
    "RefreshToken",
    "RetrievalResult",
    "SlaClockKind",
    "SlaClockState",
    "SlaPolicy",
    "Ticket",
    "TicketCategory",
    "TicketCounter",
    "TicketEvent",
    "TicketEventType",
    "TicketMessage",
    "TicketPriority",
    "TicketSlaClock",
    "TicketSlaPause",
    "TicketStatus",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "User",
]
