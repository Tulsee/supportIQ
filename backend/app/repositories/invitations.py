import uuid
from datetime import datetime

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import OrganizationInvitation


class InvitationRepository:
    """Invitations. Organization-scoped lookups filter on ``organization_id``;
    token lookups span tenants and must run inside ``rls_bypass``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, invitation: OrganizationInvitation) -> None:
        self.session.add(invitation)

    async def get(
        self, organization_id: uuid.UUID, invitation_id: uuid.UUID
    ) -> OrganizationInvitation | None:
        stmt = select(OrganizationInvitation).where(
            OrganizationInvitation.id == invitation_id,
            OrganizationInvitation.organization_id == organization_id,
        )
        return await self.session.scalar(stmt)

    async def get_by_token_hash(
        self, token_hash: str, *, for_update: bool = False
    ) -> OrganizationInvitation | None:
        stmt = (
            select(OrganizationInvitation)
            .options(
                joinedload(OrganizationInvitation.organization),
                joinedload(OrganizationInvitation.invited_by),
            )
            .where(OrganizationInvitation.token_hash == token_hash)
        )
        if for_update:
            stmt = stmt.with_for_update(of=OrganizationInvitation)
        return await self.session.scalar(stmt)

    async def list_open(
        self, organization_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[OrganizationInvitation], int]:
        condition = (
            (OrganizationInvitation.organization_id == organization_id)
            & OrganizationInvitation.accepted_at.is_(None)
            & OrganizationInvitation.revoked_at.is_(None)
        )
        stmt = (
            select(OrganizationInvitation)
            .options(joinedload(OrganizationInvitation.invited_by))
            .where(condition)
            .order_by(OrganizationInvitation.created_at.desc(), OrganizationInvitation.id)
            .limit(limit)
            .offset(offset)
        )
        invitations = list((await self.session.scalars(stmt)).all())
        total = await self.session.scalar(
            select(func.count()).select_from(OrganizationInvitation).where(condition)
        )
        return invitations, total or 0

    async def revoke_open_for_email(
        self, organization_id: uuid.UUID, email: str, revoked_at: datetime
    ) -> None:
        await self.session.execute(
            update(OrganizationInvitation)
            .where(
                OrganizationInvitation.organization_id == organization_id,
                OrganizationInvitation.email == email.lower(),
                OrganizationInvitation.accepted_at.is_(None),
                OrganizationInvitation.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )

    async def delete_stale(self, before: datetime) -> int:
        """Delete never-accepted invitations that expired or were revoked before
        ``before``. Accepted invitations are kept as a record of who invited whom."""
        result = await self.session.execute(
            delete(OrganizationInvitation).where(
                OrganizationInvitation.accepted_at.is_(None),
                or_(
                    OrganizationInvitation.expires_at < before,
                    OrganizationInvitation.revoked_at < before,
                ),
            )
        )
        return result.rowcount
