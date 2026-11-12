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
    # Decisions of the evidence policy (Module 5).
    "clarify": "clarify",
    "escalated": "escalate",
    "abstained": "abstain",
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
    # Evidence policy (Module 5): its decision, the reason, every signal, the evidence
    # score, and the model's draft answer (kept even when the policy withheld it).
    decision: str | None = None
    reason: str | None = None
    signals: dict[str, Any] | None = None
    score: float | None = None
    draft: str | None = None

    @property
    def draft_missing_facts(self) -> list[str]:
        text = normalize(self.draft or "")
        return [
            alternatives[0]
            for alternatives in self.question.key_facts
            if not any(normalize(a) in text for a in alternatives)
        ]

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
        "ndcg@10": round(statistics.fmean(ndcg(o, 10) for o in scored), 4) if scored else None,
    }


def ndcg(outcome: QuestionOutcome, k: int) -> float:
    """nDCG@k with binary gains: a passage is relevant if it contains an evidence quote
    not already credited higher up (so one quote counts once)."""
    quotes = [e.quote for e in outcome.question.evidence]
    credited: set[int] = set()
    dcg = 0.0
    for rank, passage in enumerate(outcome.ranked_passages[:k], start=1):
        new = {i for i, q in enumerate(quotes) if i not in credited and contains(passage, q)}
        if new:
            credited |= new
            dcg += 1 / math.log2(rank + 1)
    ideal = sum(1 / math.log2(r + 1) for r in range(1, min(len(quotes), k) + 1))
    return dcg / ideal if ideal else 0.0


def calibration(
    pairs: Sequence[tuple[float, bool]], bins: int = 5
) -> dict[str, float | int | None]:
    """How well a 0-1 score predicts an outcome: Brier score (mean squared error) and
    expected calibration error over equal-width bins. Lower is better for both."""
    if not pairs:
        return {"n": 0, "brier": None, "ece": None}
    brier = statistics.fmean((score - float(ok)) ** 2 for score, ok in pairs)
    ece = 0.0
    for b in range(bins):
        low, high = b / bins, (b + 1) / bins
        members = [(s, ok) for s, ok in pairs if low <= s < high or (b == bins - 1 and s == 1.0)]
        if members:
            confidence = statistics.fmean(s for s, _ in members)
            accuracy = statistics.fmean(float(ok) for _, ok in members)
            ece += len(members) / len(pairs) * abs(confidence - accuracy)
    return {"n": len(pairs), "brier": round(brier, 4), "ece": round(ece, 4)}


def score_calibration(outcomes: Sequence[QuestionOutcome]) -> dict[str, float | int | None]:
    """The evidence score against "handled as labelled, and correct if answered"."""
    pairs = [
        (o.score, o.action == o.question.expected_action and (o.action != "answer" or o.correct))
        for o in outcomes
        if o.score is not None
    ]
    return calibration(pairs)


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
        "decision_accuracy": rate(
            _count(outcomes, lambda o: o.action == o.question.expected_action), len(outcomes)
        ),
        "decisions": confusion(outcomes),
    }


def confusion(outcomes: Sequence[QuestionOutcome]) -> dict[str, dict[str, int]]:
    """Expected action -> what the system did -> count."""
    table: dict[str, dict[str, int]] = {}
    for o in outcomes:
        row = table.setdefault(o.question.expected_action, {})
        action = o.action or "none"
        row[action] = row.get(action, 0) + 1
    return table


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
        summary["calibration"] = score_calibration(outcomes)
    return summary


def by_group(
    outcomes: Sequence[QuestionOutcome], key: Callable[[QuestionOutcome], str]
) -> dict[str, list[QuestionOutcome]]:
    groups: dict[str, list[QuestionOutcome]] = {}
    for outcome in outcomes:
        groups.setdefault(key(outcome), []).append(outcome)
    return groups
