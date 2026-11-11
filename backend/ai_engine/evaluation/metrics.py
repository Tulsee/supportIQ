"""Evaluation metrics.

Retrieval is scored against the labelled evidence quotes (a passage counts as
relevant if it contains a quote), so the labels don't depend on chunking. Answer
behaviour is scored against the labelled action:

- coverage: answerable questions that got an answer (useful answer coverage)
- correctness: answers to answerable questions that state every key fact
- abstention precision / recall: abstentions that were warranted / unanswerable
  questions that were declined
- unwarranted answers: questions that should not be answered outright (ambiguous,
  conflicting, insufficient, unanswerable) that were answered anyway
- unsupported answers: answers that were unwarranted or missed a key fact; the
  headline number Module 5 sets out to reduce

Key-fact matching is a cheap proxy for faithfulness: it catches wrong and missing
facts, not unsupported extra claims. Rates carry 95% Wilson score intervals.
"""

import math
import re
import statistics
import unicodedata
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ai_engine.evaluation.dataset import Question

# Answer statuses (app.models.AnswerStatus) -> the action the system took.
_ACTION_OF_STATUS = {
    "answered": "answer",
    "uncited": "answer",
    "declined": "abstain",
    "no_passages": "abstain",
    "failed": "error",
    # Module 5 adds clarify and escalate decisions.
    "clarify": "clarify",
    "escalate": "escalate",
}

_CITATION = re.compile(r"\[\d+\]")
_PUNCTUATION = str.maketrans(
    {
        "\N{RIGHT SINGLE QUOTATION MARK}": "'",
        "\N{LEFT SINGLE QUOTATION MARK}": "'",
        "\N{LEFT DOUBLE QUOTATION MARK}": '"',
        "\N{RIGHT DOUBLE QUOTATION MARK}": '"',
        "\N{EN DASH}": "-",
        "\N{EM DASH}": "-",
        "\N{NO-BREAK SPACE}": " ",
    }
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_PUNCTUATION)
    return " ".join(_CITATION.sub(" ", text).casefold().split())


def contains(passage: str, quote: str) -> bool:
    return normalize(quote) in normalize(passage)


@dataclass
class QuestionOutcome:
    """What the pipeline did with one question."""

    question: Question
    ranked_passages: list[str]  # final order: selected passages, then other candidates
    top_k: int
    run_id: str
    latency_ms: float
    status: str | None = None  # answer status; None for retrieval-only runs
    answer: str | None = None
    cited_passages: list[str] = field(default_factory=list)  # distinct, in citation order
    invalid_citations: int = 0
    generation_ms: float | None = None
    error: str | None = None
    model: str | None = None
    model_version: str | None = None

    @property
    def action(self) -> str | None:
        return _ACTION_OF_STATUS.get(self.status) if self.status else None

    @property
    def evidence_rank(self) -> int | None:
        """1-based rank of the first passage containing any evidence quote."""
        quotes = [e.quote for e in self.question.evidence]
        for rank, passage in enumerate(self.ranked_passages, start=1):
            if any(contains(passage, quote) for quote in quotes):
                return rank
        return None

    def evidence_recall(self, k: int) -> float:
        """Share of the evidence quotes found in the top k passages."""
        quotes = [e.quote for e in self.question.evidence]
        top = self.ranked_passages[:k]
        found = sum(any(contains(p, quote) for p in top) for quote in quotes)
        return found / len(quotes)

    @property
    def missing_facts(self) -> list[str]:
        """Key facts the answer doesn't state (the first alternative of each)."""
        text = normalize(self.answer or "")
        return [
            alternatives[0]
            for alternatives in self.question.key_facts
            if not any(normalize(a) in text for a in alternatives)
        ]

    @property
    def correct(self) -> bool:
        return self.action == "answer" and not self.missing_facts

    @property
    def relevant_citations(self) -> int:
        quotes = [e.quote for e in self.question.evidence]
        return sum(any(contains(p, q) for q in quotes) for p in self.cited_passages)

    @property
    def unsupported(self) -> bool:
        """An answer that should not have been given, or that misses a key fact."""
        if self.action != "answer":
            return False
        return not self.question.answerable or bool(self.missing_facts)


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4)


def rate(successes: int, n: int) -> dict[str, Any]:
    return {
        "value": round(successes / n, 4) if n else None,
        "successes": successes,
        "n": n,
        "ci95": wilson(successes, n),
    }


def _count(outcomes: Sequence[QuestionOutcome], test: Callable[[QuestionOutcome], bool]) -> int:
    return sum(1 for o in outcomes if test(o))


def percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 1)
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1], 1)


def retrieval_metrics(outcomes: Sequence[QuestionOutcome], k: int) -> dict[str, Any]:
    """Over answerable questions (they have evidence)."""
    scored = [o for o in outcomes if o.question.answerable and o.question.evidence]
    ranks = [o.evidence_rank for o in scored]
    return {
        "questions": len(scored),
        f"hit@{k}": rate(sum(1 for r in ranks if r is not None and r <= k), len(scored)),
        f"recall@{k}": round(statistics.fmean(o.evidence_recall(k) for o in scored), 4)
        if scored
        else None,
        "mrr@10": round(
            statistics.fmean(1 / r if r is not None and r <= 10 else 0.0 for r in ranks), 4
        )
        if scored
        else None,
    }


def answer_metrics(outcomes: Sequence[QuestionOutcome]) -> dict[str, Any]:
    answerable = [o for o in outcomes if o.question.answerable]
    not_answerable = [o for o in outcomes if not o.question.answerable]
    answered = [o for o in outcomes if o.action == "answer"]
    answered_answerable = [o for o in answerable if o.action == "answer"]
    abstained = [o for o in outcomes if o.action == "abstain"]
    should_abstain = [o for o in outcomes if o.question.expected_action == "abstain"]
    cited = sum(len(o.cited_passages) for o in answered_answerable)
    return {
        "coverage": rate(_count(answerable, lambda o: o.action == "answer"), len(answerable)),
        "correctness": rate(
            _count(answered_answerable, lambda o: o.correct), len(answered_answerable)
        ),
        "unsupported_answers": rate(_count(answered, lambda o: o.unsupported), len(answered)),
        "unwarranted_answers": rate(
            _count(not_answerable, lambda o: o.action == "answer"), len(not_answerable)
        ),
        "abstention_precision": rate(
            _count(abstained, lambda o: not o.question.answerable), len(abstained)
        ),
        "abstention_recall": rate(
            _count(should_abstain, lambda o: o.action == "abstain"), len(should_abstain)
        ),
        "citation_precision": rate(sum(o.relevant_citations for o in answered_answerable), cited),
        "uncited_answers": rate(_count(answered, lambda o: o.status == "uncited"), len(answered)),
        "invalid_citations": sum(o.invalid_citations for o in outcomes),
        "failures": _count(outcomes, lambda o: o.status == "failed"),
    }


def latency_metrics(outcomes: Sequence[QuestionOutcome]) -> dict[str, Any]:
    total = [o.latency_ms for o in outcomes]
    generation = [o.generation_ms for o in outcomes if o.generation_ms is not None]
    return {
        "p50_ms": percentile(total, 50),
        "p95_ms": percentile(total, 95),
        "generation_p50_ms": percentile(generation, 50),
        "generation_p95_ms": percentile(generation, 95),
    }


def summarize(
    outcomes: Sequence[QuestionOutcome], *, k: int, answers: bool = True
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "questions": len(outcomes),
        "retrieval": retrieval_metrics(outcomes, k),
        "latency": latency_metrics(outcomes),
    }
    if answers:
        summary["answers"] = answer_metrics(outcomes)
        summary["actions"] = dict(Counter(o.action or "none" for o in outcomes))
    return summary


def by_group(
    outcomes: Sequence[QuestionOutcome], key: Callable[[QuestionOutcome], str]
) -> dict[str, list[QuestionOutcome]]:
    groups: dict[str, list[QuestionOutcome]] = {}
    for outcome in outcomes:
        groups.setdefault(key(outcome), []).append(outcome)
    return groups
