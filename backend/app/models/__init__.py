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
from app.models.assistant import (
    AssistantConversation,
    AssistantMessage,
    AssistantOutcome,
    AssistantRating,
    AssistantRole,
    ConversationStatus,
)
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.customer import Customer
from app.models.document import (
    ACTIVE_INGESTION,
    Document,
    DocumentChunk,
    DocumentStatus,
    DocumentVisibility,
)
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
from app.models.routing import (
    RoutingDecision,
    RoutingOutcome,
    RoutingRule,
    RoutingStrategy,
    RoutingTrigger,
)
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
from app.models.team import (
    DEFAULT_MAX_ACTIVE_TICKETS,
    AgentProfile,
    AgentSkill,
    AgentStatus,
    Team,
    TeamMember,
    TeamRole,
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
from app.models.triage import TicketTriage, TriageDecisionKind, TriageLabel, TriageOutcome
from app.models.user import User

__all__ = [
    "ACTIVE_INGESTION",
    "CUSTOMER_VISIBLE_EVENTS",
    "DEFAULT_MAX_ACTIVE_TICKETS",
    "AIAnswer",
    "AIDecision",
    "AIFeedback",
    "AIRun",
    "AIRunKind",
    "AgentProfile",
    "AgentSkill",
    "AgentStatus",
    "AnswerStatus",
    "AssistantConversation",
    "AssistantMessage",
    "AssistantOutcome",
    "AssistantRating",
    "AssistantRole",
    "AuditLogEntry",
    "Base",
    "BusinessCalendar",
    "BusinessHoliday",
    "BusinessHours",
    "Citation",
    "ConversationStatus",
    "Customer",
    "Document",
    "DocumentChunk",
    "DocumentStatus",
    "DocumentVisibility",
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
    "RoutingDecision",
    "RoutingOutcome",
    "RoutingRule",
    "RoutingStrategy",
    "RoutingTrigger",
    "SlaClockKind",
    "SlaClockState",
    "SlaPolicy",
    "Team",
    "TeamMember",
    "TeamRole",
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
    "TicketTriage",
    "TimestampMixin",
    "TriageDecisionKind",
    "TriageLabel",
    "TriageOutcome",
    "UUIDPrimaryKeyMixin",
    "User",
]
