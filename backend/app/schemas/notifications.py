"""Notifications, delivery preferences and the audit log (Module 9)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.notification import (
    AuditLogEntry,
    Notification,
    NotificationDelivery,
    NotificationKind,
)
from app.schemas.common import Page


class NotificationRead(BaseModel):
    id: uuid.UUID
    kind: NotificationKind
    title: str
    body: str
    ticket_id: uuid.UUID | None
    data: dict[str, Any]
    read_at: datetime | None
    created_at: datetime

    @classmethod
    def build(cls, n: Notification) -> "NotificationRead":
        return cls(
            id=n.id,
            kind=n.kind,
            title=n.title,
            body=n.body,
            ticket_id=n.ticket_id,
            data=n.data,
            read_at=n.read_at,
            created_at=n.created_at,
        )


class NotificationPage(Page[NotificationRead]):
    unread: int = Field(description="Unread notifications in this organization.")


class MarkRead(BaseModel):
    ids: list[uuid.UUID] | None = Field(
        default=None, max_length=100, description="These notifications."
    )
    all: bool = Field(default=False, description="Every unread notification instead.")

    @model_validator(mode="after")
    def _one_target(self) -> "MarkRead":
        if (self.ids is None) == (not self.all):
            raise ValueError("Give either `ids` or `all: true`")
        return self


class UnreadCount(BaseModel):
    unread: int


class NotificationPreferenceRead(BaseModel):
    kind: NotificationKind
    delivery: NotificationDelivery


class NotificationPreferences(BaseModel):
    """Every notification kind that applies to the member's role, with its delivery."""

    items: list[NotificationPreferenceRead]

    @classmethod
    def build(
        cls, prefs: dict[NotificationKind, NotificationDelivery]
    ) -> "NotificationPreferences":
        return cls(items=[NotificationPreferenceRead(kind=k, delivery=d) for k, d in prefs.items()])


class NotificationPreferenceUpdate(BaseModel):
    kind: NotificationKind
    delivery: NotificationDelivery


class AuditEntryRead(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID | None
    actor_name: str | None
    entity_type: str
    entity_id: str | None
    action: str
    summary: str
    changes: dict[str, Any]
    created_at: datetime

    @classmethod
    def build(cls, e: AuditLogEntry) -> "AuditEntryRead":
        return cls(
            id=e.id,
            actor_id=e.actor_id,
            actor_name=e.actor_name,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            action=e.action,
            summary=e.summary,
            changes=e.changes,
            created_at=e.created_at,
        )
