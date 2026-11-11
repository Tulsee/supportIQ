"""Token-aware chunking (no database, no model)."""

from itertools import pairwise

import pytest

from ai_engine.chunking.chunker import ChunkingConfig, chunk_blocks, join_blocks
from ai_engine.chunking.tokenizer import WordTokenCounter
from ai_engine.ingestion.extract import TextBlock

COUNTER = WordTokenCounter()


def sentences(prefix: str, n: int) -> str:
    return " ".join(f"{prefix} sentence number {i} is here." for i in range(n))


def test_chunks_stay_within_the_token_budget_and_slice_the_text_exactly() -> None:
    blocks = [TextBlock(sentences("Alpha", 40))]
    config = ChunkingConfig(size=50, overlap=10)

    chunks = chunk_blocks(blocks, COUNTER, config)
    text = join_blocks(blocks)

    assert len(chunks) > 3
    assert all(chunk.token_count <= config.size for chunk in chunks)
    assert all(text[chunk.start : chunk.end] == chunk.text for chunk in chunks)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    assert chunks[0].start == 0 and chunks[-1].end == len(text)  # nothing dropped


def test_consecutive_chunks_overlap_by_whole_sentences() -> None:
    chunks = chunk_blocks([TextBlock(sentences("Beta", 30))], COUNTER, ChunkingConfig(50, 10))

    for previous, current in pairwise(chunks):
        assert current.start < previous.end  # overlap exists...
        overlap = previous.text[current.start - previous.start :]
        assert COUNTER.count([overlap])[0] <= 10  # ...within budget
        assert overlap.startswith("Beta")  # ...and starts at a sentence


def test_chunks_never_span_sections() -> None:
    blocks = [
        TextBlock("Refunds", heading="Refunds", is_heading=True),
        TextBlock(sentences("Refund", 12), heading="Refunds"),
        TextBlock("Shipping", heading="Shipping", is_heading=True),
        TextBlock(sentences("Ship", 12), heading="Shipping"),
    ]

    chunks = chunk_blocks(blocks, COUNTER, ChunkingConfig(size=40, overlap=8))

    for chunk in chunks:
        assert ("Refund" in chunk.text) != ("Ship" in chunk.text), chunk.text
        assert chunk.heading == ("Refunds" if "Refund" in chunk.text else "Shipping")
    # The heading opens its section's first chunk.
    assert chunks[0].text.startswith("Refunds")


def test_page_range_is_tracked_across_pages() -> None:
    blocks = [TextBlock("Short first page.", page=1), TextBlock("Second page text.", page=2)]

    [chunk] = chunk_blocks(blocks, COUNTER, ChunkingConfig(size=100, overlap=10))

    assert (chunk.page, chunk.page_end) == (1, 2)


def test_an_oversized_sentence_is_split_at_token_boundaries() -> None:
    long_sentence = " ".join(f"word{i}" for i in range(130))

    chunks = chunk_blocks([TextBlock(long_sentence)], COUNTER, ChunkingConfig(50, 10))

    assert [chunk.token_count for chunk in chunks] == [50, 50, 30]
    assert " ".join(chunk.text for chunk in chunks) == long_sentence


def test_overlap_must_be_smaller_than_size() -> None:
    with pytest.raises(ValueError, match="overlap"):
        ChunkingConfig(size=10, overlap=10)
