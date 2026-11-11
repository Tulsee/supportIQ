from sqlalchemy import String, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # Always stored lower-cased (normalized by the API schemas) so uniqueness is
    # effectively case-insensitive.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    # Platform-level administration (users, organizations). Granted only via the
    # CLI or by another super admin. Does NOT grant access to tenant data.
    is_superadmin: Mapped[bool] = mapped_column(default=False, server_default=false())
