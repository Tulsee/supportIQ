"""Declarative base and shared column mixins for all ORM models."""

import enum
import uuid
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, Enum, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names so Alembic autogenerate produces stable,
# reviewable migrations (and constraints can be dropped by name later).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Fetch server-generated values (created_at, updated_at) with RETURNING on
    # INSERT *and* UPDATE. Otherwise they are expired after flush and reading them
    # triggers an implicit lazy load, which raises under asyncio.
    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}


def str_enum_type(enum_class: type[enum.StrEnum], name: str, length: int = 32) -> Enum:
    """Column type for a StrEnum: VARCHAR + CHECK rather than a native PG enum, so
    adding a value later is a plain migration instead of ALTER TYPE."""
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=length,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
