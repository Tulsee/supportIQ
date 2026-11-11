"""Query analysis, Reciprocal Rank Fusion, and the retrieval pipeline (no database,
no models)."""

import uuid
from collections.abc import Sequence

import pytest

from ai_engine.reranking.cross_encoder import OverlapReranker, relevance
from ai_engine.retrieval.engine import (
    RequestedStrategy,
    RetrievalConfig,
    RetrievalEngine,
    RetrievalUnavailableError,
)
from ai_engine.retrieval.fusion import RankedChunk, reciprocal_rank_fusion
from ai_engine.retrieval.query_analysis import QueryType, Strategy, analyze

pytestmark = pytest.mark.anyio


# --- Query analysis -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "query_type", "strategy"),
    [
        ("E-4012", QueryType.EXACT_IDENTIFIER, Strategy.SPARSE_HEAVY),
        ("what does error 0x80070005 mean", QueryType.EXACT_IDENTIFIER, Strategy.SPARSE_HEAVY),
        ('"factory settings"', QueryType.EXACT_IDENTIFIER, Strategy.SPARSE_HEAVY),
        ("Can I return an opened item?", QueryType.POLICY, Strategy.HYBRID),
        ("refund eligibility for annual plans", QueryType.POLICY, Strategy.HYBRID),
        (
            "How do I connect the router to my phone?",
            QueryType.NATURAL_QUESTION,
            Strategy.DENSE_HEAVY,
        ),
        ("router X200 wifi setup", QueryType.TERMINOLOGY, Strategy.HYBRID),
        ("ERR404 on login", QueryType.EXACT_IDENTIFIER, Strategy.SPARSE_HEAVY),
        ("X200 error lights", QueryType.EXACT_IDENTIFIER, Strategy.SPARSE_HEAVY),
        ("HTTP 503 from the API", QueryType.EXACT_IDENTIFIER, Strategy.SPARSE_HEAVY),
        ("billing", QueryType.AMBIGUOUS, Strategy.HYBRID),
        (
            "the screen stays black after I plug the cable in",
            QueryType.NATURAL_QUESTION,
            Strategy.DENSE_HEAVY,
        ),
    ],
)
def test_query_analysis(query: str, query_type: QueryType, strategy: Strategy) -> None:
    analysis = analyze(query)

    assert (analysis.query_type, analysis.strategy) == (query_type, strategy)
    assert analysis.reasons  # every decision explains itself


def test_ambiguous_queries_look_deeper() -> None:
    assert analyze("billing").candidate_multiplier == 2
    assert analyze("How do I reset the router?").candidate_multiplier == 1


# --- Fusion ----------------------------------------------------------------------------------


def ids(n: int) -> list[uuid.UUID]:
    return [uuid.UUID(int=i) for i in range(1, n + 1)]


def test_rrf_rewards_agreement_between_lists() -> None:
    a, b, c = ids(3)
    sparse = [RankedChunk(a, 9.0), RankedChunk(b, 5.0)]
    dense = [RankedChunk(c, 0.9), RankedChunk(b, 0.8)]

    fused = reciprocal_rank_fusion({"sparse": sparse, "dense": dense}, {}, k=60)

    assert fused[0].chunk_id == b  # second in both beats first in one
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 62)
    assert fused[0].sources == {"sparse": (2, 5.0), "dense": (2, 0.8)}


def test_rrf_weights_tilt_the_result() -> None:
    a, c = ids(2)
    sparse, dense = [RankedChunk(a, 1.0)], [RankedChunk(c, 1.0)]

    sparse_heavy = reciprocal_rank_fusion(
        {"sparse": sparse, "dense": dense}, {"sparse": 1.0, "dense": 0.3}
    )
    dense_heavy = reciprocal_rank_fusion(
        {"sparse": sparse, "dense": dense}, {"sparse": 0.3, "dense": 1.0}
    )

    assert sparse_heavy[0].chunk_id == a
    assert dense_heavy[0].chunk_id == c


# --- The pipeline ------------------------------------------------------------------------------


class FixedRetriever:
    def __init__(self, ranking: list[RankedChunk], *, fail: bool = False) -> None:
        self.ranking = ranking
        self.fail = fail
        self.calls: list[int] = []

    async def search(self, query: str, limit: int) -> list[RankedChunk]:
        self.calls.append(limit)
        if self.fail:
            raise ConnectionError("vector store down")
        return self.ranking[:limit]


class Passages:
    def __init__(self, texts: dict[uuid.UUID, str]) -> None:
        self.texts = texts

    async def load(self, chunk_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        return {i: self.texts[i] for i in chunk_ids if i in self.texts}


def engine(
    sparse: FixedRetriever, dense: FixedRetriever, texts: dict[uuid.UUID, str], **config: object
) -> RetrievalEngine:
    return RetrievalEngine(
        sparse=sparse,
        dense=dense,
        passages=Passages(texts),
        reranker=OverlapReranker(),
        config=RetrievalConfig(**config),  # type: ignore[arg-type]
    )


async def test_fixed_strategies_use_only_their_retriever() -> None:
    a, b = ids(2)
    sparse = FixedRetriever([RankedChunk(a, 3.0)])
    dense = FixedRetriever([RankedChunk(b, 0.7)])
    texts = {a: "alpha", b: "beta"}

    outcome = await engine(sparse, dense, texts, rerank=False).retrieve(
        "anything", RequestedStrategy.SPARSE
    )

    assert dense.calls == []
    assert [c.chunk_id for c in outcome.selected] == [a]
    assert outcome.weights == {"sparse": 1.0, "dense": 0.0}


async def test_reranking_reorders_the_fused_head() -> None:
    a, b, c = ids(3)
    sparse = FixedRetriever([RankedChunk(a, 3.0), RankedChunk(b, 2.0)])
    dense = FixedRetriever([RankedChunk(a, 0.9), RankedChunk(c, 0.8)])
    texts = {a: "shipping times", b: "reset the router button", c: "router lights"}

    outcome = await engine(sparse, dense, texts, top_k=2).retrieve(
        "reset router button", RequestedStrategy.HYBRID
    )

    assert outcome.candidates[0].fused_rank > 1  # not the fusion winner...
    assert [c.chunk_id for c in outcome.selected] == [b, c]  # ...but the most relevant
    assert outcome.selected[0].rerank_score == pytest.approx(1.0)
    assert outcome.reranker == "test/word-overlap"
    assert {"analysis", "sparse", "dense", "fusion", "rerank", "total"} <= set(outcome.timings_ms)


async def test_stale_vector_hits_are_dropped() -> None:
    live, gone = ids(2)
    dense = FixedRetriever([RankedChunk(gone, 0.99), RankedChunk(live, 0.5)])

    outcome = await engine(FixedRetriever([]), dense, {live: "still here"}, rerank=False).retrieve(
        "x", RequestedStrategy.DENSE
    )

    assert [c.chunk_id for c in outcome.candidates] == [live]
    assert outcome.candidates[0].fused_rank == 1


async def test_a_failing_retriever_degrades_instead_of_failing() -> None:
    a = ids(1)[0]
    sparse = FixedRetriever([RankedChunk(a, 2.0)])
    dense = FixedRetriever([], fail=True)

    outcome = await engine(sparse, dense, {a: "text"}).retrieve("query", RequestedStrategy.HYBRID)

    assert [c.chunk_id for c in outcome.selected] == [a]
    assert "ConnectionError" in outcome.failures["dense"]


async def test_all_retrievers_failing_is_an_error() -> None:
    dense = FixedRetriever([], fail=True)

    with pytest.raises(RetrievalUnavailableError):
        await engine(FixedRetriever([]), dense, {}).retrieve("q", RequestedStrategy.DENSE)


async def test_auto_widens_the_pool_for_ambiguous_queries() -> None:
    sparse, dense = FixedRetriever([]), FixedRetriever([])

    outcome = await engine(sparse, dense, {}, sparse_top_k=20, dense_top_k=20).retrieve(
        "billing", RequestedStrategy.AUTO
    )

    assert outcome.analysis.query_type is QueryType.AMBIGUOUS
    assert sparse.calls == [40] and dense.calls == [40]


def test_relevance_maps_logits_to_probabilities() -> None:
    assert relevance(0.0) == pytest.approx(0.5)
    assert relevance(8.0) > 0.99 and relevance(-8.0) < 0.01
