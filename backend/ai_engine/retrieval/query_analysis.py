"""Rule-based query analysis: which retrieval strategy suits a query.

Deliberately transparent: every decision lists the rules that fired, so a run can
be inspected and the rules compared against fixed strategies in experiments. A
learned selector can replace this later if there is enough labelled data.

    Query type          Strategy       Fusion weights (sparse, dense)
    exact identifier    sparse-heavy   1.0, 0.3   error codes, IDs, quoted phrases
    terminology         hybrid         1.0, 1.0   short keyword queries, product names
    natural question    dense-heavy    0.3, 1.0   questions in plain language
    policy              hybrid         1.0, 1.0   rules, eligibility, refunds, warranty
    ambiguous           hybrid, wider  1.0, 1.0   one or two generic words
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum


class QueryType(StrEnum):
    EXACT_IDENTIFIER = "exact_identifier"
    TERMINOLOGY = "terminology"
    NATURAL_QUESTION = "natural_question"
    POLICY = "policy"
    AMBIGUOUS = "ambiguous"


class Strategy(StrEnum):
    """What actually ran. ``auto`` resolves to one of the weighted ones."""

    SPARSE = "sparse"  # BM25 only
    DENSE = "dense"  # vector search only
    HYBRID = "hybrid"  # both, equal weight
    SPARSE_HEAVY = "sparse_heavy"
    DENSE_HEAVY = "dense_heavy"


WEIGHTS: dict[Strategy, tuple[float, float]] = {
    Strategy.SPARSE: (1.0, 0.0),
    Strategy.DENSE: (0.0, 1.0),
    Strategy.HYBRID: (1.0, 1.0),
    Strategy.SPARSE_HEAVY: (1.0, 0.3),
    Strategy.DENSE_HEAVY: (0.3, 1.0),
}


@dataclass(frozen=True)
class QueryAnalysis:
    query_type: QueryType
    strategy: Strategy
    reasons: list[str] = field(default_factory=list)
    # Ambiguous queries look further down each list before fusing.
    candidate_multiplier: int = 1

    @property
    def weights(self) -> tuple[float, float]:
        return WEIGHTS[self.strategy]


_QUOTED = re.compile(r'"[^"]{2,}"')
# Codes: E-4012, AB-12-345 (separator), 0x80070005 (hex), E4012/ERR404 (error-style
# prefix), 503 (3+ digits). A bare "X200" is a product name, not a code, unless the
# query talks about an error or code.
_ERROR_CODE = re.compile(
    r"\b(?:[A-Za-z]{1,6}[-_]\d{2,}[A-Za-z0-9-]*|0x[0-9A-Fa-f]{2,}"
    r"|(?:e|err|error|ex|code)\d{2,}|\d{3,}(?:-\d+)*)\b",
    re.IGNORECASE,
)
_ERROR_CONTEXT = {"error", "errors", "code", "codes", "fault", "exception", "status"}
# Letters and digits mixed: X200, v2.3, SKU12 (product and version names).
_IDENTIFIER = re.compile(r"\b(?=[\w.-]*\d)(?=[\w.-]*[A-Za-z])[A-Za-z0-9][\w.-]+\b")
_WORD = re.compile(r"[A-Za-z0-9][\w'-]*")
_QUESTION_WORDS = {
    "how", "what", "why", "when", "where", "who", "which", "can", "could", "should",
    "is", "are", "does", "do", "did", "will", "would", "may", "might",
}  # fmt: skip
_POLICY_WORDS = {
    "policy", "policies", "allowed", "eligible", "eligibility", "refund", "refunds",
    "return", "returns", "warranty", "terms", "rule", "rules", "permitted",
    "entitled", "guarantee", "cancel", "cancellation", "compensation", "liable",
}  # fmt: skip
_STOP_WORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "my", "i", "it",
    "is", "be", "with", "this", "that", "me", "you", "your", "our", "we", "at", "by",
}  # fmt: skip


def analyze(query: str) -> QueryAnalysis:
    words = [w.lower() for w in _WORD.findall(query)]
    content_words = [w for w in words if w not in _STOP_WORDS and w not in _QUESTION_WORDS]
    is_question = query.strip().endswith("?") or (bool(words) and words[0] in _QUESTION_WORDS)

    if quoted := _QUOTED.findall(query):
        return QueryAnalysis(
            QueryType.EXACT_IDENTIFIER,
            Strategy.SPARSE_HEAVY,
            [f"quoted phrase {quoted[0]}: exact wording matters"],
        )
    code = _ERROR_CODE.search(query)
    if code is None and set(words) & _ERROR_CONTEXT:
        code = _IDENTIFIER.search(query)  # "X200 error": the identifier is the code
    if code:
        return QueryAnalysis(
            QueryType.EXACT_IDENTIFIER,
            Strategy.SPARSE_HEAVY,
            [f"identifier or code “{code.group()}”: keyword matching finds it exactly"],
        )
    if policy := sorted(set(content_words) & _POLICY_WORDS):
        return QueryAnalysis(
            QueryType.POLICY,
            Strategy.HYBRID,
            [f"policy terms ({', '.join(policy)}): needs exact terms and their meaning"],
        )
    if is_question and len(words) >= 4:
        return QueryAnalysis(
            QueryType.NATURAL_QUESTION,
            Strategy.DENSE_HEAVY,
            ["phrased as a question: meaning matters more than exact words"],
        )
    if product := _IDENTIFIER.search(query):
        return QueryAnalysis(
            QueryType.TERMINOLOGY,
            Strategy.HYBRID,
            [f"product or version name “{product.group()}”: exact term plus context"],
        )
    if len(content_words) <= 2:
        return QueryAnalysis(
            QueryType.AMBIGUOUS,
            Strategy.HYBRID,
            ["one or two generic words: search both ways and look deeper"],
            candidate_multiplier=2,
        )
    if len(words) >= 6:
        return QueryAnalysis(
            QueryType.NATURAL_QUESTION,
            Strategy.DENSE_HEAVY,
            ["a longer description in plain language: meaning matters most"],
        )
    return QueryAnalysis(
        QueryType.TERMINOLOGY,
        Strategy.HYBRID,
        ["a short keyword query: exact terms and meaning both help"],
    )
