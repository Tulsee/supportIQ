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
# Background jobs never reach a real broker from tests (they run in-process).
os.environ["CELERY_BROKER_URL"] = "memory://"

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient
from sqlalchemy import make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai_engine.embeddings.embedder import HashingEmbedder
from ai_engine.evidence.nli import OverlapNli
from ai_engine.reranking.cross_encoder import OverlapReranker
from ai_engine.retrieval.vector_store import QdrantVectorStore
from app.api.deps import (
    get_embedder_dep,
    get_event_broker,
    get_ingestion_queue,
    get_llm_client_dep,
    get_nli_model_dep,
    get_notification_mailer,
    get_rate_limiter,
    get_reranker_dep,
    get_vector_store,
    get_worker_registry,
)
from app.core.config import BACKEND_DIR, settings
from app.core.database import get_db_session, reset_rls_context
from app.core.email import get_email_sender
from app.core.rate_limit import InMemoryRateLimiter
from app.core.storage import LocalObjectStorage, get_object_storage
from app.main import app
from app.services.ingestion import IngestionService, Outcome
from app.websocket.broker import InMemoryEventBroker
from tests.factories import (
    TEST_INGESTION_LIMITS,
    Outbox,
    RecordingMailer,
    RecordingQueue,
    ScriptedLLM,
    StaticWorkerRegistry,
    Workspace,
    add_member,
    create_org,
    create_user,
)

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
def broker() -> InMemoryEventBroker:
    """Real-time events stay in the test process (no Redis)."""
    return InMemoryEventBroker()


# --- Knowledge base: in-process stand-ins for storage, embeddings, Qdrant, the queue --


@pytest.fixture
def storage(tmp_path: Path) -> LocalObjectStorage:
    return LocalObjectStorage(tmp_path / "objects")


@pytest.fixture
def embedder() -> HashingEmbedder:
    return HashingEmbedder(dimensions=64)


@pytest.fixture
async def vector_store(embedder: HashingEmbedder) -> AsyncIterator[QdrantVectorStore]:
    """Qdrant's own client in local (in-memory) mode: real API, no server."""
    client = AsyncQdrantClient(location=":memory:")
    yield QdrantVectorStore(client, "test_chunks", embedder.dimensions)
    await client.close()


@pytest.fixture
def ingestion_queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
def mailer() -> RecordingMailer:
    return RecordingMailer()


@pytest.fixture
def worker_registry() -> StaticWorkerRegistry:
    return StaticWorkerRegistry(count=1)


@pytest.fixture
def reranker() -> OverlapReranker:
    """Model-free cross-encoder stand-in (word overlap)."""
    return OverlapReranker()


@pytest.fixture
def nli() -> OverlapNli:
    """Model-free NLI stand-in (word overlap; unknown numbers contradict)."""
    return OverlapNli()


@pytest.fixture
def llm() -> ScriptedLLM:
    """Language-model stand-in: scripted replies, recorded prompts."""
    return ScriptedLLM()


@pytest.fixture
def ingest(
    db_session: AsyncSession,
    storage: LocalObjectStorage,
    embedder: HashingEmbedder,
    vector_store: QdrantVectorStore,
    ingestion_queue: RecordingQueue,
) -> Callable[..., Awaitable[Outcome]]:
    """Run the queued ingestion job for a document, as a worker would."""

    async def _ingest(
        document_id: str, *, ingestion_id: uuid.UUID | None = None, final_attempt: bool = True
    ) -> Outcome:
        if ingestion_id is None:
            ingestion_id = next(
                job for doc, job in reversed(ingestion_queue.jobs) if str(doc) == document_id
            )
        service = IngestionService(
            db_session, storage, embedder, vector_store, TEST_INGESTION_LIMITS
        )
        return await service.run(uuid.UUID(document_id), ingestion_id, final_attempt=final_attempt)

    return _ingest


@pytest.fixture
def make_client(
    db_session: AsyncSession,
    rate_limiter: InMemoryRateLimiter,
    outbox: Outbox,
    broker: InMemoryEventBroker,
    storage: LocalObjectStorage,
    embedder: HashingEmbedder,
    vector_store: QdrantVectorStore,
    ingestion_queue: RecordingQueue,
    mailer: RecordingMailer,
    worker_registry: StaticWorkerRegistry,
    reranker: OverlapReranker,
    llm: ScriptedLLM,
    nli: OverlapNli,
) -> ClientFactory:
    """Factory for HTTP clients against ``target_app`` (default: the real app), all
    sharing the test's rolled-back session and in-process stand-ins (rate limiter,
    outbox, event broker, storage, embedder, vector store, job queue, language
    model). The
    overrides also apply to WebSocket connections (tests/ws.py)."""

    @asynccontextmanager
    async def _make(target_app: FastAPI = app) -> AsyncIterator[httpx2.AsyncClient]:
        async def _session_override() -> AsyncIterator[AsyncSession]:
            # In production every request gets a fresh session; here requests share
            # one, so clear the previous request's RLS context first.
            await reset_rls_context(db_session)
            yield db_session

        overrides = {
            get_db_session: _session_override,
            get_rate_limiter: lambda: rate_limiter,
            get_email_sender: lambda: outbox,
            get_event_broker: lambda: broker,
            get_object_storage: lambda: storage,
            get_embedder_dep: lambda: embedder,
            get_vector_store: lambda: vector_store,
            get_ingestion_queue: lambda: ingestion_queue,
            get_notification_mailer: lambda: mailer,
            get_worker_registry: lambda: worker_registry,
            get_reranker_dep: lambda: reranker,
            get_llm_client_dep: lambda: llm,
            get_nli_model_dep: lambda: nli,
        }
        target_app.dependency_overrides.update(overrides)
        try:
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=target_app), base_url="http://testserver"
            ) as http_client:
                yield http_client
        finally:
            for dependency in overrides:
                target_app.dependency_overrides.pop(dependency, None)

    return _make


@pytest.fixture
async def api(make_client: ClientFactory) -> AsyncIterator[httpx2.AsyncClient]:
    async with make_client() as http_client:
        yield http_client


@pytest.fixture
async def ws(api: httpx2.AsyncClient, outbox: Outbox) -> Workspace:
    """An organization with an admin, an agent, and a customer member."""
    admin = await create_user(api, full_name="Ada Admin")
    agent = await create_user(api, full_name="Abe Agent")
    customer = await create_user(api, full_name="Cleo Customer")
    org = await create_org(api, admin)
    await add_member(api, outbox, admin, org["id"], agent, "agent")
    await add_member(api, outbox, admin, org["id"], customer, "customer")
    return Workspace(org["id"], admin, agent, customer)
