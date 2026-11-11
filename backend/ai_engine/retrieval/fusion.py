"""Reciprocal Rank Fusion (Cormack et al., 2009), with per-list weights.

    RRF(d) = sum over rankings r of  w_r / (k + rank_r(d))

Only ranks matter, so lists with incomparable scores (BM25 and cosine similarity)
combine without normalization. ``k`` (default 60) damps the influence of the very
top positions; weights let one list dominate (sparse- or dense-heavy retrieval).
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RankedChunk:
    chunk_id: uuid.UUID
    score: float  # the retriever's own score (BM25, cosine similarity, ...)


@dataclass
class FusedChunk:
    chunk_id: uuid.UUID
    score: float = 0.0
    # Per source list: (1-based rank, the retriever's score).
    sources: dict[str, tuple[int, float]] = field(default_factory=dict)


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[RankedChunk]],
    weights: Mapping[str, float],
    *,
    k: int = 60,
) -> list[FusedChunk]:
    """Fuse ranked lists into one, best first. Ties keep first-seen order."""
    fused: dict[uuid.UUID, FusedChunk] = {}
    for name, ranking in rankings.items():
        weight = weights.get(name, 1.0)
        for rank, item in enumerate(ranking, start=1):
            entry = fused.setdefault(item.chunk_id, FusedChunk(item.chunk_id))
            entry.sources[name] = (rank, item.score)
            entry.score += weight / (k + rank)
    return sorted(fused.values(), key=lambda entry: entry.score, reverse=True)
