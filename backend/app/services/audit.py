"""Audit log (Module 9): administrative changes, append-only.

Services call ``record`` inside the transaction that makes the change, so the entry
exists exactly when the change does.
"""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import AuditLogEntry
from app.models.organization import OrganizationMember, OrgRole
from app.models.user import User
from app.services.authorization import require_role

MAX_PAGE = 100


async def record(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    entity_type: str,
    entity_id: uuid.UUID | str | None,
    action: str,
    summary: str,
    changes: dict[str, Any] | None = None,
) -> AuditLogEntry:
    """Add an entry to the session (the caller commits). ``changes`` holds a minimal
    before/after, e.g. ``{"role": {"from": "agent", "to": "admin"}}``."""
    # Usually already in the session's identity map (the signed-in user).
    actor = await session.get(User, actor_id) if actor_id else None
    entry = AuditLogEntry(
        organization_id=organization_id,
        actor_id=actor_id,
        actor_name=actor.full_name if actor else None,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        action=action,
        summary=summary[:500],
        changes=changes or {},
    )
    session.add(entry)
    return entry


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_entries(
        self,
        member: OrganizationMember,
        *,
        entity_type: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[AuditLogEntry], int]:
        require_role(member, OrgRole.ADMIN)
        query = select(AuditLogEntry).where(AuditLogEntry.organization_id == member.organization_id)
        if entity_type:
            query = query.where(AuditLogEntry.entity_type == entity_type)
        items = (
            await self.session.scalars(
                query.order_by(AuditLogEntry.seq.desc()).limit(min(limit, MAX_PAGE)).offset(offset)
            )
        ).all()
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        return list(items), int(total or 0)
