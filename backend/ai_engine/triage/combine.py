"""Combining the two signals, and deciding whether to apply the result.

    score(c) = alpha * llm(c) + (1 - alpha) * neighbours(c)

where llm(c) is the model's confidence for the category it chose (0 for the others)
and neighbours(c) is the category's share of the neighbour vote. With only one signal
available, its own score is used. The top category is applied when its score reaches
the threshold; otherwise the ticket keeps no category (abstention) and the top guess
is only shown as a suggestion. Agreement between the signals raises the score, so it
clears the threshold more easily; disagreement lowers it.

``alpha`` and ``threshold`` are tuned on the dev split of the triage set
(experiments T1-T4), never on the tickets they're reported on.
"""

from dataclasses import dataclass

from ai_engine.triage.llm import LlmVote
from ai_engine.triage.neighbours import NeighbourVote


@dataclass(frozen=True)
class TriagePolicy:
    alpha: float = 0.3  # weight of the language model against the neighbours
    threshold: float = 0.425  # apply at or above this combined score


@dataclass(frozen=True)
class TriageDecision:
    category: str | None  # the top guess (None: no signal at all)
    confidence: float  # its combined score
    confident: bool  # at or above the threshold: apply it
    agree: bool | None  # whether both signals picked the same category (None: one missing)
    scores: dict[str, float]


def combine(
    llm: LlmVote | None, neighbours: NeighbourVote | None, policy: TriagePolicy
) -> TriageDecision:
    llm_scores = {llm.category: llm.confidence} if llm and llm.category else {}
    nn_scores = dict(neighbours.shares) if neighbours else {}
    if llm is not None and neighbours is not None:
        categories = set(llm_scores) | set(nn_scores)
        scores = {
            c: policy.alpha * llm_scores.get(c, 0.0) + (1 - policy.alpha) * nn_scores.get(c, 0.0)
            for c in categories
        }
        agree = bool(llm.category) and llm.category == neighbours.top
    else:
        scores = llm_scores or nn_scores
        agree = None
    if not scores:
        return TriageDecision(None, 0.0, False, agree, {})
    top = max(scores, key=lambda c: (scores[c], c))
    confidence = round(scores[top], 4)
    return TriageDecision(top, confidence, confidence >= policy.threshold, agree, scores)
