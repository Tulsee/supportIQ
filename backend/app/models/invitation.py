import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.organization import Organization, OrgRole, org_role_type
from app.models.user import User


class OrganizationInvitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An emailed invitation to join an organization with a given role.

    Only the SHA-256 hash of the token is stored; the raw token exists only in the
    email. An invitation is *open* until it is accepted or revoked; past
    ``expires_at`` it can no longer be accepted.
    """

    __tablename__ = "organization_invitations"
    __table_args__ = (
        # At most one open invitation per email per organization. Re-inviting
        # revokes the previous one first, so only the newest link works.
        Index(
            "uq_organization_invitations_open",
            "organization_id",
            "email",
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    email: Mapped[str] = mapped_column(String(320))  # lower-cased
    role: Mapped[OrgRole] = mapped_column(org_role_type())
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[Organization] = relationship(lazy="raise")
    invited_by: Mapped[User | None] = relationship(lazy="raise")
