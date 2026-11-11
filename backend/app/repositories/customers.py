import uuid

from sqlalchemy import exists, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Customer, Ticket


class CustomerRepository:
    """Customers. Every lookup filters on ``organization_id`` as well as its own id."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, customer: Customer) -> None:
        self.session.add(customer)

    async def delete(self, customer: Customer) -> None:
        await self.session.delete(customer)

    async def get(self, organization_id: uuid.UUID, customer_id: uuid.UUID) -> Customer | None:
        return await self.session.scalar(
            select(Customer).where(
                Customer.id == customer_id, Customer.organization_id == organization_id
            )
        )

    async def get_for_user(self, organization_id: uuid.UUID, user_id: uuid.UUID) -> Customer | None:
        return await self.session.scalar(
            select(Customer).where(
                Customer.organization_id == organization_id, Customer.user_id == user_id
            )
        )

    async def get_unlinked_by_email(
        self, organization_id: uuid.UUID, email: str
    ) -> Customer | None:
        return await self.session.scalar(
            select(Customer)
            .where(
                Customer.organization_id == organization_id,
                Customer.email == email.lower(),
                Customer.user_id.is_(None),
            )
            .with_for_update()
        )

    async def find(
        self, organization_id: uuid.UUID, *, search: str | None, limit: int, offset: int
    ) -> tuple[list[tuple[Customer, int]], int]:
        condition = (
            or_(
                Customer.name.icontains(search, autoescape=True),
                Customer.email.icontains(search, autoescape=True),
            )
            if search
            else true()
        )
        ticket_count = (
            select(func.count())
            .where(Ticket.customer_id == Customer.id)
            .correlate(Customer)
            .scalar_subquery()
        )
        stmt = (
            select(Customer, ticket_count)
            .where(Customer.organization_id == organization_id, condition)
            .order_by(Customer.name, Customer.id)
            .limit(limit)
            .offset(offset)
        )
        rows = [(customer, count) for customer, count in (await self.session.execute(stmt)).all()]
        total = await self.session.scalar(
            select(func.count())
            .select_from(Customer)
            .where(Customer.organization_id == organization_id, condition)
        )
        return rows, total or 0

    async def has_tickets(self, customer_id: uuid.UUID) -> bool:
        return bool(
            await self.session.scalar(select(exists().where(Ticket.customer_id == customer_id)))
        )
