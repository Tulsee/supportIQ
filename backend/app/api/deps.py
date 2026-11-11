"""Shared FastAPI dependencies, exposed as ``Annotated`` aliases for endpoint signatures.

Request context is resolved in layers:

1. ``CurrentUser``: who is calling (validated access token, active user).
2. Which organization they act in, which also proves membership:
   - ``OrgMembership``: from the ``{org_id}`` path parameter, for
     ``/organizations/{org_id}/...`` routes, where the organization *is* the resource.
   - ``TenantMembership``: from the ``X-Organization-Id`` header, for tenant-scoped
     resources (customers, tickets, documents, ...).
3. What they may do: decided by services via ``require_role(membership, ...)``.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.exceptions import AuthenticationError, NotFoundError
from app.core.security import InvalidTokenError, decode_access_token
from app.models import OrganizationMember, User
from app.repositories.organizations import OrganizationRepository
from app.repositories.users import UserRepository

DbSession = Annotated[AsyncSession, Depends(get_db_session)]

_bearer_scheme = HTTPBearer(
    auto_error=False, description="Access token from `POST /api/v1/auth/login`."
)


async def get_current_user(
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> User:
    if credentials is None:
        raise AuthenticationError("Not authenticated")
    try:
        user_id = decode_access_token(credentials.credentials)
    except InvalidTokenError:
        raise AuthenticationError("Invalid or expired access token") from None
    user = await UserRepository(session).get(user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid or expired access token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def _membership_or_404(
    session: AsyncSession, user: User, organization_id: uuid.UUID
) -> OrganizationMember:
    membership = await OrganizationRepository(session).get_membership(organization_id, user.id)
    if membership is None:
        # 404 rather than 403: don't reveal that an inaccessible organization exists.
        raise NotFoundError("Organization not found")
    return membership


async def get_org_membership(
    org_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> OrganizationMember:
    return await _membership_or_404(session, user, org_id)


async def get_tenant_membership(
    user: CurrentUser,
    session: DbSession,
    organization_id: Annotated[
        uuid.UUID,
        Header(alias="X-Organization-Id", description="The organization to act in."),
    ],
) -> OrganizationMember:
    return await _membership_or_404(session, user, organization_id)


OrgMembership = Annotated[OrganizationMember, Depends(get_org_membership)]
TenantMembership = Annotated[OrganizationMember, Depends(get_tenant_membership)]


@dataclass(frozen=True)
class PageParams:
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum items to return.")] = 20
    offset: Annotated[int, Query(ge=0, description="Number of items to skip.")] = 0


Pagination = Annotated[PageParams, Depends()]
