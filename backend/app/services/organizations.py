"""Organization lifecycle and membership management."""

import re
import secrets
import unicodedata
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models import Organization, OrganizationMember, OrgRole, User
from app.repositories.organizations import OrganizationRepository
from app.repositories.users import UserRepository
from app.schemas.organizations import (
    MemberCreate,
    MemberUpdate,
    OrganizationCreate,
    OrganizationUpdate,
)
from app.services.authorization import STAFF_ROLES, require_role

SLUG_TAKEN = "Organization slug is already taken"
ALREADY_MEMBER = "User is already a member of this organization"
LAST_ADMIN = "An organization must keep at least one admin"


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug[:40].rstrip("-") or "org"


class OrganizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orgs = OrganizationRepository(session)
        self.users = UserRepository(session)

    # --- Organizations ----------------------------------------------------------

    async def create(self, owner: User, data: OrganizationCreate) -> OrganizationMember:
        """Create an organization with ``owner`` as its first admin."""
        if data.slug is not None:
            if await self.orgs.slug_exists(data.slug):
                raise ConflictError(SLUG_TAKEN)
            slug = data.slug
        else:
            slug = await self._generate_unique_slug(data.name)

        organization = Organization(name=data.name, slug=slug)
        membership = OrganizationMember(
            organization=organization, user_id=owner.id, role=OrgRole.ADMIN
        )
        self.orgs.add(organization, membership)
        await self._commit_or_conflict(SLUG_TAKEN)
        return membership

    async def list_for_user(
        self, user: User, *, limit: int, offset: int
    ) -> tuple[list[tuple[Organization, OrgRole]], int]:
        return await self.orgs.list_for_user(user.id, limit=limit, offset=offset)

    async def update(self, actor: OrganizationMember, data: OrganizationUpdate) -> Organization:
        require_role(actor, OrgRole.ADMIN)
        organization = actor.organization
        organization.name = data.name
        await self.session.commit()
        return organization

    # --- Members ----------------------------------------------------------------

    async def list_members(
        self, actor: OrganizationMember, *, limit: int, offset: int
    ) -> tuple[list[OrganizationMember], int]:
        require_role(actor, *STAFF_ROLES)
        return await self.orgs.list_members(actor.organization_id, limit=limit, offset=offset)

    async def add_member(self, actor: OrganizationMember, data: MemberCreate) -> OrganizationMember:
        require_role(actor, OrgRole.ADMIN)
        user = await self.users.get_by_email(data.email)
        if user is None or not user.is_active:
            raise NotFoundError("No active user is registered with this email")
        if await self.orgs.get_membership(actor.organization_id, user.id) is not None:
            raise ConflictError(ALREADY_MEMBER)

        member = OrganizationMember(
            organization_id=actor.organization_id, user=user, role=data.role
        )
        self.orgs.add(member)
        await self._commit_or_conflict(ALREADY_MEMBER)
        return member

    async def update_member(
        self, actor: OrganizationMember, member_id: uuid.UUID, data: MemberUpdate
    ) -> OrganizationMember:
        require_role(actor, OrgRole.ADMIN)
        member = await self._get_member(actor, member_id)
        if member.role == OrgRole.ADMIN and data.role != OrgRole.ADMIN:
            await self._ensure_not_last_admin(actor.organization_id)
        member.role = data.role
        await self.session.commit()
        return member

    async def remove_member(self, actor: OrganizationMember, member_id: uuid.UUID) -> None:
        require_role(actor, OrgRole.ADMIN)
        member = await self._get_member(actor, member_id)
        if member.role == OrgRole.ADMIN:
            await self._ensure_not_last_admin(actor.organization_id)
        await self.orgs.delete(member)
        await self.session.commit()

    # --- Helpers ----------------------------------------------------------------

    async def _get_member(
        self, actor: OrganizationMember, member_id: uuid.UUID
    ) -> OrganizationMember:
        member = await self.orgs.get_member(actor.organization_id, member_id)
        if member is None:
            raise NotFoundError("Member not found")
        return member

    async def _ensure_not_last_admin(self, organization_id: uuid.UUID) -> None:
        if len(await self.orgs.lock_admin_ids(organization_id)) <= 1:
            raise ConflictError(LAST_ADMIN)

    async def _generate_unique_slug(self, name: str) -> str:
        base = slugify(name)
        candidate = base
        for _ in range(5):
            if len(candidate) >= 3 and not await self.orgs.slug_exists(candidate):
                return candidate
            candidate = f"{base}-{secrets.token_hex(3)}"
        raise ConflictError("Could not generate a unique slug; please provide one")

    async def _commit_or_conflict(self, message: str) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:  # lost a race on a unique constraint
            await self.session.rollback()
            raise ConflictError(message) from exc
