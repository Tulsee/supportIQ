"""The neighbour signal: past tickets whose category a person set or confirmed vote
for the new ticket's category, weighted by how similar they are (cosine similarity
of embeddings of subject and opening message)."""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Neighbour:
    index: int  # position in the labelled examples
    category: str
    similarity: float


@dataclass(frozen=True)
class NeighbourVote:
    shares: dict[str, float]  # category -> share of the weighted vote (sums to 1)
    neighbours: tuple[Neighbour, ...]

    @property
    def top(self) -> str | None:
        return max(self.shares, key=lambda c: (self.shares[c], c)) if self.shares else None

    @property
    def confidence(self) -> float:
        return self.shares[self.top] if self.top else 0.0


def vote(
    query: Sequence[float],
    examples: Sequence[Sequence[float]],
    categories: Sequence[str],
    *,
    k: int = 10,
    min_similarity: float = 0.5,
    min_neighbours: int = 2,
) -> NeighbourVote | None:
    """The ``k`` most similar labelled examples at or above ``min_similarity`` vote,
    each with its similarity as weight. None when fewer than ``min_neighbours`` are
    close enough: no signal rather than a weak one."""
    if len(examples) == 0:
        return None
    matrix = np.asarray(examples, dtype=np.float32)
    vector = np.asarray(query, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1) * (np.linalg.norm(vector) or 1.0)
    similarities = (matrix @ vector) / np.where(norms == 0, 1.0, norms)
    order = np.argsort(-similarities)[:k]
    chosen = [int(i) for i in order if similarities[i] >= min_similarity]
    if len(chosen) < min_neighbours:
        return None
    weights: dict[str, float] = {}
    for i in chosen:
        weights[categories[i]] = weights.get(categories[i], 0.0) + float(similarities[i])
    total = sum(weights.values())
    return NeighbourVote(
        shares={category: weight / total for category, weight in weights.items()},
        neighbours=tuple(Neighbour(i, categories[i], float(similarities[i])) for i in chosen),
    )
