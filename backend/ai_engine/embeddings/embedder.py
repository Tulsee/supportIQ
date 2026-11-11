"""Embedding models behind one small interface.

``FastEmbedEmbedder`` runs BGE (or any FastEmbed model) on ONNX Runtime: the same
model weights as sentence-transformers, without PyTorch. The model loads lazily,
once per process, and its own tokenizer measures chunk sizes.

``HashingEmbedder`` is deterministic and model-free (texts sharing words get
similar vectors), for tests.
"""

import hashlib
import math
import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ai_engine.chunking.tokenizer import HuggingFaceTokenCounter, TokenCounter, WordTokenCounter

if TYPE_CHECKING:
    from fastembed import TextEmbedding


class Embedder(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages (chunks). CPU-bound: call from a worker thread."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query (models may add a query instruction)."""
        ...

    def token_counter(self) -> TokenCounter:
        """The tokenizer the model uses, for measuring chunks."""
        ...


class FastEmbedEmbedder:
    def __init__(
        self,
        model_name: str,
        *,
        cache_dir: Path,
        query_prefix: str = "",
        batch_size: int = 32,
    ) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._query_prefix = query_prefix
        self._batch_size = batch_size
        self._model: TextEmbedding | None = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        from fastembed import TextEmbedding

        for description in TextEmbedding.list_supported_models():
            if description["model"] == self._model_name:
                return int(description["dim"])
        raise ValueError(f"Unsupported embedding model: {self._model_name}")

    def _load(self) -> "TextEmbedding":
        with self._lock:  # first use may come from several threads at once
            if self._model is None:
                from fastembed import TextEmbedding

                self._cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = TextEmbedding(self._model_name, cache_dir=str(self._cache_dir))
            return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        return [vector.tolist() for vector in model.embed(list(texts), batch_size=self._batch_size)]

    def embed_query(self, text: str) -> list[float]:
        model = self._load()
        return next(iter(model.query_embed(self._query_prefix + text))).tolist()

    def token_counter(self) -> TokenCounter:
        # FastEmbed keeps the model's Hugging Face tokenizer on the ONNX model object.
        tokenizer = getattr(self._load().model, "tokenizer", None)
        if tokenizer is None:
            raise RuntimeError(f"No tokenizer available for {self._model_name}")
        return HuggingFaceTokenCounter(tokenizer)


_WORDS = re.compile(r"\w+")


class HashingEmbedder:
    """Bag-of-words feature hashing, L2-normalized. Deterministic and fast."""

    def __init__(self, dimensions: int = 64, model_name: str = "test/hashing-embedder") -> None:
        self._dimensions = dimensions
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for word in _WORDS.findall(text.lower()):
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            vector[int.from_bytes(digest[:4], "big") % self._dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def token_counter(self) -> TokenCounter:
        return WordTokenCounter()
