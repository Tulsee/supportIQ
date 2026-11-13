"""Public and internal documents (Module 12): who may change visibility, what a
customer's database session can read, and public-only retrieval.

The customer assistant's boundary is held twice, by the retrieval filter
(``public_only``) and by row-level security; each layer is tested on its own here.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.embeddings.embedder import HashingEmbedder
from ai_engine.retrieval.engine import RequestedStrategy
from ai_engine.retrieval.vector_store import QdrantVectorStore
from app.core.config import settings
from app.core.database import reset_rls_context, set_rls_context
from app.repositories.retrieval import BM25Retriever
from app.services.ingestion import Outcome
from app.services.retrieval import RetrievalService
from tests.factories import API, AuthedUser, Workspace, add_member, create_org, create_user, in_org
from tests.test_retrieval import reference_bm25, upload_and_index

pytestmark = pytest.mark.anyio

Ingest = Callable[..., Awaitable[Outcome]]
DOCS = f"{API}/documents"

PUBLIC_GUIDE = b"""# Returns

You can return an unopened router within 30 days for a full refund.
Refunds go back to the original payment method within five business days.
"""

# Unique words mark the internal document: any hit containing them is a leak.
INTERNAL_PLAYBOOK = b"""# Goodwill credits

Agents may grant a goodwill refund credit of up to 40 dollars without approval.
Use the code ZEPHYR-OVERRIDE for refund exceptions beyond 30 days.
"""


async def set_visibility(
    api: httpx2.AsyncClient, user: AuthedUser, org_id: str, doc_id: str, visibility: str
) -> httpx2.Response:
    return await api.patch(
        f"{DOCS}/{doc_id}", json={"visibility": visibility}, headers=in_org(user, org_id)
    )


@pytest.fixture
async def library(api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest) -> dict[str, str]:
    """One public and one internal document, indexed."""
    public = await upload_and_index(api, ws.admin, ws.org_id, ingest, "returns.md", PUBLIC_GUIDE)
    internal = await upload_and_index(
        api, ws.admin, ws.org_id, ingest, "playbook.md", INTERNAL_PLAYBOOK
    )
    response = await set_visibility(api, ws.admin, ws.org_id, public, "public")
    assert response.status_code == 200, response.text
    return {"public": public, "internal": internal}


async def as_member(session: AsyncSession, org_id: str, user: AuthedUser) -> None:
    await reset_rls_context(session)
    await set_rls_context(session, organization_id=uuid.UUID(org_id), user_id=uuid.UUID(user.id))


# --- The API -------------------------------------------------------------------------------------


async def test_documents_are_internal_until_an_admin_publishes_them(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest
) -> None:
    doc_id = await upload_and_index(api, ws.admin, ws.org_id, ingest, "returns.md", PUBLIC_GUIDE)
    created = await api.get(f"{DOCS}/{doc_id}", headers=in_org(ws.agent, ws.org_id))
    assert created.json()["visibility"] == "internal"

    for user in (ws.agent, ws.customer):
        response = await set_visibility(api, user, ws.org_id, doc_id, "public")
        assert response.status_code == 403, user.email

    published = await set_visibility(api, ws.admin, ws.org_id, doc_id, "public")
    assert published.status_code == 200, published.text
    assert published.json()["visibility"] == "public"

    unchanged = await set_visibility(api, ws.admin, ws.org_id, doc_id, "public")
    assert unchanged.status_code == 200
    renamed = await api.patch(
        f"{DOCS}/{doc_id}", json={"title": "Returns guide"}, headers=in_org(ws.admin, ws.org_id)
    )
    assert renamed.json()["title"] == "Returns guide"
    assert renamed.json()["visibility"] == "public"

    log = await api.get(
        f"{API}/audit-log", params={"entity_type": "document"}, headers=in_org(ws.admin, ws.org_id)
    )
    actions = [entry["action"] for entry in log.json()["items"]]
    # Newest first; repeating a value records nothing.
    assert actions == ["document.renamed", "document.published"]
    assert log.json()["items"][1]["changes"] == {"visibility": {"from": "internal", "to": "public"}}


async def test_documents_can_be_listed_by_visibility(
    api: httpx2.AsyncClient, ws: Workspace, library: dict[str, str]
) -> None:
    async def listed(visibility: str | None) -> set[str]:
        params = {"visibility": visibility} if visibility else {}
        response = await api.get(DOCS, params=params, headers=in_org(ws.agent, ws.org_id))
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    assert await listed("public") == {library["public"]}
    assert await listed("internal") == {library["internal"]}
    assert await listed(None) == set(library.values())


async def test_customers_still_cannot_list_or_search_documents(
    api: httpx2.AsyncClient, ws: Workspace, library: dict[str, str]
) -> None:
    """Public means "may answer customers' questions", not "browsable by customers"."""
    listing = await api.get(DOCS, headers=in_org(ws.customer, ws.org_id))
    detail = await api.get(f"{DOCS}/{library['public']}", headers=in_org(ws.customer, ws.org_id))
    download = await api.get(
        f"{DOCS}/{library['public']}/file", headers=in_org(ws.customer, ws.org_id)
    )
    search = await api.post(
        f"{API}/ai/retrieve", json={"query": "refund"}, headers=in_org(ws.customer, ws.org_id)
    )
    assert {r.status_code for r in (listing, detail, download, search)} == {403}


# --- Row-level security --------------------------------------------------------------------------


async def visible_documents(session: AsyncSession) -> tuple[set[str], set[str]]:
    """Document ids, and the document ids of chunks, an unfiltered query returns."""
    documents = await session.scalars(text("SELECT id FROM documents"))
    chunks = await session.scalars(text("SELECT DISTINCT document_id FROM document_chunks"))
    return {str(d) for d in documents}, {str(d) for d in chunks}


async def test_a_customer_session_reads_only_public_documents_and_their_chunks(
    api: httpx2.AsyncClient,
    ws: Workspace,
    library: dict[str, str],
    db_session: AsyncSession,
) -> None:
    await as_member(db_session, ws.org_id, ws.customer)
    assert await visible_documents(db_session) == ({library["public"]}, {library["public"]})
    leaked = await db_session.scalar(
        text("SELECT count(*) FROM document_chunks WHERE content ILIKE '%ZEPHYR%'")
    )
    assert leaked == 0

    await as_member(db_session, ws.org_id, ws.agent)  # staff are unaffected
    assert await visible_documents(db_session) == (set(library.values()), set(library.values()))


async def test_customers_of_other_tenants_see_none_of_the_public_documents(
    api: httpx2.AsyncClient, outbox, library: dict[str, str], db_session: AsyncSession
) -> None:
    owner = await create_user(api)
    other_org = await create_org(api, owner, "Other Co")
    their_customer = await create_user(api)
    await add_member(api, outbox, owner, other_org["id"], their_customer, "customer")

    await as_member(db_session, other_org["id"], their_customer)
    assert await visible_documents(db_session) == (set(), set())


async def test_customers_cannot_change_documents_in_the_database(
    ws: Workspace, library: dict[str, str], db_session: AsyncSession
) -> None:
    await as_member(db_session, ws.org_id, ws.customer)
    published = await db_session.execute(
        text("UPDATE documents SET visibility = 'public' WHERE id = :id"),
        {"id": library["internal"]},
    )
    edited = await db_session.execute(
        text("UPDATE document_chunks SET content = 'x' WHERE document_id = :id"),
        {"id": library["public"]},
    )
    deleted = await db_session.execute(
        text("DELETE FROM documents WHERE id = :id"), {"id": library["public"]}
    )
    assert published.rowcount == edited.rowcount == deleted.rowcount == 0


async def test_making_a_document_internal_again_takes_effect_at_once(
    api: httpx2.AsyncClient, ws: Workspace, library: dict[str, str], db_session: AsyncSession
) -> None:
    response = await set_visibility(api, ws.admin, ws.org_id, library["public"], "internal")
    assert response.status_code == 200

    await as_member(db_session, ws.org_id, ws.customer)
    assert await visible_documents(db_session) == (set(), set())


# --- Retrieval -----------------------------------------------------------------------------------


def retrieval(
    session: AsyncSession, embedder: HashingEmbedder, vector_store: QdrantVectorStore, reranker
) -> RetrievalService:
    return RetrievalService(
        session, embedder=embedder, vector_store=vector_store, reranker=reranker
    )


async def search_documents(
    service: RetrievalService, org_id: str, query: str, **options: Any
) -> dict[RequestedStrategy, set[uuid.UUID]]:
    """The documents of the chunks each strategy returns (all candidates, not just top k)."""
    found: dict[RequestedStrategy, set[uuid.UUID]] = {}
    for strategy in (RequestedStrategy.SPARSE, RequestedStrategy.DENSE, RequestedStrategy.HYBRID):
        outcome = await service.search(
            uuid.UUID(org_id), query, strategy=strategy, rerank=True, top_k=5, **options
        )
        chunks = await service.documents.chunks_by_id(
            uuid.UUID(org_id), [c.chunk_id for c in outcome.candidates]
        )
        assert len(chunks) == len(outcome.candidates)
        found[strategy] = {chunk.document_id for chunk in chunks.values()}
    return found


async def test_public_only_retrieval_never_returns_internal_chunks(
    ws: Workspace,
    library: dict[str, str],
    db_session: AsyncSession,
    embedder: HashingEmbedder,
    vector_store: QdrantVectorStore,
    reranker,
) -> None:
    """The code filter alone (a staff session, which RLS would let read everything)."""
    await as_member(db_session, ws.org_id, ws.agent)
    service = retrieval(db_session, embedder, vector_store, reranker)
    query = "refund code for exceptions beyond 30 days goodwill credit"

    everything = await search_documents(service, ws.org_id, query)
    public = await search_documents(service, ws.org_id, query, public_only=True)

    internal = uuid.UUID(library["internal"])
    assert all(internal in found for found in everything.values())  # the query does reach it
    assert all(found == {uuid.UUID(library["public"])} for found in public.values()), public


async def test_a_customer_session_retrieves_no_internal_chunks_without_the_filter(
    ws: Workspace,
    library: dict[str, str],
    db_session: AsyncSession,
    embedder: HashingEmbedder,
    vector_store: QdrantVectorStore,
    reranker,
) -> None:
    """Row-level security alone: ``public_only`` deliberately left off, as if the code
    forgot it. Vector hits for internal chunks can't be resolved and are dropped."""
    await as_member(db_session, ws.org_id, ws.customer)
    service = retrieval(db_session, embedder, vector_store, reranker)

    found = await search_documents(
        service, ws.org_id, "refund code for exceptions beyond 30 days goodwill credit"
    )

    assert all(docs == {uuid.UUID(library["public"])} for docs in found.values()), found


async def test_public_only_bm25_statistics_cover_public_chunks_only(
    api: httpx2.AsyncClient, ws: Workspace, library: dict[str, str], db_session: AsyncSession
) -> None:
    """Scores computed from public chunks alone, so they reveal nothing about internal
    ones; the code filter and RLS give identical scores."""
    query = "refund within 30 days"
    org = uuid.UUID(ws.org_id)

    await as_member(db_session, ws.org_id, ws.customer)
    expected = await reference_bm25(db_session, ws.org_id, query)  # what RLS lets it see
    under_rls = await BM25Retriever(db_session, org, k1=settings.bm25_k1, b=settings.bm25_b).search(
        query, 10
    )

    await as_member(db_session, ws.org_id, ws.agent)
    filtered = await BM25Retriever(
        db_session, org, k1=settings.bm25_k1, b=settings.bm25_b, public_only=True
    ).search(query, 10)
    unfiltered = await BM25Retriever(
        db_session, org, k1=settings.bm25_k1, b=settings.bm25_b
    ).search(query, 10)

    assert expected
    assert {h.chunk_id: h.score for h in under_rls} == pytest.approx(expected)
    assert {h.chunk_id: h.score for h in filtered} == pytest.approx(expected)
    assert {h.chunk_id: h.score for h in unfiltered} != pytest.approx(expected)


async def test_public_only_retrieval_with_no_public_documents_finds_nothing(
    api: httpx2.AsyncClient,
    ws: Workspace,
    ingest: Ingest,
    db_session: AsyncSession,
    embedder: HashingEmbedder,
    vector_store: QdrantVectorStore,
    reranker,
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "playbook.md", INTERNAL_PLAYBOOK)
    await as_member(db_session, ws.org_id, ws.agent)
    service = retrieval(db_session, embedder, vector_store, reranker)

    found = await search_documents(service, ws.org_id, "goodwill refund credit", public_only=True)

    assert all(docs == set() for docs in found.values()), found
