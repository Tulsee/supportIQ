import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User


class Customer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Someone an organization supports.

    Staff can create customers who have no account (e.g. a ticket logged from a
    phone call). ``user_id`` links the record to an account once that person joins
    the organization with the customer role: from then on they see this record's
    tickets in the customer portal.
    """

    __tablename__ = "customers"
    __table_args__ = (
        Index("ix_customers_organization_id_name", "organization_id", "name"),
        # Emails are stored lower-cased; unique per organization when present.
        Index(
            "uq_customers_organization_email",
            "organization_id",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
        # An account maps to at most one customer record per organization.
        Index(
            "uq_customers_organization_user",
            "organization_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Free-form attributes (plan, account manager, ...). "metadata" is reserved on
    # declarative classes, hence the attribute name.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb")
    )

    user: Mapped[User | None] = relationship(lazy="raise")
