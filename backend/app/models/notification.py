"""In-app notifications, per-user delivery preferences, and the audit log (Module 9)."""

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
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum_type


class NotificationKind(enum.StrEnum):
    # Staff
    TICKET_ASSIGNED = "ticket_assigned"  # a ticket was assigned to you
    CUSTOMER_REPLIED = "customer_replied"  # the customer replied on your ticket
    AI_ESCALATED = "ai_escalated"  # the AI couldn't draft a supported reply on your ticket
    TICKET_CLOSED = "ticket_closed"  # the customer closed your ticket
    # Customers
    AGENT_REPLIED = "agent_replied"  # support replied to your request


STAFF_KINDS = (
    NotificationKind.TICKET_ASSIGNED,
    NotificationKind.CUSTOMER_REPLIED,
    NotificationKind.AI_ESCALATED,
    NotificationKind.TICKET_CLOSED,
)
CUSTOMER_KINDS = (NotificationKind.AGENT_REPLIED,)


class NotificationDelivery(enum.StrEnum):
    OFF = "off"
    IN_APP = "in_app"
    EMAIL = "email"  # in the app and by email


# Without a saved preference. Customers aren't usually in the app, so replies to
# them are emailed too.
DEFAULT_DELIVERY: dict[NotificationKind, NotificationDelivery] = {
    NotificationKind.TICKET_ASSIGNED: NotificationDelivery.IN_APP,
    NotificationKind.CUSTOMER_REPLIED: NotificationDelivery.IN_APP,
    NotificationKind.AI_ESCALATED: NotificationDelivery.IN_APP,
    NotificationKind.TICKET_CLOSED: NotificationDelivery.IN_APP,
    NotificationKind.AGENT_REPLIED: NotificationDelivery.EMAIL,
}


class Notification(UUIDPrimaryKeyMixin, Base):
    """Something a person should know about. Only its recipient can read it (RLS).

    ``dedupe_key`` makes delivery idempotent: the same event (a retried job, a
    repeated scan) never notifies the same person twice.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key"),
        Index("ix_notifications_user_id_organization_id_seq", "user_id", "organization_id", "seq"),
    )

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[NotificationKind] = mapped_column(
        str_enum_type(NotificationKind, "notification_kind")
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="", server_default="")
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    dedupe_key: Mapped[str] = mapped_column(String(200))
    email: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NotificationPreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """How a person wants one kind of notification in one organization."""

    __tablename__ = "notification_preferences"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", "kind"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[NotificationKind] = mapped_column(
        str_enum_type(NotificationKind, "notification_kind")
    )
    delivery: Mapped[NotificationDelivery] = mapped_column(
        str_enum_type(NotificationDelivery, "notification_delivery")
    )


class AuditLogEntry(UUIDPrimaryKeyMixin, Base):
    """An administrative change: who did what to which entity, and a minimal
    before/after. Append-only: the API role can insert and read, never update or
    delete (grants, not just policies)."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_organization_id_seq", "organization_id", "seq"),)

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Snapshot, so the entry stays readable after the account is deleted.
    actor_name: Mapped[str | None] = mapped_column(String(200))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(80))
    summary: Mapped[str] = mapped_column(String(500))
    changes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
