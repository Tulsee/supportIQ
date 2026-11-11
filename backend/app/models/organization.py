import enum
import uuid

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User


class OrgRole(enum.StrEnum):
    ADMIN = "admin"  # manage organization, members, documents, settings
    AGENT = "agent"  # handle tickets and customer conversations
    CUSTOMER = "customer"  # create tickets, view own conversations


def org_role_type() -> Enum:
    """Column type for OrgRole: VARCHAR + CHECK rather than a native PG enum, so
    adding a role later is a plain migration instead of ALTER TYPE."""
    return Enum(
        OrgRole,
        name="org_role",
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda roles: [role.value for role in roles],
        validate_strings=True,
    )


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)


class OrganizationMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[OrgRole] = mapped_column(org_role_type())

    # lazy="raise": async code must load relationships explicitly (joinedload /
    # selectinload); an accidental lazy load fails loudly instead of hanging.
    organization: Mapped[Organization] = relationship(lazy="raise")
    user: Mapped[User] = relationship(lazy="raise")
