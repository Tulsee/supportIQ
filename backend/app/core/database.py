"""Async SQLAlchemy engine, sessions, row-level-security context, connectivity check."""

import asyncio
import enum
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace

from sqlalchemy import Connection, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction

from app.core.config import settings

logger = logging.getLogger(__name__)

# Creating the engine does not connect; the pool opens connections lazily.
# DATABASE_URL is the least-privilege app role, so RLS policies apply to it.
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.database_echo,
    pool_pre_ping=True,
)

# expire_on_commit=False: attributes stay loaded after commit, avoiding implicit
# lazy-load I/O, which is not allowed under asyncio.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request.

    Services own transaction boundaries (``await session.commit()``); anything left
    uncommitted is rolled back when the session closes.
    """
    async with SessionLocal() as session:
        yield session


# --- Row-level security context -------------------------------------------------------
#
# RLS policies (see the "row level security" migration) read three settings:
#   app.current_organization_id, app.current_user_id, app.rls_bypass
# The values live in ``session.info`` and are applied with set_config(..., is_local
# => true) at the start of *every* transaction the session begins. Being
# transaction-local, they vanish at COMMIT/ROLLBACK, so they can never leak to
# another request through a pooled connection.

_RLS_INFO_KEY = "rls_context"
_APPLY_RLS_CONTEXT = text(
    "SELECT set_config('app.current_organization_id', :organization_id, true),"
    " set_config('app.current_user_id', :user_id, true),"
    " set_config('app.rls_bypass', :bypass, true)"
)


@dataclass(frozen=True)
class RlsContext:
    organization_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    bypass: bool = False

    def as_params(self) -> dict[str, str]:
        return {
            "organization_id": str(self.organization_id) if self.organization_id else "",
            "user_id": str(self.user_id) if self.user_id else "",
            "bypass": "on" if self.bypass else "off",
        }


def get_rls_context(session: AsyncSession) -> RlsContext:
    return session.sync_session.info.get(_RLS_INFO_KEY, RlsContext())


@event.listens_for(Session, "after_begin")
def _apply_rls_context(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    context = session.info.get(_RLS_INFO_KEY, RlsContext())
    connection.execute(_APPLY_RLS_CONTEXT, context.as_params())


class _Unset(enum.Enum):
    TOKEN = enum.auto()


_UNSET = _Unset.TOKEN


async def set_rls_context(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID | _Unset | None = _UNSET,
    user_id: uuid.UUID | _Unset | None = _UNSET,
    bypass: bool | _Unset = _UNSET,
) -> None:
    """Update the session's RLS context (omitted fields keep their value).

    Applied immediately if a transaction is open, and re-applied automatically at
    the start of every later transaction.
    """
    changes = {
        name: value
        for name, value in (
            ("organization_id", organization_id),
            ("user_id", user_id),
            ("bypass", bypass),
        )
        if value is not _UNSET
    }
    context = replace(get_rls_context(session), **changes)
    session.sync_session.info[_RLS_INFO_KEY] = context
    if session.in_transaction():
        await session.execute(_APPLY_RLS_CONTEXT, context.as_params())


async def reset_rls_context(session: AsyncSession) -> None:
    await set_rls_context(session, organization_id=None, user_id=None, bypass=False)


@asynccontextmanager
async def rls_bypass(session: AsyncSession) -> AsyncIterator[None]:
    """Lift tenant policies for a system operation that legitimately spans tenants
    (token lookups, platform administration, maintenance). Keep blocks small."""
    previous = get_rls_context(session).bypass
    await set_rls_context(session, bypass=True)
    try:
        yield
    finally:
        await set_rls_context(session, bypass=previous)


# --- Health ----------------------------------------------------------------------------


async def ping_database() -> bool:
    """Return True if the database answers ``SELECT 1`` within the health timeout."""
    try:
        async with asyncio.timeout(settings.health_check_timeout_seconds):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except Exception as exc:  # any failure means "not ready"; never crash the probe
        logger.warning("Database ping failed: %s: %s", type(exc).__name__, exc)
        return False
    return True
