"""Token counting. Chunks are measured with the embedding model's own tokenizer,
so "480 tokens" means exactly what the model will see."""

import re
from collections.abc import Sequence
from typing import Protocol

from tokenizers import Tokenizer


class TokenCounter(Protocol):
    def count(self, texts: Sequence[str]) -> list[int]:
        """Number of tokens in each text (without special tokens)."""
        ...

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        """Character span of each token, for splitting text at token boundaries."""
        ...


class HuggingFaceTokenCounter:
    def __init__(self, tokenizer: Tokenizer) -> None:
        # A private copy without truncation or padding: count real lengths, and
        # never mutate the tokenizer the embedding model itself uses.
        self._tokenizer = Tokenizer.from_str(tokenizer.to_str())
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()

    def count(self, texts: Sequence[str]) -> list[int]:
        if not texts:
            return []
        encodings = self._tokenizer.encode_batch(list(texts), add_special_tokens=False)
        return [len(encoding.ids) for encoding in encodings]

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        return list(self._tokenizer.encode(text, add_special_tokens=False).offsets)


_WORD = re.compile(r"\w+|[^\w\s]")


class WordTokenCounter:
    """Words and punctuation marks as tokens. For tests and model-free tooling."""

    def count(self, texts: Sequence[str]) -> list[int]:
        return [len(_WORD.findall(text)) for text in texts]

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        return [match.span() for match in _WORD.finditer(text)]
