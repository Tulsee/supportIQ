"""Evidence signals computed before the model is asked (cheap, no model call).

- relevance: how well the best passage matches the question (cross-encoder, 0-1)
- coverage: the share of the question's content words found in the passages
- vagueness: a question with no specific subject ("How do I reset it?")
"""

import re
from collections.abc import Sequence

from ai_engine.reranking.cross_encoder import relevance as logit_to_relevance

_WORD = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")
_SPECIFIC = re.compile(r"\d|\b[A-Za-z]{1,4}-\d")  # numbers, codes like E-204, C-12
STOPWORDS = frozenset(
    [
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "me",
        "more",
        "most",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "that's",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
        "i'm",
        "i've",
        "can't",
        "don't",
        "won't",
        "isn't",
        "it's",
        "what's",
        "there's",
        "let",
        "let's",
        "please",
    ]
)
QUESTION_WORDS = frozenset(
    [
        "how",
        "what",
        "why",
        "when",
        "where",
        "which",
        "who",
        "whose",
        "much",
        "many",
        "long",
        "far",
        "often",
    ]
)
# Verbs and fillers that don't name what the question is about.
GENERIC = frozenset(
    [
        "mean",
        "means",
        "meant",
        "have",
        "has",
        "get",
        "gets",
        "got",
        "work",
        "works",
        "need",
        "needs",
        "use",
        "used",
        "using",
        "make",
        "made",
        "know",
        "tell",
        "supported",
        "support",
        "available",
        "possible",
        "ok",
        "okay",
        "thing",
        "things",
        "stuff",
        "happen",
        "happens",
        "something",
        "anything",
        "someone",
    ]
)


def terms(text: str) -> list[str]:
    """Lower-case words without stopwords (codes and numbers kept)."""
    words = _WORD.findall(text.lower())
    return [w for w in words if w not in STOPWORDS and (len(w) > 2 or any(c.isdigit() for c in w))]


def stem(word: str) -> str:
    """A crude English stem, enough to match "refunds" with "refund"."""
    word = word.removesuffix("'s")
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def content_terms(question: str) -> list[str]:
    return [t for t in terms(question) if t not in QUESTION_WORDS and t not in GENERIC]


def top_relevance(rerank_scores: Sequence[float | None]) -> tuple[float | None, float | None]:
    """(best, mean of the best three) cross-encoder relevance, 0-1."""
    values = sorted((logit_to_relevance(s) for s in rerank_scores if s is not None), reverse=True)
    if not values:
        return None, None
    top = values[:3]
    return values[0], sum(top) / len(top)


def coverage(question: str, passages: Sequence[str]) -> tuple[float, list[str]]:
    """Share of the question's content words that appear in the passages (stems, or a
    shared five-letter prefix), and the words that don't."""
    wanted = list(dict.fromkeys(stem(t) for t in content_terms(question)))
    if not wanted:
        return 1.0, []
    found = {stem(t) for passage in passages for t in terms(passage)}
    prefixes = {w[:5] for w in found if len(w) >= 5}
    missing = [w for w in wanted if w not in found and not (len(w) >= 5 and w[:5] in prefixes)]
    return 1 - len(missing) / len(wanted), missing


def is_specific(question: str) -> bool:
    """Names something: a number or code, or a capitalized name after the first word."""
    if _SPECIFIC.search(question):
        return True
    words = question.split()[1:]
    return any(w[:1].isupper() and w.strip("?.!,") not in ("I", "I'm", "I've") for w in words)


PRONOUNS = frozenset(["it", "this", "that", "these", "those", "they", "them", "one"])


def vagueness(question: str) -> tuple[bool, list[str]]:
    """A question whose only subject is a pronoun ("Is it covered?", "How do I reset
    it?") or that has no content words at all ("How long do I have?"), and names
    nothing specific. Subtler ambiguity is left to the model's own report."""
    content = content_terms(question)
    words = set(_WORD.findall(question.lower()))
    vague = not is_specific(question) and (
        not content or (bool(words & PRONOUNS) and len(content) <= 1)
    )
    return vague, content
