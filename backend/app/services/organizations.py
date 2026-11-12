"""Organization lifecycle and membership management."""

import re
import secrets
import unicodedata
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import rls_bypass, set_rls_context
from app.core.exceptions import ConflictError, NotFoundError
from app.models import Organization, OrganizationMember, OrgRole, User
from app.repositories.organizations import OrganizationRepository
from app.schemas.organizations import MemberUpdate, OrganizationCreate, OrganizationUpdate
from app.services import audit
from app.services.authorization import STAFF_ROLES, require_role
from app.services.tickets import TicketService

SLUG_TAKEN = "Organization slug is already taken"
LAST_ADMIN = "An organization must keep at least one admin"


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug[:40].rstrip("-") or "org"


class OrganizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orgs = OrganizationRepository(session)

    # --- Organizations ----------------------------------------------------------

    async def create(self, owner: User, data: OrganizationCreate) -> OrganizationMember:
        """Create an organization with ``owner`` as its first admin."""
        if data.slug is not None:
            if await self._slug_taken(data.slug):
                raise ConflictError(SLUG_TAKEN)
            slug = data.slug
        else:
            slug = await self._generate_unique_slug(data.name)

        organization = Organization(id=uuid.uuid4(), name=data.name, slug=slug)
        # Act as the new organization: RLS only lets a request write (and read back
        # via RETURNING) rows of its current organization.
        await set_rls_context(self.session, organization_id=organization.id)
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
        if data.name != organization.name:
            await audit.record(
                self.session,
                organization_id=organization.id,
                actor_id=actor.user_id,
                entity_type="organization",
                entity_id=organization.id,
                action="organization.renamed",
                summary=f"Renamed the organization to {data.name}",
                changes={"name": {"from": organization.name, "to": data.name}},
            )
        organization.name = data.name
        await self.session.commit()
        return organization

    # --- Members ----------------------------------------------------------------

    async def list_members(
        self, actor: OrganizationMember, *, limit: int, offset: int
    ) -> tuple[list[OrganizationMember], int]:
        require_role(actor, *STAFF_ROLES)
        return await self.orgs.list_members(actor.organization_id, limit=limit, offset=offset)

    async def update_member(
        self, actor: OrganizationMember, member_id: uuid.UUID, data: MemberUpdate
    ) -> OrganizationMember:
        require_role(actor, OrgRole.ADMIN)
        member = await self._get_member(actor, member_id)
        if member.role == OrgRole.ADMIN and data.role != OrgRole.ADMIN:
            await self._ensure_not_last_admin(actor.organization_id)
        if member.role in STAFF_ROLES and data.role not in STAFF_ROLES:
            # Only staff can own tickets.
            await TicketService(self.session).unassign_member(actor, member.user_id)
        if member.role != data.role:
            await audit.record(
                self.session,
                organization_id=actor.organization_id,
                actor_id=actor.user_id,
                entity_type="member",
                entity_id=member.user_id,
                action="member.role_changed",
                summary=f"Changed {member.user.full_name}'s role from {member.role} to {data.role}",
                changes={"role": {"from": member.role.value, "to": data.role.value}},
            )
        member.role = data.role
        await self.session.commit()
        return member

    async def remove_member(self, actor: OrganizationMember, member_id: uuid.UUID) -> None:
        require_role(actor, OrgRole.ADMIN)
        member = await self._get_member(actor, member_id)
        if member.role == OrgRole.ADMIN:
            await self._ensure_not_last_admin(actor.organization_id)
        # Before the membership goes: the actor may be removing themselves, and
        # only staff may update tickets.
        await TicketService(self.session).unassign_member(actor, member.user_id)
        await audit.record(
            self.session,
            organization_id=actor.organization_id,
            actor_id=actor.user_id,
            entity_type="member",
            entity_id=member.user_id,
            action="member.removed",
            summary=f"Removed {member.user.full_name} ({member.role})",
            changes={"role": {"from": member.role.value, "to": None}},
        )
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

    async def _slug_taken(self, slug: str) -> bool:
        # Slugs are unique across all tenants, so this check must see every
        # organization. It reveals only whether a slug exists, which a failed
        # insert would reveal anyway.
        async with rls_bypass(self.session):
            return await self.orgs.slug_exists(slug)

    async def _generate_unique_slug(self, name: str) -> str:
        base = slugify(name)
        candidate = base
        for _ in range(5):
            if len(candidate) >= 3 and not await self._slug_taken(candidate):
                return candidate
            candidate = f"{base}-{secrets.token_hex(3)}"
        raise ConflictError("Could not generate a unique slug; please provide one")

    async def _commit_or_conflict(self, message: str) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:  # lost a race on a unique constraint
            await self.session.rollback()
            raise ConflictError(message) from exc
