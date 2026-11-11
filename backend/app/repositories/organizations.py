import uuid

from sqlalchemy import exists, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, joinedload

from app.models import Organization, OrganizationMember, OrgRole, User


class OrganizationRepository:
    """Organizations and memberships.

    Every membership lookup filters on ``organization_id`` as well as its own id,
    so an id that belongs to another tenant can never match.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, *objects: Organization | OrganizationMember) -> None:
        self.session.add_all(objects)

    async def delete(self, obj: Organization | OrganizationMember) -> None:
        await self.session.delete(obj)

    async def get_by_slug(self, slug: str) -> Organization | None:
        return await self.session.scalar(select(Organization).where(Organization.slug == slug))

    async def slug_exists(self, slug: str) -> bool:
        return bool(await self.session.scalar(select(exists().where(Organization.slug == slug))))

    async def list_for_user(
        self, user_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[tuple[Organization, OrgRole]], int]:
        stmt = (
            select(Organization, OrganizationMember.role)
            .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
            .where(OrganizationMember.user_id == user_id)
            .order_by(Organization.name, Organization.id)
            .limit(limit)
            .offset(offset)
        )
        rows = [(org, role) for org, role in (await self.session.execute(stmt)).all()]
        total = await self.session.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(OrganizationMember.user_id == user_id)
        )
        return rows, total or 0

    async def get_membership(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationMember | None:
        """The user's membership in the organization (with the organization loaded)."""
        stmt = (
            select(OrganizationMember)
            .options(joinedload(OrganizationMember.organization))
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user_id,
            )
        )
        return await self.session.scalar(stmt)

    async def get_membership_with_user(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationMember | None:
        stmt = (
            select(OrganizationMember)
            .options(joinedload(OrganizationMember.user))
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user_id,
            )
        )
        return await self.session.scalar(stmt)

    async def get_membership_by_email(
        self, organization_id: uuid.UUID, email: str
    ) -> OrganizationMember | None:
        stmt = (
            select(OrganizationMember)
            .join(OrganizationMember.user)
            .where(
                OrganizationMember.organization_id == organization_id,
                User.email == email.lower(),
            )
        )
        return await self.session.scalar(stmt)

    async def list_all_with_member_counts(
        self, *, search: str | None, limit: int, offset: int
    ) -> tuple[list[tuple[Organization, int]], int]:
        """Every organization on the platform (platform admin; requires RLS bypass)."""
        condition = (
            Organization.name.icontains(search, autoescape=True)
            | Organization.slug.icontains(search, autoescape=True)
            if search
            else true()
        )
        member_count = (
            select(func.count())
            .where(OrganizationMember.organization_id == Organization.id)
            .correlate(Organization)
            .scalar_subquery()
        )
        stmt = (
            select(Organization, member_count)
            .where(condition)
            .order_by(Organization.name, Organization.id)
            .limit(limit)
            .offset(offset)
        )
        rows = [(org, count) for org, count in (await self.session.execute(stmt)).all()]
        total = await self.session.scalar(
            select(func.count()).select_from(Organization).where(condition)
        )
        return rows, total or 0

    async def get_member(
        self, organization_id: uuid.UUID, member_id: uuid.UUID
    ) -> OrganizationMember | None:
        """A membership by id, only if it belongs to this organization (with user loaded)."""
        stmt = (
            select(OrganizationMember)
            .options(joinedload(OrganizationMember.user))
            .where(
                OrganizationMember.id == member_id,
                OrganizationMember.organization_id == organization_id,
            )
        )
        return await self.session.scalar(stmt)

    async def list_members(
        self, organization_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[OrganizationMember], int]:
        stmt = (
            select(OrganizationMember)
            .join(OrganizationMember.user)
            .options(contains_eager(OrganizationMember.user))
            .where(OrganizationMember.organization_id == organization_id)
            .order_by(User.email)
            .limit(limit)
            .offset(offset)
        )
        members = list((await self.session.scalars(stmt)).all())
        total = await self.session.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(OrganizationMember.organization_id == organization_id)
        )
        return members, total or 0

    async def lock_admin_ids(self, organization_id: uuid.UUID) -> list[uuid.UUID]:
        """Lock the organization's admin memberships (SELECT ... FOR UPDATE).

        Concurrent demotions/removals then serialize, so two requests can't both
        pass a "keep at least one admin" check.
        """
        stmt = (
            select(OrganizationMember.id)
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.role == OrgRole.ADMIN,
            )
            .with_for_update()
        )
        return list((await self.session.scalars(stmt)).all())
