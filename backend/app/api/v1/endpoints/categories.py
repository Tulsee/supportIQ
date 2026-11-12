"""Ticket categories of the organization in ``X-Organization-Id``: staff read them,
admins manage them."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, TenantMembership
from app.schemas.categories import CategoryCreate, CategoryRead, CategoryUpdate
from app.schemas.common import error_responses
from app.services.categories import CategoryService

router = APIRouter(
    prefix="/categories",
    tags=["categories"],
    responses=error_responses(401, 403, 404, 409, 422),
)


@router.get("", summary="List ticket categories")
async def list_categories(
    member: TenantMembership,
    session: DbSession,
    include_inactive: Annotated[bool, Query(description="Also deactivated ones.")] = False,
) -> list[CategoryRead]:
    categories = await CategoryService(session).list_categories(
        member, include_inactive=include_inactive
    )
    return [CategoryRead.build(c) for c in categories]


@router.post("", status_code=status.HTTP_201_CREATED, summary="Add a ticket category (admin)")
async def create_category(
    body: CategoryCreate, member: TenantMembership, session: DbSession
) -> CategoryRead:
    """409 `category_exists` if the key is taken in this organization."""
    return CategoryRead.build(await CategoryService(session).create(member, body))


@router.patch("/{category_id}", summary="Rename, describe or (de)activate a category (admin)")
async def update_category(
    category_id: uuid.UUID, body: CategoryUpdate, member: TenantMembership, session: DbSession
) -> CategoryRead:
    return CategoryRead.build(await CategoryService(session).update(member, category_id, body))
