"""Async SQLAlchemy engine, session factory, and connectivity check."""

import asyncio
import logging
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

# Creating the engine does not connect; the pool opens connections lazily.
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


async def ping_database() -> bool:
    """Return True if the database answers ``SELECT 1`` within the health timeout."""
    try:
        async with asyncio.timeout(settings.database_health_timeout_seconds):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except Exception as exc:  # any failure means "not ready"; never crash the probe
        logger.warning("Database ping failed: %s: %s", type(exc).__name__, exc)
        return False
    return True
