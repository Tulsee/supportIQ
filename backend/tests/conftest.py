import os

# Must run before the app (and its settings) are imported. Test databases are
# forced even if DATABASE_URL is set in the shell or .env, because the suite drops
# and recreates the schema. Override with TEST_DATABASE_URL / TEST_MIGRATION_DATABASE_URL.
os.environ["ENVIRONMENT"] = "test"
# Tests connect as the least-privilege app role, so row-level security is enforced.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://supportiq_app:supportiq_app@127.0.0.1:5433/supportiq_test",
)
# Migrations (and the schema reset) run as the owner role.
os.environ["MIGRATION_DATABASE_URL"] = os.environ.get(
    "TEST_MIGRATION_DATABASE_URL",
    "postgresql+asyncpg://supportiq:supportiq@127.0.0.1:5433/supportiq_test",
)

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_rate_limiter
from app.core.config import BACKEND_DIR, settings
from app.core.database import get_db_session, reset_rls_context
from app.core.email import get_email_sender
from app.core.rate_limit import InMemoryRateLimiter
from app.main import app
from tests.factories import Outbox

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx2.AsyncClient]]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Sync client for tests that don't need the database."""
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- Database-backed tests ----------------------------------------------------------


async def _recreate_public_schema(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Rebuild the test schema once per run by applying every Alembic migration.
    Returns the app-role URL that tests connect with."""
    for url in (settings.database_url, settings.migration_database_url):
        database = make_url(url).database or ""
        if not database.endswith("_test"):
            pytest.exit(f"Refusing to use non-test database {database!r}", returncode=1)
    try:
        asyncio.run(_recreate_public_schema(settings.migration_database_url))
    except (OSError, SQLAlchemyError) as exc:
        pytest.fail(
            f"Test database unreachable ({type(exc).__name__}). "
            "Start it with: docker compose up -d postgres",
            pytrace=False,
        )
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
    return settings.database_url


@pytest.fixture
async def db_session(migrated_database: str) -> AsyncIterator[AsyncSession]:
    """An app-role session whose work is rolled back after each test.

    Everything runs inside one outer transaction; the services' ``commit()`` calls
    only release SAVEPOINTs. Constraints and RLS behave for real, but no data persists.
    """
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def session_factory(migrated_database: str) -> AsyncIterator[async_sessionmaker]:
    """Real, independently committing app-role sessions (like production), for tests
    that need behavior across separate connections or real transaction boundaries."""
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def rate_limiter() -> InMemoryRateLimiter:
    """A fresh limiter per test, so limits never carry over between tests."""
    return InMemoryRateLimiter()


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


@pytest.fixture
def make_client(
    db_session: AsyncSession, rate_limiter: InMemoryRateLimiter, outbox: Outbox
) -> ClientFactory:
    """Factory for HTTP clients against ``target_app`` (default: the real app), all
    sharing the test's rolled-back session, in-memory rate limiter, and outbox."""

    @asynccontextmanager
    async def _make(target_app: FastAPI = app) -> AsyncIterator[httpx2.AsyncClient]:
        async def _session_override() -> AsyncIterator[AsyncSession]:
            # In production every request gets a fresh session; here requests share
            # one, so clear the previous request's RLS context first.
            await reset_rls_context(db_session)
            yield db_session

        target_app.dependency_overrides[get_db_session] = _session_override
        target_app.dependency_overrides[get_rate_limiter] = lambda: rate_limiter
        target_app.dependency_overrides[get_email_sender] = lambda: outbox
        try:
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=target_app), base_url="http://testserver"
            ) as http_client:
                yield http_client
        finally:
            for dependency in (get_db_session, get_rate_limiter, get_email_sender):
                target_app.dependency_overrides.pop(dependency, None)

    return _make


@pytest.fixture
async def api(make_client: ClientFactory) -> AsyncIterator[httpx2.AsyncClient]:
    async with make_client() as http_client:
        yield http_client
