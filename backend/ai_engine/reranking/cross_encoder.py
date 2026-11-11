"""Cross-encoder reranking.

First-stage retrievers score the query and each passage separately (BM25 term
statistics, or two independent embeddings). A cross-encoder reads the query and a
passage *together*, which judges relevance much better but costs a model pass per
pair, so it only reorders the top candidates.

Default model: ms-marco-MiniLM-L-6-v2 (trained on MS MARCO passage ranking), run
by FastEmbed on ONNX Runtime. Scores are logits; ``relevance()`` maps them to 0-1.
"""

import math
import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from fastembed.rerank.cross_encoder import TextCrossEncoder


class Reranker(Protocol):
    @property
    def model_name(self) -> str: ...

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        """Relevance of each passage to the query (higher is better). CPU-bound."""
        ...


def relevance(score: float) -> float:
    """Logit -> probability-like 0-1 value, for display."""
    return 1 / (1 + math.exp(-score))


class FastEmbedReranker:
    def __init__(self, model_name: str, *, cache_dir: Path, batch_size: int = 16) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._batch_size = batch_size
        self._model: TextCrossEncoder | None = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    def _load(self) -> "TextCrossEncoder":
        with self._lock:
            if self._model is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                self._cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = TextCrossEncoder(self._model_name, cache_dir=str(self._cache_dir))
            return self._model

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        model = self._load()
        return [float(s) for s in model.rerank(query, list(passages), batch_size=self._batch_size)]


_WORDS = re.compile(r"\w+")


class OverlapReranker:
    """Model-free stand-in for tests: the share of query words in the passage."""

    model_name = "test/word-overlap"

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        wanted = set(_WORDS.findall(query.lower()))
        return [
            len(wanted & set(_WORDS.findall(passage.lower()))) / max(len(wanted), 1)
            for passage in passages
        ]
