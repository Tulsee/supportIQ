"""The real models: BAAI/bge-small-en-v1.5 embeddings and the ms-marco MiniLM
cross-encoder (both via FastEmbed).

Downloads them (~67 MB and ~80 MB) on first run into MODEL_CACHE_DIR; CI caches them.
"""

import math

import pytest

from ai_engine.chunking.chunker import ChunkingConfig, chunk_blocks
from ai_engine.embeddings.embedder import FastEmbedEmbedder
from ai_engine.ingestion.extract import TextBlock
from ai_engine.reranking.cross_encoder import FastEmbedReranker
from app.core.config import settings


@pytest.fixture(scope="module")
def model() -> FastEmbedEmbedder:
    return FastEmbedEmbedder(
        settings.embedding_model,
        cache_dir=settings.model_cache_dir,
        query_prefix=settings.embedding_query_prefix,
    )


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    )


def test_embeddings_rank_relevant_passages_first(model: FastEmbedEmbedder) -> None:
    passages = [
        "To reset the router, hold the reset button for ten seconds.",
        "Refunds are issued to the original payment method within five days.",
    ]

    vectors = model.embed_documents(passages)
    query = model.embed_query("how do I get my money back")

    assert model.dimensions == len(vectors[0]) == 384
    assert cosine(query, vectors[1]) > cosine(query, vectors[0])


def test_cross_encoder_prefers_the_passage_that_answers() -> None:
    reranker = FastEmbedReranker(settings.reranker_model, cache_dir=settings.model_cache_dir)
    query = "how long does a refund take"
    passages = [
        "You can request a refund within 30 days of purchase using the refund form.",
        "Refunds go back to the original payment method and take five business days.",
        "Our warehouse ships worldwide from Rotterdam.",
    ]

    scores = reranker.score(query, passages)

    # Both refund passages are on topic; the one answering "how long" ranks first,
    # and the off-topic passage is far behind (scores are logits).
    assert scores[1] > scores[0] > scores[2]
    assert scores[0] - scores[2] > 5


def test_chunks_fit_the_model_context(model: FastEmbedEmbedder) -> None:
    """Measured with the model's own tokenizer, chunks never exceed its 512-token
    window, so nothing is silently truncated when embedded."""
    counter = model.token_counter()
    text = " ".join(f"Paragraph {i} explains configuration step {i} in detail." for i in range(400))

    chunks = chunk_blocks(
        [TextBlock(text)],
        counter,
        ChunkingConfig(settings.chunk_size_tokens, settings.chunk_overlap_tokens),
    )

    assert len(chunks) > 3
    assert max(chunk.token_count for chunk in chunks) <= settings.chunk_size_tokens <= 510
    # WordPiece ("support", "##iq"), without the [CLS]/[SEP] the model adds itself.
    assert counter.count(["SupportIQ"]) == [2]
