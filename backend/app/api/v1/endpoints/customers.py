"""Customers of the organization in ``X-Organization-Id``. Admins and agents only."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, Pagination, TenantMembership
from app.schemas.common import Page, error_responses
from app.schemas.customers import CustomerCreate, CustomerRead, CustomerUpdate
from app.services.customers import CustomerService

router = APIRouter(
    prefix="/customers",
    tags=["customers"],
    responses=error_responses(401, 403, 404, 409, 422),
)

Search = Annotated[
    str | None, Query(max_length=100, description="Case-insensitive match on name or email.")
]


@router.get("", summary="List customers")
async def list_customers(
    member: TenantMembership, session: DbSession, page: Pagination, search: Search = None
) -> Page[CustomerRead]:
    rows, total = await CustomerService(session).list_customers(
        member, search=search, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[CustomerRead.build(customer, count) for customer, count in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a customer")
async def create_customer(
    body: CustomerCreate, member: TenantMembership, session: DbSession
) -> CustomerRead:
    """409 if another customer in the organization has the same email."""
    return CustomerRead.build(await CustomerService(session).create(member, body))


@router.get("/{customer_id}", summary="Get a customer")
async def get_customer(
    customer_id: uuid.UUID, member: TenantMembership, session: DbSession
) -> CustomerRead:
    return CustomerRead.build(await CustomerService(session).get(member, customer_id))


@router.patch("/{customer_id}", summary="Update a customer")
async def update_customer(
    customer_id: uuid.UUID, body: CustomerUpdate, member: TenantMembership, session: DbSession
) -> CustomerRead:
    return CustomerRead.build(await CustomerService(session).update(member, customer_id, body))


@router.delete(
    "/{customer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a customer (admin; only without tickets)",
)
async def delete_customer(
    customer_id: uuid.UUID, member: TenantMembership, session: DbSession
) -> None:
    await CustomerService(session).delete(member, customer_id)
