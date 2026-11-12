"""Claim-level grounding: is each cited claim supported by the passage it cites?

Each citation span of the answer is a claim. Its premise is the cited passage,
narrowed to the sentences that share the most words with the claim (NLI models
read at most 512 tokens and judge short premises better). A claim cited to
several passages takes its best score.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from ai_engine.evidence.nli import NliModel, NliScores
from ai_engine.evidence.signals import stem, terms
from ai_engine.generation.citations import CheckedAnswer

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_PREMISE_SENTENCES = 3


@dataclass(frozen=True)
class ClaimCheck:
    claim: str
    passages: list[int]
    entailment: float
    contradiction: float


@dataclass(frozen=True)
class Grounding:
    claims: list[ClaimCheck]

    @property
    def entailment(self) -> float | None:
        """Mean entailment over claims (1 = every claim stated by its source)."""
        if not self.claims:
            return None
        return sum(c.entailment for c in self.claims) / len(self.claims)

    @property
    def contradiction(self) -> float | None:
        """The strongest contradiction between a claim and its source."""
        return max((c.contradiction for c in self.claims), default=None)


def premise_for(claim: str, passage: str) -> str:
    """The passage sentences that share the most words with the claim, in order."""
    sentences = [s.strip() for s in _SENTENCE.split(passage) if s.strip()]
    if len(sentences) <= _PREMISE_SENTENCES:
        return " ".join(sentences)
    wanted = {stem(t) for t in terms(claim)}
    ranked = sorted(
        range(len(sentences)),
        key=lambda i: -len(wanted & {stem(t) for t in terms(sentences[i])}),
    )
    keep = sorted(ranked[:_PREMISE_SENTENCES])
    return " ".join(sentences[i] for i in keep)


def check_claims(checked: CheckedAnswer, passages: Sequence[str], nli: NliModel) -> Grounding:
    """Score every cited claim against its cited passages (passage n is passages[n-1])."""
    by_claim: dict[tuple[int, int], list[int]] = {}
    for citation in checked.citations:
        if citation.end > citation.start:
            by_claim.setdefault((citation.start, citation.end), []).append(citation.passage)
    if not by_claim:
        return Grounding([])

    pairs: list[tuple[str, str]] = []
    index: list[tuple[tuple[int, int], int]] = []
    for span, numbers in by_claim.items():
        claim = checked.text[span[0] : span[1]]
        for number in numbers:
            pairs.append((premise_for(claim, passages[number - 1]), claim))
            index.append((span, number))
    scores = nli.score(pairs)

    best: dict[tuple[int, int], NliScores] = {}
    for (span, _), score in zip(index, scores, strict=True):
        current = best.get(span)
        if current is None or score.entailment > current.entailment:
            best[span] = score
    return Grounding(
        [
            ClaimCheck(
                claim=checked.text[span[0] : span[1]],
                passages=by_claim[span],
                entailment=round(score.entailment, 4),
                contradiction=round(score.contradiction, 4),
            )
            for span, score in best.items()
        ]
    )
