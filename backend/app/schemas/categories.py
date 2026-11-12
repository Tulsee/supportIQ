"""Ticket categories (Module 10, phase 1)."""

import uuid
from typing import Annotated, Self

from pydantic import BaseModel, Field, StringConstraints

from app.models import TicketCategory

CategoryName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)
]
CategoryKey = Annotated[
    str,
    StringConstraints(min_length=2, max_length=50, pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$"),
]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


class CategoryCreate(BaseModel):
    name: CategoryName
    key: CategoryKey | None = Field(
        default=None,
        description="Stable identifier used by routing rules; generated from the name when "
        "omitted. Can't be changed later.",
    )
    description: Description = ""


class CategoryUpdate(BaseModel):
    """Only the fields provided are changed. Deactivate instead of deleting: tickets
    keep their category."""

    name: CategoryName | None = None
    description: Description | None = None
    is_active: bool | None = None


class CategoryRead(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str
    is_active: bool

    @classmethod
    def build(cls, category: TicketCategory) -> Self:
        return cls(
            id=category.id,
            key=category.key,
            name=category.name,
            description=category.description,
            is_active=category.is_active,
        )
