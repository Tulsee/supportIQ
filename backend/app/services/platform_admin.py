"""Platform administration (super admins).

Super admins manage *platform* objects: user accounts and the list of
organizations. They do not get implicit access to any organization's data;
tenant endpoints still require a membership.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import rls_bypass
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.security import utcnow
from app.models import Organization, User
from app.repositories.organizations import OrganizationRepository
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.schemas.admin import AdminUserUpdate


class PlatformAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.orgs = OrganizationRepository(session)
        self.refresh_tokens = RefreshTokenRepository(session)

    async def list_users(
        self, *, search: str | None, limit: int, offset: int
    ) -> tuple[list[User], int]:
        return await self.users.list_all(search=search, limit=limit, offset=offset)

    async def update_user(self, actor: User, user_id: uuid.UUID, data: AdminUserUpdate) -> User:
        if user_id == actor.id:
            # Prevents locking yourself out or silently dropping your own privileges.
            raise PermissionDeniedError("You cannot change your own platform status")
        user = await self.users.get(user_id)
        if user is None:
            raise NotFoundError("User not found")
        if data.is_superadmin is not None:
            user.is_superadmin = data.is_superadmin
        if data.is_active is not None:
            user.is_active = data.is_active
            if not data.is_active:
                # Access tokens stop working at once (checked per request); also end
                # every refresh-token session so nothing can be renewed.
                await self.refresh_tokens.revoke_all_for_user(user.id, utcnow())
        await self.session.commit()
        return user

    async def set_superadmin(self, email: str, value: bool) -> User:
        """Bootstrap path for the CLI: grant/revoke without an acting admin."""
        user = await self.users.get_by_email(email)
        if user is None:
            raise NotFoundError(f"No user with email {email}")
        user.is_superadmin = value
        await self.session.commit()
        return user

    async def list_organizations(
        self, *, search: str | None, limit: int, offset: int
    ) -> tuple[list[tuple[Organization, int]], int]:
        async with rls_bypass(self.session):  # platform-wide listing spans all tenants
            return await self.orgs.list_all_with_member_counts(
                search=search, limit=limit, offset=offset
            )
