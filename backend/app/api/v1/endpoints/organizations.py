import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession, OrgMembership, Pagination
from app.schemas.common import Page, error_responses
from app.schemas.organizations import (
    MemberRead,
    MemberUpdate,
    OrganizationCreate,
    OrganizationUpdate,
    OrganizationWithRole,
)
from app.services.organizations import OrganizationService

router = APIRouter(
    prefix="/organizations",
    tags=["organizations"],
    responses=error_responses(401, 403, 404, 409, 422),
)


@router.get("", summary="List organizations the current user belongs to")
async def list_my_organizations(
    user: CurrentUser, session: DbSession, page: Pagination
) -> Page[OrganizationWithRole]:
    rows, total = await OrganizationService(session).list_for_user(
        user, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[OrganizationWithRole.build(org, role) for org, role in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create an organization")
async def create_organization(
    body: OrganizationCreate, user: CurrentUser, session: DbSession
) -> OrganizationWithRole:
    """The creator becomes the organization's first admin."""
    membership = await OrganizationService(session).create(user, body)
    return OrganizationWithRole.build(membership.organization, membership.role)


@router.get("/{org_id}", summary="Get an organization")
async def get_organization(membership: OrgMembership) -> OrganizationWithRole:
    return OrganizationWithRole.build(membership.organization, membership.role)


@router.patch("/{org_id}", summary="Update an organization (admin)")
async def update_organization(
    body: OrganizationUpdate, membership: OrgMembership, session: DbSession
) -> OrganizationWithRole:
    organization = await OrganizationService(session).update(membership, body)
    return OrganizationWithRole.build(organization, membership.role)


@router.get("/{org_id}/members", summary="List members (admin, agent)")
async def list_members(
    membership: OrgMembership, session: DbSession, page: Pagination
) -> Page[MemberRead]:
    members, total = await OrganizationService(session).list_members(
        membership, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[MemberRead.build(m) for m in members],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.patch("/{org_id}/members/{member_id}", summary="Change a member's role (admin)")
async def update_member(
    member_id: uuid.UUID, body: MemberUpdate, membership: OrgMembership, session: DbSession
) -> MemberRead:
    member = await OrganizationService(session).update_member(membership, member_id, body)
    return MemberRead.build(member)


@router.delete(
    "/{org_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member (admin)",
)
async def remove_member(
    member_id: uuid.UUID, membership: OrgMembership, session: DbSession
) -> None:
    await OrganizationService(session).remove_member(membership, member_id)
