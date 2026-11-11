import uuid
from dataclasses import replace
from datetime import timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.embeddings.embedder import HashingEmbedder
from ai_engine.ingestion.ocr import OcrLine, OcrOptions
from ai_engine.retrieval.vector_store import QdrantVectorStore
from app.core.config import settings
from app.core.database import reset_rls_context, rls_bypass, set_rls_context
from app.core.security import utcnow
from app.core.storage import LocalObjectStorage, StorageError
from app.services.ingestion import (
    IngestionService,
    Outcome,
    TransientIngestionError,
)
from app.services.maintenance import fail_stalled_ingestions
from tests.factories import (
    API,
    TEST_INGESTION_LIMITS,
    AuthedUser,
    RecordingQueue,
    Workspace,
    create_org,
    create_user,
    in_org,
)
from tests.samples import HANDBOOK_MD, make_pdf

pytestmark = pytest.mark.anyio

DOCS = f"{API}/documents"
LIMITS = TEST_INGESTION_LIMITS


async def upload(
    api: httpx2.AsyncClient,
    ws: Workspace,
    name: str,
    content: bytes,
    *,
    actor: AuthedUser | None = None,
    title: str | None = None,
    org_id: str | None = None,
) -> httpx2.Response:
    return await api.post(
        DOCS,
        files={"file": (name, content, "application/octet-stream")},
        data={"title": title} if title else {},
        headers=in_org(actor or ws.admin, org_id or ws.org_id),
    )


async def get_doc(api: httpx2.AsyncClient, ws: Workspace, doc_id: str) -> dict[str, Any]:
    response = await api.get(f"{DOCS}/{doc_id}", headers=in_org(ws.agent, ws.org_id))
    assert response.status_code == 200, response.text
    return response.json()


async def chunks_of(api: httpx2.AsyncClient, ws: Workspace, doc_id: str) -> list[dict]:
    response = await api.get(
        f"{DOCS}/{doc_id}/chunks", params={"limit": 100}, headers=in_org(ws.agent, ws.org_id)
    )
    return response.json()["items"]


async def search(api: httpx2.AsyncClient, ws: Workspace, q: str) -> list[dict]:
    """Semantic search through the retrieval endpoint (dense only, no reranking)."""
    response = await api.post(
        f"{API}/ai/retrieve",
        json={"query": q, "strategy": "dense", "rerank": False, "top_k": 8},
        headers=in_org(ws.agent, ws.org_id),
    )
    assert response.status_code == 200, response.text
    return response.json()["results"]


async def vector_count(store: QdrantVectorStore, document_id: str) -> int:
    from qdrant_client import models

    if not await store.client.collection_exists(store.collection):
        return 0
    result = await store.client.count(
        store.collection,
        count_filter=models.Filter(
            must=[
                models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))
            ]
        ),
    )
    return result.count


# --- Upload ---------------------------------------------------------------------------------


async def test_upload_is_validated_stored_and_queued(
    api: httpx2.AsyncClient,
    ws: Workspace,
    storage: LocalObjectStorage,
    ingestion_queue: RecordingQueue,
) -> None:
    response = await upload(api, ws, "Customer_Handbook.md", HANDBOOK_MD.encode())

    assert response.status_code == 202
    document = response.json()
    assert document["status"] == "queued"
    assert document["title"] == "Customer Handbook"
    assert document["file_type"] == "markdown"
    assert document["version"] == 1 and document["indexed_version"] is None
    assert document["uploaded_by"]["full_name"] == "Ada Admin"
    assert [str(doc) for doc, _ in ingestion_queue.jobs] == [document["id"]]
    [key] = await storage.list_keys(f"documents/{ws.org_id}/{document['id']}/")
    assert key.endswith(".md") and "Handbook" not in key  # generated key, not the user's name
    assert await storage.get(key) == HANDBOOK_MD.encode()


async def test_only_admins_upload_and_customers_never_read(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    by_agent = await upload(api, ws, "notes.txt", b"agent notes", actor=ws.agent)
    agent_list = await api.get(DOCS, headers=in_org(ws.agent, ws.org_id))
    customer_list = await api.get(DOCS, headers=in_org(ws.customer, ws.org_id))
    customer_search = await api.post(
        f"{API}/ai/retrieve", json={"query": "refund"}, headers=in_org(ws.customer, ws.org_id)
    )

    assert by_agent.status_code == 403
    assert agent_list.status_code == 200
    assert customer_list.status_code == customer_search.status_code == 403


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("tool.exe", b"MZ binary", "Unsupported file type"),
        ("report.pdf", b"hello, not a pdf", "doesn't look like a PDF"),
    ],
    ids=["extension", "content"],
)
async def test_invalid_files_are_rejected(
    api: httpx2.AsyncClient,
    ws: Workspace,
    storage: LocalObjectStorage,
    name: str,
    content: bytes,
    message: str,
) -> None:
    response = await upload(api, ws, name, content)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_file"
    assert message in response.json()["error"]["message"]
    assert await storage.list_keys("documents/") == []


async def test_oversized_files_are_rejected(
    api: httpx2.AsyncClient,
    ws: Workspace,
    storage: LocalObjectStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_upload_bytes", 1_000)

    response = await upload(api, ws, "big.txt", b"x" * 1_001)

    assert response.status_code == 413
    assert await storage.list_keys("documents/") == []


async def test_duplicate_uploads_conflict(api: httpx2.AsyncClient, ws: Workspace) -> None:
    first = (await upload(api, ws, "a.md", HANDBOOK_MD.encode())).json()

    again = await upload(api, ws, "copy.md", HANDBOOK_MD.encode())

    assert again.status_code == 409
    assert again.json()["error"]["details"] == {"document_id": first["id"]}


async def test_upload_rate_limit(
    api: httpx2.AsyncClient, ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "document_upload_limit", 2)
    for i in range(2):
        assert (await upload(api, ws, f"n{i}.txt", f"note {i}".encode())).status_code == 202

    response = await upload(api, ws, "n3.txt", b"note 3")

    assert response.status_code == 429


async def test_queue_outage_marks_the_document_failed(
    api: httpx2.AsyncClient, ws: Workspace, ingestion_queue: RecordingQueue
) -> None:
    ingestion_queue.unavailable = True

    response = await upload(api, ws, "a.md", HANDBOOK_MD.encode())

    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    assert "job queue is unavailable" in response.json()["error"]


# --- Ingestion --------------------------------------------------------------------------------


async def test_markdown_is_indexed_with_sections(
    api: httpx2.AsyncClient, ws: Workspace, ingest, vector_store: QdrantVectorStore
) -> None:
    doc = (await upload(api, ws, "handbook.md", HANDBOOK_MD.encode())).json()

    assert await ingest(doc["id"]) is Outcome.INDEXED

    document = await get_doc(api, ws, doc["id"])
    chunks = await chunks_of(api, ws, doc["id"])
    assert document["status"] == "indexed"
    assert document["indexed_version"] == 1
    assert document["chunk_count"] == len(chunks) > 1
    assert document["token_count"] == sum(c["token_count"] for c in chunks)
    assert document["embedding_model"] == "test/hashing-embedder"
    assert {c["section_heading"] for c in chunks} == {
        "Refund policy",
        "Damaged items",
        "Shipping",
    }
    assert all(c["token_count"] <= LIMITS.chunking.size for c in chunks)
    assert await vector_count(vector_store, doc["id"]) == len(chunks)


async def test_pdf_chunks_keep_their_pages(api: httpx2.AsyncClient, ws: Workspace, ingest) -> None:
    pdf = make_pdf([["Installation guide. Plug in the router."], ["Reset by holding the button."]])
    doc = (await upload(api, ws, "router.pdf", pdf)).json()

    await ingest(doc["id"])

    chunks = await chunks_of(api, ws, doc["id"])
    document = await get_doc(api, ws, doc["id"])
    assert document["page_count"] == 2
    assert (chunks[0]["page_number"], chunks[-1]["page_end"]) == (1, 2)


async def test_search_finds_relevant_chunks_only_in_the_organization(
    api: httpx2.AsyncClient, ws: Workspace, ingest
) -> None:
    doc = (await upload(api, ws, "handbook.md", HANDBOOK_MD.encode())).json()
    await ingest(doc["id"])
    other_admin = await create_user(api)
    other_org = await create_org(api, other_admin, "Other Co")
    secret = b"# Damaged items\nOther Co replaces damaged items and pays shipping photo refund."
    other_doc = await api.post(
        DOCS,
        files={"file": ("other.md", secret, "text/markdown")},
        headers=in_org(other_admin, other_org["id"]),
    )
    await ingest(other_doc.json()["id"])

    results = await search(api, ws, "item arrived damaged, send a photo")

    assert results, "expected hits"
    assert {r["document"]["id"] for r in results} == {doc["id"]}
    assert results[0]["chunk"]["section_heading"] == "Damaged items"
    assert results[0]["dense"]["score"] >= results[-1]["dense"]["score"]


async def test_failed_ingestion_is_visible_and_retryable(
    api: httpx2.AsyncClient, ws: Workspace, ingest, ingestion_queue: RecordingQueue
) -> None:
    doc = (await upload(api, ws, "scan.pdf", make_pdf([[]]))).json()

    assert await ingest(doc["id"]) is Outcome.FAILED

    failed = await get_doc(api, ws, doc["id"])
    assert failed["status"] == "failed"
    assert "OCR" in failed["error"]
    retry = await api.post(f"{DOCS}/{doc['id']}/reindex", headers=in_org(ws.admin, ws.org_id))
    assert retry.status_code == 202
    assert retry.json()["status"] == "queued" and retry.json()["error"] is None
    assert len(ingestion_queue.jobs) == 2
    assert ingestion_queue.jobs[0][1] != ingestion_queue.jobs[1][1]  # a new attempt id


async def test_scanned_pdf_is_indexed_with_ocr(
    api: httpx2.AsyncClient,
    ws: Workspace,
    db_session: AsyncSession,
    storage: LocalObjectStorage,
    embedder: HashingEmbedder,
    vector_store: QdrantVectorStore,
    ingestion_queue: RecordingQueue,
) -> None:
    class OneLineOcr:
        name = "fake"

        def read(self, image: object) -> list[OcrLine]:
            return [OcrLine("Invoice total is 120 euros, due in 30 days.", 0.97)]

    doc = (await upload(api, ws, "scan.pdf", make_pdf([[]]))).json()
    limits = replace(LIMITS, ocr=OcrOptions(OneLineOcr()))
    service = IngestionService(db_session, storage, embedder, vector_store, limits)

    outcome = await service.run(uuid.UUID(doc["id"]), ingestion_queue.jobs[-1][1])

    assert outcome is Outcome.INDEXED
    document = await get_doc(api, ws, doc["id"])
    assert document["status"] == "indexed"
    assert "read with OCR" in document["error"]  # shown as an indexing warning
    [chunk] = await chunks_of(api, ws, doc["id"])
    assert chunk["page_number"] == 1 and "120 euros" in chunk["content"]


async def test_reindex_is_refused_while_indexing(api: httpx2.AsyncClient, ws: Workspace) -> None:
    doc = (await upload(api, ws, "a.md", HANDBOOK_MD.encode())).json()

    response = await api.post(f"{DOCS}/{doc['id']}/reindex", headers=in_org(ws.admin, ws.org_id))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ingestion_in_progress"


async def test_obsolete_jobs_do_nothing(api: httpx2.AsyncClient, ws: Workspace, ingest) -> None:
    doc = (await upload(api, ws, "a.md", HANDBOOK_MD.encode())).json()

    outcome = await ingest(doc["id"], ingestion_id=uuid.uuid4())

    assert outcome is Outcome.SKIPPED
    assert (await get_doc(api, ws, doc["id"]))["status"] == "queued"


async def test_transient_failures_are_retried_then_reported(
    api: httpx2.AsyncClient,
    ws: Workspace,
    ingest,
    storage: LocalObjectStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = (await upload(api, ws, "a.md", HANDBOOK_MD.encode())).json()

    async def storage_down(key: str) -> bytes:
        raise StorageError("connection refused")

    monkeypatch.setattr(storage, "get", storage_down)

    with pytest.raises(TransientIngestionError):
        await ingest(doc["id"], final_attempt=False)
    assert (await get_doc(api, ws, doc["id"]))["status"] == "processing"  # will be retried

    assert await ingest(doc["id"], final_attempt=True) is Outcome.FAILED
    failed = await get_doc(api, ws, doc["id"])
    assert failed["status"] == "failed"
    assert "service was unavailable" in failed["error"]


async def test_new_version_replaces_chunks_without_stale_results(
    api: httpx2.AsyncClient,
    ws: Workspace,
    ingest,
    vector_store: QdrantVectorStore,
    storage: LocalObjectStorage,
) -> None:
    v1 = b"# Returns\nThe return window is thirty days for unopened boxes."
    v2 = b"# Returns\nThe return window is ninety days for every order."
    doc = (await upload(api, ws, "returns.md", v1)).json()
    await ingest(doc["id"])

    new_version = await api.post(
        f"{DOCS}/{doc['id']}/versions",
        files={"file": ("returns-2026.md", v2, "text/markdown")},
        headers=in_org(ws.admin, ws.org_id),
    )
    assert new_version.status_code == 202
    pending = new_version.json()
    assert (pending["version"], pending["indexed_version"], pending["status"]) == (2, 1, "queued")
    # Until v2 is indexed, v1 is what search returns.
    assert "thirty" in (await search(api, ws, "return window days"))[0]["chunk"]["content"]

    await ingest(doc["id"])

    results = await search(api, ws, "return window days")
    chunks = await chunks_of(api, ws, doc["id"])
    assert [r["chunk"]["content"] for r in results] == [
        "Returns\n\nThe return window is ninety days for every order."
    ]
    assert {c["document_version"] for c in chunks} == {2}
    assert await vector_count(vector_store, doc["id"]) == len(chunks)
    [only_file] = await storage.list_keys(f"documents/{ws.org_id}/{doc['id']}/")
    assert "/v2/" in only_file  # the superseded original is gone


async def test_reindexing_the_same_version_replaces_vectors(
    api: httpx2.AsyncClient, ws: Workspace, ingest, vector_store: QdrantVectorStore
) -> None:
    doc = (await upload(api, ws, "handbook.md", HANDBOOK_MD.encode())).json()
    await ingest(doc["id"])
    before = {c["id"] for c in await chunks_of(api, ws, doc["id"])}

    await api.post(f"{DOCS}/{doc['id']}/reindex", headers=in_org(ws.admin, ws.org_id))
    await ingest(doc["id"])

    after = {c["id"] for c in await chunks_of(api, ws, doc["id"])}
    assert before.isdisjoint(after) and len(after) == len(before)
    assert await vector_count(vector_store, doc["id"]) == len(after)


async def test_delete_removes_document_chunks_vectors_and_files(
    api: httpx2.AsyncClient,
    ws: Workspace,
    ingest,
    vector_store: QdrantVectorStore,
    storage: LocalObjectStorage,
) -> None:
    doc = (await upload(api, ws, "handbook.md", HANDBOOK_MD.encode())).json()
    await ingest(doc["id"])

    by_agent = await api.delete(f"{DOCS}/{doc['id']}", headers=in_org(ws.agent, ws.org_id))
    deleted = await api.delete(f"{DOCS}/{doc['id']}", headers=in_org(ws.admin, ws.org_id))

    assert by_agent.status_code == 403
    assert deleted.status_code == 204
    gone = await api.get(f"{DOCS}/{doc['id']}", headers=in_org(ws.agent, ws.org_id))
    assert gone.status_code == 404
    assert await vector_count(vector_store, doc["id"]) == 0
    assert await storage.list_keys(f"documents/{ws.org_id}/") == []
    assert await search(api, ws, "refund") == []


async def test_download_returns_the_original_as_an_attachment(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    doc = (await upload(api, ws, "Ré sumé.md", HANDBOOK_MD.encode())).json()

    response = await api.get(f"{DOCS}/{doc['id']}/file", headers=in_org(ws.agent, ws.org_id))

    assert response.status_code == 200
    assert response.content == HANDBOOK_MD.encode()
    assert response.headers["content-type"].startswith("text/markdown")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''R%C3%A9%20sum%C3%A9.md" in disposition
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_unsafe_file_names_are_sanitized(api: httpx2.AsyncClient, ws: Workspace) -> None:
    response = await upload(api, ws, "..\\..\\etc/<evil>.md", b"# Hi\nhello there")

    assert response.json()["filename"] == "_evil_.md"


# --- Housekeeping and isolation -----------------------------------------------------------------


async def test_stalled_ingestions_are_marked_failed(
    api: httpx2.AsyncClient, ws: Workspace, db_session: AsyncSession
) -> None:
    doc = (await upload(api, ws, "a.md", HANDBOOK_MD.encode())).json()

    async with rls_bypass(db_session):
        stalled = await fail_stalled_ingestions(db_session, utcnow() + timedelta(minutes=1))

    assert stalled == 1
    document = await get_doc(api, ws, doc["id"])
    assert document["status"] == "failed"
    assert "worker may have stopped" in document["error"]


async def test_customer_role_and_other_tenants_see_no_documents_in_the_database(
    api: httpx2.AsyncClient, ws: Workspace, ingest, db_session: AsyncSession
) -> None:
    doc = (await upload(api, ws, "handbook.md", HANDBOOK_MD.encode())).json()
    await ingest(doc["id"])
    outsider = await create_user(api)
    outsider_org = await create_org(api, outsider, "Outsider Co")

    async def visible(org_id: str, user: AuthedUser) -> tuple[int, int]:
        await reset_rls_context(db_session)
        await set_rls_context(
            db_session, organization_id=uuid.UUID(org_id), user_id=uuid.UUID(user.id)
        )
        documents = await db_session.scalar(text("SELECT count(*) FROM documents"))
        chunks = await db_session.scalar(text("SELECT count(*) FROM document_chunks"))
        return documents, chunks

    assert (await visible(ws.org_id, ws.agent))[0] == 1
    assert await visible(ws.org_id, ws.customer) == (0, 0)  # customer role in the same org
    assert await visible(outsider_org["id"], outsider) == (0, 0)  # another tenant
