"""Ticket categories: admins manage them, staff use them on tickets."""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models import OrganizationMember, OrgRole, TicketCategory
from app.schemas.categories import CategoryCreate, CategoryUpdate
from app.services import audit
from app.services.authorization import STAFF_ROLES, require_role


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:50].strip("-") or "category"


class CategoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_categories(
        self, member: OrganizationMember, *, include_inactive: bool
    ) -> list[TicketCategory]:
        require_role(member, *STAFF_ROLES)
        query = select(TicketCategory).where(
            TicketCategory.organization_id == member.organization_id
        )
        if not include_inactive:
            query = query.where(TicketCategory.is_active.is_(True))
        return list((await self.session.scalars(query.order_by(TicketCategory.name))).all())

    async def create(self, member: OrganizationMember, data: CategoryCreate) -> TicketCategory:
        require_role(member, OrgRole.ADMIN)
        category = TicketCategory(
            id=uuid.uuid4(),
            organization_id=member.organization_id,
            key=data.key or slugify(data.name),
            name=data.name,
            description=data.description,
            is_active=True,
        )
        self.session.add(category)
        await audit.record(
            self.session,
            organization_id=member.organization_id,
            actor_id=member.user_id,
            entity_type="category",
            entity_id=category.id,
            action="category.created",
            summary=f"Added the ticket category {data.name} ({category.key})",
        )
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                f"A category with the key {category.key!r} already exists",
                code="category_exists",
            ) from exc
        return category

    async def update(
        self, member: OrganizationMember, category_id: uuid.UUID, data: CategoryUpdate
    ) -> TicketCategory:
        require_role(member, OrgRole.ADMIN)
        category = await self.session.get(TicketCategory, category_id)
        if category is None or category.organization_id != member.organization_id:
            raise NotFoundError("Category not found")
        changes = {
            field: {"from": getattr(category, field), "to": value}
            for field, value in data.model_dump(exclude_none=True).items()
            if getattr(category, field) != value
        }
        if not changes:
            return category
        for field, change in changes.items():
            setattr(category, field, change["to"])
        if set(changes) == {"is_active"}:
            verb = "Reactivated" if category.is_active else "Deactivated"
            summary = f"{verb} the ticket category {category.name}"
        else:
            summary = f"Updated the ticket category {category.name}"
        await audit.record(
            self.session,
            organization_id=member.organization_id,
            actor_id=member.user_id,
            entity_type="category",
            entity_id=category.id,
            action="category.updated",
            summary=summary,
            changes=changes,
        )
        await self.session.commit()
        return category
