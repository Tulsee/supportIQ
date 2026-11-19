"""Grouping unanswered questions into gaps.

Questions are compared by the cosine similarity of their embeddings. New questions
first join the most similar existing gap (its centroid), so gaps are stable across
analyses; the rest are grouped among themselves by average-linkage agglomerative
clustering: the two groups whose questions are most similar on average are merged,
until no two groups reach the threshold. Average linkage (rather than single
linkage) keeps a chain of loosely related questions from collapsing into one gap.

Everything is deterministic for the same input order.
"""

import math
import re
from collections.abc import Sequence

Vector = Sequence[float]

_SPACE = re.compile(r"\s+")
_TRAILING = " ?.!,;:"
MAX_KEY = 500


def query_key(query: str) -> str:
    """The identity of a question: case, spacing and trailing punctuation ignored."""
    return _SPACE.sub(" ", query.casefold()).strip().strip(_TRAILING).strip()[:MAX_KEY]


def cosine(a: Vector, b: Vector) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def centroid(vectors: Sequence[Vector], weights: Sequence[float] | None = None) -> list[float]:
    """The (weighted) mean of ``vectors``, normalized to unit length."""
    if not vectors:
        raise ValueError("No vectors")
    weights = weights or [1.0] * len(vectors)
    size = len(vectors[0])
    total = [0.0] * size
    for vector, weight in zip(vectors, weights, strict=True):
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        for i in range(size):
            total[i] += weight * vector[i] / norm
    norm = math.sqrt(sum(x * x for x in total)) or 1.0
    return [x / norm for x in total]


def assign(
    vectors: Sequence[Vector], centroids: Sequence[Vector], threshold: float
) -> list[int | None]:
    """For each vector, the index of the most similar centroid if it reaches
    ``threshold``, else None. Ties go to the first centroid."""
    result: list[int | None] = []
    for vector in vectors:
        best, best_score = None, threshold
        for index, c in enumerate(centroids):
            score = cosine(vector, c)
            if score >= best_score and (best is None or score > best_score):
                best, best_score = index, score
        result.append(best)
    return result


def cluster(vectors: Sequence[Vector], threshold: float) -> list[list[int]]:
    """Average-linkage agglomerative clustering. Returns groups of indices into
    ``vectors``, each sorted, ordered by their first index. O(n^3) in the worst case:
    callers keep n to a few hundred."""
    n = len(vectors)
    if n == 0:
        return []
    similarity = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            similarity[i][j] = similarity[j][i] = cosine(vectors[i], vectors[j])

    groups: dict[int, list[int]] = {i: [i] for i in range(n)}
    # Average similarity between groups, keyed by (smaller id, larger id).
    linkage: dict[tuple[int, int], float] = {
        (i, j): similarity[i][j] for i in range(n) for j in range(i + 1, n)
    }
    while linkage:
        (a, b), best = max(linkage.items(), key=lambda item: (item[1], -item[0][0], -item[0][1]))
        if best < threshold:
            break
        size_a, size_b = len(groups[a]), len(groups[b])
        groups[a] = sorted(groups[a] + groups.pop(b))
        # Lance-Williams update for average linkage.
        for other in groups:
            if other == a:
                continue
            with_a = linkage.pop((min(a, other), max(a, other)))
            with_b = linkage.pop((min(b, other), max(b, other)))
            linkage[(min(a, other), max(a, other))] = (size_a * with_a + size_b * with_b) / (
                size_a + size_b
            )
        linkage.pop((min(a, b), max(a, b)), None)
    return sorted(groups.values(), key=lambda g: g[0])


def representative(members: Sequence[int], asked: Sequence[int], vectors: Sequence[Vector]) -> int:
    """The question that names a gap: the most asked, then the one closest to the
    others (the medoid), then the first."""
    if not members:
        raise ValueError("No members")
    most = max(asked[i] for i in members)
    top = [i for i in members if asked[i] == most]
    if len(top) == 1:
        return top[0]
    return max(
        top,
        key=lambda i: (sum(cosine(vectors[i], vectors[j]) for j in members if j != i), -i),
    )
