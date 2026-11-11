import uuid
from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import BaseModel, Field, StringConstraints

from app.models import Customer
from app.schemas.users import NormalizedEmail

CustomerName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
Metadata = Annotated[
    dict[str, Any],
    Field(description="Free-form attributes, e.g. plan or account manager.", max_length=50),
]


class CustomerCreate(BaseModel):
    name: CustomerName
    email: NormalizedEmail | None = None
    metadata: Metadata = Field(default_factory=dict)


class CustomerUpdate(BaseModel):
    """Only the fields provided are changed; send ``email: null`` to clear it."""

    name: CustomerName | None = None
    email: NormalizedEmail | None = None
    metadata: Metadata | None = None


class CustomerSummary(BaseModel):
    id: uuid.UUID
    name: str
    email: str | None

    @classmethod
    def build(cls, customer: Customer) -> Self:
        return cls(id=customer.id, name=customer.name, email=customer.email)


class CustomerRead(CustomerSummary):
    has_account: bool = Field(
        description="Linked to a user account (the customer can sign in to the portal)."
    )
    metadata: dict[str, Any]
    ticket_count: int | None = Field(
        default=None, description="Number of tickets (included in lists)."
    )
    created_at: datetime
    updated_at: datetime

    @classmethod
    def build(cls, customer: Customer, ticket_count: int | None = None) -> Self:
        return cls(
            id=customer.id,
            name=customer.name,
            email=customer.email,
            has_account=customer.user_id is not None,
            metadata=customer.metadata_,
            ticket_count=ticket_count,
            created_at=customer.created_at,
            updated_at=customer.updated_at,
        )
