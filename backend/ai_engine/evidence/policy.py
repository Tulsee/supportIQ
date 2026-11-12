"""The decision policy: answer, ask a clarifying question, escalate, or abstain.

    before generation (no model call when it decides):
      no passages / best passage barely relevant      -> abstain
      vague question with no specific subject         -> clarify
      too few of the question's words in the passages -> abstain
    after generation:
      the model says the question is ambiguous        -> clarify
      the model says the passages disagree            -> escalate
      the model says the passages don't (fully) answer -> abstain
      no citation, or a claim contradicted by its own source, or weak grounding -> abstain
      otherwise                                       -> answer

Every threshold is a field of ``Thresholds``; ``app.cli tune-policy`` picks them on
the dev split from recorded signals. The evidence score combines the signals
(S_e = w_r R + w_c C + w_s S + w_g G); it ranks evidence quality and is not a
calibrated probability.
"""

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

POLICY_VERSION = "evidence-policy-v1"


class Decision(StrEnum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    ESCALATE = "escalate"
    ABSTAIN = "abstain"


@dataclass(frozen=True)
class Thresholds:
    min_relevance: float = 0.1
    clarify_vague: bool = True
    min_coverage: float = 0.0
    abstain_on_partial: bool = True
    max_contradiction: float = 0.95
    min_grounding: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Weights:
    relevance: float = 0.25
    coverage: float = 0.25
    consistency: float = 0.25
    grounding: float = 0.25


@dataclass
class Signals:
    passages: int
    relevance: float | None = None  # best passage, 0-1
    relevance_mean: float | None = None  # mean of the best three
    coverage: float = 1.0
    missing_terms: list[str] = field(default_factory=list)
    vague: bool = False
    documents: int = 0  # distinct documents among the selected passages
    # After generation:
    generated: bool = False
    model_status: str | None = None
    citations: int = 0
    invalid_citations: int = 0
    conflict_documents: int = 0  # distinct documents among passages said to disagree
    grounding: float | None = None  # mean claim entailment
    contradiction: float | None = None  # strongest claim/source contradiction

    @property
    def consistency(self) -> float:
        return 0.0 if self.model_status == "conflicting" else 1.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    reason: str
    stage: str  # "before" or "after" generation


def decide_before(s: Signals, t: Thresholds) -> Verdict | None:
    """A decision that makes asking the model unnecessary, if any."""
    if s.passages == 0:
        return Verdict(Decision.ABSTAIN, "no_passages", "before")
    if s.relevance is not None and s.relevance < t.min_relevance:
        return Verdict(Decision.ABSTAIN, "low_relevance", "before")
    if t.clarify_vague and s.vague:
        return Verdict(Decision.CLARIFY, "vague_question", "before")
    if s.coverage < t.min_coverage:
        return Verdict(Decision.ABSTAIN, "low_coverage", "before")
    return None


def decide_after(s: Signals, t: Thresholds) -> Verdict:
    status = s.model_status
    if status == "ambiguous":
        return Verdict(Decision.CLARIFY, "model_ambiguous", "after")
    if status == "conflicting":
        return Verdict(Decision.ESCALATE, "conflicting_sources", "after")
    if status == "not_found":
        return Verdict(Decision.ABSTAIN, "model_not_found", "after")
    if status == "partial" and t.abstain_on_partial:
        return Verdict(Decision.ABSTAIN, "incomplete_evidence", "after")
    if s.citations == 0:
        return Verdict(Decision.ABSTAIN, "no_citations", "after")
    if s.contradiction is not None and s.contradiction > t.max_contradiction:
        return Verdict(Decision.ABSTAIN, "contradicted_by_source", "after")
    if s.grounding is not None and s.grounding < t.min_grounding:
        return Verdict(Decision.ABSTAIN, "weak_grounding", "after")
    return Verdict(Decision.ANSWER, "supported", "after")


def decide(s: Signals, t: Thresholds) -> Verdict | None:
    """The full policy over recorded signals (None: it needs a generated answer)."""
    before = decide_before(s, t)
    if before is not None:
        return before
    return decide_after(s, t) if s.generated else None


DEFAULT_WEIGHTS = Weights()


def evidence_score(s: Signals, w: Weights = DEFAULT_WEIGHTS) -> float | None:
    """0-1 evidence quality (higher is better); None without passages."""
    if s.passages == 0 or s.relevance is None:
        return None
    grounding = s.grounding if s.grounding is not None else 1.0
    total = w.relevance + w.coverage + w.consistency + w.grounding
    score = (
        w.relevance * s.relevance
        + w.coverage * s.coverage
        + w.consistency * s.consistency
        + w.grounding * grounding
    ) / total
    return round(score, 4)
