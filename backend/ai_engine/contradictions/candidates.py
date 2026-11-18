"""Candidate pairs: passages from different documents that are about the same thing.

Comparing every pair of passages grows with the square of the knowledge base, and
nearly all pairs are unrelated. Two passages can only contradict each other if they
talk about the same subject, so each passage is paired with its nearest neighbours
(cosine similarity of the knowledge base's embeddings) in *other* documents.
"""

import hashlib
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Passage:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str


@dataclass(frozen=True)
class Neighbour:
    chunk_id: uuid.UUID
    similarity: float


@dataclass(frozen=True)
class CandidatePair:
    """Two passages, ordered by chunk id so a pair has one identity whichever side
    found the other."""

    a: Passage
    b: Passage
    similarity: float
    key: str  # identifies the two texts, see ``pair_key``


def content_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def pair_key(text_a: str, text_b: str) -> str:
    """The same two texts give the same key in either order, and any edit to either
    gives a new one: a pair is judged once, and again only when a passage changes."""
    first, second = sorted((content_hash(text_a), content_hash(text_b)))
    return hashlib.sha256(f"{first}:{second}".encode()).hexdigest()


def select_pairs(
    passages: Sequence[Passage],
    neighbours: Mapping[uuid.UUID, Iterable[Neighbour]],
    *,
    per_passage: int,
    min_similarity: float,
) -> list[CandidatePair]:
    """For each passage, up to ``per_passage`` of its nearest passages in other
    documents at or above ``min_similarity``; each pair once, most similar first.

    ``neighbours`` maps a chunk id to its nearest chunks, best first (from any
    document: same-document hits and unknown chunks are skipped here)."""
    by_id = {p.chunk_id: p for p in passages}
    pairs: dict[tuple[uuid.UUID, uuid.UUID], CandidatePair] = {}
    for passage in passages:
        taken = 0
        for hit in neighbours.get(passage.chunk_id, ()):
            if taken >= per_passage or hit.similarity < min_similarity:
                break
            other = by_id.get(hit.chunk_id)
            if other is None or other.document_id == passage.document_id:
                continue
            taken += 1
            a, b = sorted((passage, other), key=lambda p: str(p.chunk_id))
            ids = (a.chunk_id, b.chunk_id)
            if ids not in pairs:
                pairs[ids] = CandidatePair(a, b, hit.similarity, pair_key(a.content, b.content))
    return sorted(pairs.values(), key=lambda p: (-p.similarity, p.key))
