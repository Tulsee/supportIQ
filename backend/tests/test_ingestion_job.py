"""The Celery task end to end: worker entry point, its own engine, real commits."""

import hashlib
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from anyio import to_thread
from qdrant_client import AsyncQdrantClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.core.knowledge as knowledge
import app.core.storage as storage_module
from ai_engine.embeddings.embedder import HashingEmbedder
from ai_engine.ingestion.extract import DocumentType
from app.core.database import set_rls_context
from app.core.storage import LocalObjectStorage
from app.models import (
    Document,
    DocumentChunk,
    DocumentStatus,
    Organization,
    OrganizationMember,
    OrgRole,
    User,
)
from app.workers.tasks import ingest_document
from tests.samples import HANDBOOK_MD

pytestmark = pytest.mark.anyio


@pytest.fixture
async def committed_document(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> AsyncIterator[tuple[Document, LocalObjectStorage]]:
    """A queued document, committed for real (the worker uses its own connection),
    removed again afterwards."""
    storage = LocalObjectStorage(tmp_path / "objects")
    content = HANDBOOK_MD.encode()
    org_id, user_id, doc_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    key = f"documents/{org_id}/{doc_id}/v1/handbook.md"
    await storage.put(key, content, "text/markdown")
    async with session_factory() as session:
        await set_rls_context(session, bypass=True)
        session.add(
            User(id=user_id, email=f"{user_id}@example.com", password_hash="x", full_name="W")
        )
        session.add(Organization(id=org_id, name="Worker Co", slug=f"worker-{org_id.hex[:8]}"))
        await session.flush()
        session.add(OrganizationMember(organization_id=org_id, user_id=user_id, role=OrgRole.ADMIN))
        document = Document(
            id=doc_id,
            organization_id=org_id,
            title="Handbook",
            filename="handbook.md",
            file_type=DocumentType.MARKDOWN,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            storage_key=key,
            version=1,
            status=DocumentStatus.QUEUED,
            ingestion_id=uuid.uuid4(),
            uploaded_by_id=user_id,
            chunk_count=0,
            token_count=0,
        )
        session.add(document)
        await session.commit()
    try:
        yield document, storage
    finally:
        async with session_factory() as session:
            await set_rls_context(session, bypass=True)
            await session.execute(delete(Organization).where(Organization.id == org_id))
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()


async def test_task_indexes_a_document(
    committed_document: tuple[Document, LocalObjectStorage],
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document, storage = committed_document
    embedder = HashingEmbedder(64)
    client = AsyncQdrantClient(location=":memory:")

    async def keep_open() -> None:  # the job closes its client; keep ours to inspect
        return None

    monkeypatch.setattr(client, "close", keep_open)
    monkeypatch.setattr(knowledge, "new_qdrant_client", lambda: client)
    monkeypatch.setattr(knowledge, "get_embedder", lambda: embedder)
    monkeypatch.setattr(storage_module, "get_object_storage", lambda: storage)

    # Celery runs the task in-process; asyncio.run needs a thread without a loop.
    result = await to_thread.run_sync(
        lambda: ingest_document.apply(args=[str(document.id), str(document.ingestion_id)]).get()
    )

    assert result == "indexed"
    async with session_factory() as session:
        await set_rls_context(session, bypass=True)
        indexed = await session.get(Document, document.id)
        chunks = await session.scalar(
            select(func.count()).where(DocumentChunk.document_id == document.id)
        )
    assert indexed is not None and indexed.status is DocumentStatus.INDEXED
    assert chunks == indexed.chunk_count > 0
    collection = knowledge.vector_store_for(client, embedder).collection
    assert (await client.count(collection)).count == chunks
