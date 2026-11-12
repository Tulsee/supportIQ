import enum
import uuid
from typing import Any

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum_type
from app.models.user import User


class OrgRole(enum.StrEnum):
    ADMIN = "admin"  # manage organization, members, documents, settings
    AGENT = "agent"  # handle tickets and customer conversations
    CUSTOMER = "customer"  # create tickets, view own conversations


def org_role_type() -> Enum:
    return str_enum_type(OrgRole, "org_role")


# Resolved tickets with no reply for this many days are closed (0 = never).
DEFAULT_AUTO_CLOSE_DAYS = 7


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    # Organization-level options (see OrganizationSettings): absent keys mean the default.
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


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
