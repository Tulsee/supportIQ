"""Platform administration. Super admins only."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, Pagination, SuperAdmin, require_superadmin
from app.schemas.admin import AdminOrganizationRead, AdminUserUpdate
from app.schemas.common import Page, error_responses
from app.schemas.users import UserRead
from app.services.platform_admin import PlatformAdminService

router = APIRouter(
    prefix="/admin",
    tags=["platform admin"],
    dependencies=[Depends(require_superadmin)],
    responses=error_responses(401, 403, 404, 422),
)

Search = Annotated[str | None, Query(max_length=100, description="Case-insensitive substring.")]


@router.get("/users", summary="List all users")
async def list_users(session: DbSession, page: Pagination, search: Search = None) -> Page[UserRead]:
    users, total = await PlatformAdminService(session).list_users(
        search=search, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[UserRead.model_validate(u) for u in users],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.patch("/users/{user_id}", summary="Activate/deactivate a user or change super admin status")
async def update_user(
    user_id: uuid.UUID, body: AdminUserUpdate, admin: SuperAdmin, session: DbSession
) -> UserRead:
    user = await PlatformAdminService(session).update_user(admin, user_id, body)
    return UserRead.model_validate(user)


@router.get("/organizations", summary="List all organizations")
async def list_organizations(
    session: DbSession, page: Pagination, search: Search = None
) -> Page[AdminOrganizationRead]:
    rows, total = await PlatformAdminService(session).list_organizations(
        search=search, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[AdminOrganizationRead.build(org, count) for org, count in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
