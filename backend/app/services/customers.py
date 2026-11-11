"""Customer records (staff-managed) and their link to customer accounts."""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import rls_bypass
from app.core.exceptions import ConflictError, NotFoundError
from app.models import Customer, OrganizationMember, OrgRole, User
from app.repositories.customers import CustomerRepository
from app.schemas.customers import CustomerCreate, CustomerUpdate
from app.services.authorization import STAFF_ROLES, require_role

EMAIL_TAKEN = "A customer with this email already exists"


class CustomerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.customers = CustomerRepository(session)

    async def list_customers(
        self, actor: OrganizationMember, *, search: str | None, limit: int, offset: int
    ) -> tuple[list[tuple[Customer, int]], int]:
        require_role(actor, *STAFF_ROLES)
        return await self.customers.find(
            actor.organization_id, search=search, limit=limit, offset=offset
        )

    async def get(self, actor: OrganizationMember, customer_id: uuid.UUID) -> Customer:
        require_role(actor, *STAFF_ROLES)
        customer = await self.customers.get(actor.organization_id, customer_id)
        if customer is None:
            raise NotFoundError("Customer not found")
        return customer

    async def create(self, actor: OrganizationMember, data: CustomerCreate) -> Customer:
        require_role(actor, *STAFF_ROLES)
        customer = Customer(
            organization_id=actor.organization_id,
            name=data.name,
            email=data.email,
            metadata_=data.metadata,
        )
        self.customers.add(customer)
        await self._commit_or_conflict()
        return customer

    async def update(
        self, actor: OrganizationMember, customer_id: uuid.UUID, data: CustomerUpdate
    ) -> Customer:
        customer = await self.get(actor, customer_id)
        changes = data.model_dump(exclude_unset=True)
        if "name" in changes and data.name is not None:
            customer.name = data.name
        if "email" in changes:
            customer.email = data.email
        if "metadata" in changes and data.metadata is not None:
            customer.metadata_ = data.metadata
        await self._commit_or_conflict()
        return customer

    async def delete(self, actor: OrganizationMember, customer_id: uuid.UUID) -> None:
        require_role(actor, OrgRole.ADMIN)
        customer = await self.get(actor, customer_id)
        if await self.customers.has_tickets(customer.id):
            raise ConflictError("This customer has tickets and can't be deleted")
        await self.customers.delete(customer)
        await self.session.commit()

    async def ensure_for_member(self, member: OrganizationMember, user: User) -> Customer:
        """The customer record of a customer-role member, created on first use.

        If staff already created a record with the member's email, the account is
        linked to it, so tickets logged for them earlier appear in their portal.
        Does not commit; the caller's transaction does.
        """
        require_role(member, OrgRole.CUSTOMER)
        existing = await self.customers.get_for_user(member.organization_id, user.id)
        if existing is not None:
            return existing
        # Unlinked records are invisible to customers under RLS: linking one is a
        # system operation, scoped to this organization and this exact email.
        async with rls_bypass(self.session):
            unlinked = await self.customers.get_unlinked_by_email(
                member.organization_id, user.email
            )
            if unlinked is not None:
                unlinked.user_id = user.id
                await self.session.flush()
                return unlinked
        try:
            return await self._create_linked(member, user, email=user.email)
        except IntegrityError:
            concurrent = await self.customers.get_for_user(member.organization_id, user.id)
            if concurrent is not None:  # a concurrent request created it first
                return concurrent
            # Staff gave this email to another (linked) record; don't steal it.
            return await self._create_linked(member, user, email=None)

    async def _create_linked(
        self, member: OrganizationMember, user: User, *, email: str | None
    ) -> Customer:
        customer = Customer(
            organization_id=member.organization_id,
            name=user.full_name,
            email=email,
            user_id=user.id,
        )
        async with self.session.begin_nested():  # a failure rolls back only this insert
            self.customers.add(customer)
        return customer

    async def _commit_or_conflict(self) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:  # unique email per organization
            await self.session.rollback()
            raise ConflictError(EMAIL_TAKEN) from exc
