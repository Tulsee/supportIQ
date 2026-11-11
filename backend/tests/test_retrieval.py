"""Retrieval end to end: BM25 in PostgreSQL, fusion, reranking, recorded runs."""

import math
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.retrieval.vector_store import QdrantVectorStore
from app.core.config import settings
from app.core.database import reset_rls_context, set_rls_context
from app.repositories.retrieval import BM25Retriever
from app.services.ingestion import Outcome
from tests.factories import API, AuthedUser, Workspace, create_org, create_user, in_org

pytestmark = pytest.mark.anyio

Ingest = Callable[..., Awaitable[Outcome]]

MANUAL = b"""# Troubleshooting

Error E-4012 means the cooling fan has stopped. Replace the fan module.
Error E-5020 means the power supply is overheating. Unplug the device for an hour.

# Wireless

Connect your phone to the network named SupportIQ-Setup and open the app.
The router supports WPA3 and WPA2 compatibility mode.

# Returns

You can return an unopened device within 30 days for a full refund.
"""


async def upload_and_index(
    api: httpx2.AsyncClient, admin: AuthedUser, org_id: str, ingest: Ingest, name: str, body: bytes
) -> str:
    response = await api.post(
        f"{API}/documents",
        files={"file": (name, body, "text/markdown")},
        headers=in_org(admin, org_id),
    )
    assert response.status_code == 202, response.text
    document_id = response.json()["id"]
    assert await ingest(document_id) is Outcome.INDEXED
    return document_id


async def retrieve(
    api: httpx2.AsyncClient, user: AuthedUser, org_id: str, query: str, **options: Any
) -> dict[str, Any]:
    response = await api.post(
        f"{API}/ai/retrieve", json={"query": query, **options}, headers=in_org(user, org_id)
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- BM25 ----------------------------------------------------------------------------------------


async def reference_bm25(session: AsyncSession, org_id: str, query: str) -> dict[uuid.UUID, float]:
    """Okapi BM25 computed in Python from the stored tsvectors, to check the SQL."""
    terms = set(
        await session.scalar(
            text("SELECT tsvector_to_array(to_tsvector('english'::regconfig, :q))"), {"q": query}
        )
    )
    rows = (
        await session.execute(
            text(
                "SELECT c.id, c.token_count, e.lexeme, coalesce(array_length(e.positions, 1), 1) "
                "FROM document_chunks c CROSS JOIN LATERAL unnest(c.search_vector) "
                "AS e(lexeme, positions, weights) WHERE c.organization_id = :org"
            ),
            {"org": org_id},
        )
    ).all()
    lengths = {row[0]: row[1] for row in rows}
    tf = {(row[0], row[2]): row[3] for row in rows}
    n, avgdl = len(lengths), sum(lengths.values()) / len(lengths)
    df = Counter(lexeme for (_, lexeme) in tf if lexeme in terms)
    k1, b = settings.bm25_k1, settings.bm25_b
    scores: dict[uuid.UUID, float] = {}
    for (chunk, lexeme), freq in tf.items():
        if lexeme not in terms:
            continue
        idf = math.log(1 + (n - df[lexeme] + 0.5) / (df[lexeme] + 0.5))
        norm = freq + k1 * (1 - b + b * lengths[chunk] / avgdl)
        scores[chunk] = scores.get(chunk, 0.0) + idf * freq * (k1 + 1) / norm
    return scores


async def test_bm25_matches_the_okapi_formula(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest, db_session: AsyncSession
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    await upload_and_index(
        api, ws.admin, ws.org_id, ingest, "faq.md", b"# Fans\nThe fan is quiet. Fan speed adapts."
    )
    await reset_rls_context(db_session)
    await set_rls_context(
        db_session, organization_id=uuid.UUID(ws.org_id), user_id=uuid.UUID(ws.agent.id)
    )
    query = "fan stopped error"

    hits = await BM25Retriever(
        db_session, uuid.UUID(ws.org_id), k1=settings.bm25_k1, b=settings.bm25_b
    ).search(query, 10)
    expected = await reference_bm25(db_session, ws.org_id, query)

    assert hits, "expected keyword hits"
    assert {hit.chunk_id: hit.score for hit in hits} == pytest.approx(expected)
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)


async def test_bm25_statistics_are_per_organization(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    before = await retrieve(api, ws.agent, ws.org_id, "E-4012", strategy="sparse", rerank=False)

    # Another tenant indexes many documents mentioning the same terms.
    other_admin = await create_user(api)
    other_org = await create_org(api, other_admin, "Noisy Co")
    for i in range(5):
        await upload_and_index(
            api,
            other_admin,
            other_org["id"],
            ingest,
            f"n{i}.md",
            f"# N{i}\nError E-4012 fan {i}".encode(),
        )
    after = await retrieve(api, ws.agent, ws.org_id, "E-4012", strategy="sparse", rerank=False)

    assert [r["chunk"]["id"] for r in after["results"]] == [
        r["chunk"]["id"] for r in before["results"]
    ]
    assert [r["sparse"]["score"] for r in after["results"]] == pytest.approx(
        [r["sparse"]["score"] for r in before["results"]]
    )
    assert {r["document"]["id"] for r in after["results"]} <= {
        r["document"]["id"] for r in before["results"]
    }


# --- The endpoint -------------------------------------------------------------------------------


async def test_adaptive_retrieval_finds_an_error_code_and_records_the_run(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)

    run = await retrieve(api, ws.agent, ws.org_id, "E-4012", top_k=3)

    assert run["query_type"] == "exact_identifier"
    assert (run["requested_strategy"], run["strategy"]) == ("auto", "sparse_heavy")
    assert "E-4012" in run["reasons"][0]
    top = run["results"][0]
    assert top["final_rank"] == 1
    assert "E-4012" in top["chunk"]["content"]
    assert top["sparse"]["rank"] == 1 and top["relevance"] is not None
    assert run["config"]["weights"] == {"sparse": 1.0, "dense": 0.3}
    assert run["config"]["embedding_model"] == "test/hashing-embedder"
    assert run["config"]["reranker_model"] == "test/word-overlap"
    assert run["config"]["bm25"] == {"k1": settings.bm25_k1, "b": settings.bm25_b}
    assert {"analysis", "sparse", "dense", "fusion", "rerank", "total"} <= set(run["timings_ms"])
    assert len(run["results"]) <= 3

    recorded = await api.get(f"{API}/ai/runs/{run['id']}", headers=in_org(ws.admin, ws.org_id))
    listed = await api.get(f"{API}/ai/runs", headers=in_org(ws.admin, ws.org_id))
    assert recorded.status_code == 200
    assert recorded.json()["results"] == run["results"]
    assert recorded.json()["user"]["full_name"] == "Abe Agent"
    assert [item["id"] for item in listed.json()["items"]] == [run["id"]]


async def test_fixed_strategies_for_comparison(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)

    sparse = await retrieve(api, ws.agent, ws.org_id, "fan module", strategy="sparse")
    dense = await retrieve(api, ws.agent, ws.org_id, "fan module", strategy="dense")
    hybrid = await retrieve(api, ws.agent, ws.org_id, "fan module", strategy="hybrid")

    assert all(r["dense"] is None for r in sparse["results"] + sparse["other_candidates"])
    assert all(r["sparse"] is None for r in dense["results"] + dense["other_candidates"])
    assert hybrid["config"]["weights"] == {"sparse": 1.0, "dense": 1.0}


async def test_without_reranking_the_fused_order_is_kept(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)

    run = await retrieve(api, ws.agent, ws.org_id, "router network", rerank=False)

    assert run["config"]["reranker_model"] is None
    assert [r["fused_rank"] for r in run["results"]] == sorted(
        r["fused_rank"] for r in run["results"]
    )
    assert all(r["rerank_score"] is None for r in run["results"])


async def test_vector_store_outage_degrades_to_keyword_search(
    api: httpx2.AsyncClient,
    ws: Workspace,
    ingest: Ingest,
    vector_store: QdrantVectorStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)

    async def down(*args: object, **kwargs: object) -> list:
        raise ConnectionError("qdrant unreachable")

    monkeypatch.setattr(vector_store, "search", down)

    run = await retrieve(api, ws.agent, ws.org_id, "fan module", strategy="hybrid")

    assert run["results"], "keyword search still answers"
    assert "qdrant unreachable" in run["degraded"]["dense"]


async def test_runs_keep_provenance_after_a_reindex(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest
) -> None:
    document_id = await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    run = await retrieve(api, ws.agent, ws.org_id, "E-4012")

    await api.post(f"{API}/documents/{document_id}/reindex", headers=in_org(ws.admin, ws.org_id))
    await ingest(document_id)

    recorded = (
        await api.get(f"{API}/ai/runs/{run['id']}", headers=in_org(ws.agent, ws.org_id))
    ).json()
    top = recorded["results"][0]
    assert top["chunk"] is None  # replaced by the re-index...
    assert top["document_id"] == document_id  # ...but where it came from is kept
    assert top["chunk_index"] == run["results"][0]["chunk_index"]


async def test_only_staff_of_the_organization(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    run = await retrieve(api, ws.agent, ws.org_id, "fan")
    outsider = await create_user(api)
    outsider_org = await create_org(api, outsider, "Outsider Co")

    customer = await api.post(
        f"{API}/ai/retrieve", json={"query": "fan"}, headers=in_org(ws.customer, ws.org_id)
    )
    foreign_run = await api.get(
        f"{API}/ai/runs/{run['id']}", headers=in_org(outsider, outsider_org["id"])
    )
    foreign_search = await retrieve(api, outsider, outsider_org["id"], "fan")

    assert customer.status_code == 403
    assert foreign_run.status_code == 404
    assert foreign_search["results"] == []
