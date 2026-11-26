"""Evidence signals, claim checks and the decision policy (no database)."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ai_engine.evidence.grounding import check_claims, premise_for
from ai_engine.evidence.nli import OnnxNliModel, OverlapNli
from ai_engine.evidence.policy import (
    Decision,
    Signals,
    Thresholds,
    decide,
    decide_after,
    decide_before,
    evidence_score,
)
from ai_engine.evidence.signals import coverage, is_specific, stem, top_relevance, vagueness
from ai_engine.generation.citations import check_answer
from ai_engine.generation.structured import parse_structured

# --- Signals -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "vague"),
    [
        ("How do I reset it?", True),
        ("Is it covered?", True),
        ("How long do I have?", True),
        ("How much does it cost?", True),
        ("How long does it take to get my refund?", False),  # "it" is a dummy subject
        ("What are your phone support hours?", False),
        ("E-204", False),
        ("How much does Secure Family cost?", False),  # a name
        ("How do I reset the router?", False),
    ],
)
def test_vagueness(question: str, vague: bool) -> None:
    assert vagueness(question)[0] is vague


def test_specific_questions_name_something() -> None:
    assert is_specific("What does C-12 mean?")
    assert is_specific("Is the Q3 compatible with HomeKit?")
    assert not is_specific("Can I return it?")  # "I" is not a name


def test_coverage_matches_stems_and_prefixes() -> None:
    covered, missing = coverage(
        "How long do refunds take for returned routers?",
        ["Refunds are issued within 5 to 7 business days.", "Return the router in its box."],
    )

    assert missing == ["take"]
    assert covered == pytest.approx(3 / 4)
    assert stem("refunds") == "refund" and stem("returned") == "return"


def test_relevance_is_the_best_cross_encoder_score() -> None:
    best, mean = top_relevance([5.0, None, -5.0, 0.0])

    assert best == pytest.approx(0.9933, abs=1e-3)
    assert mean == pytest.approx((0.9933 + 0.5 + 0.0067) / 3, abs=1e-3)
    assert top_relevance([None]) == (None, None)


# --- Structured replies --------------------------------------------------------------------------


def test_structured_replies_are_parsed_and_plain_text_is_kept() -> None:
    parsed = parse_structured(
        '```json\n{"status": "conflicting", "answer": "A [1]. B [2].", '
        '"conflicting_passages": [1, 2], "clarifying_question": ""}\n```'
    )
    assert parsed.status == "conflicting" and parsed.conflicting_passages == [1, 2]

    plain = parse_structured("The fan stopped [1].")
    assert plain.status == "unstructured" and plain.answer == "The fan stopped [1]."
    assert parse_structured('{"status": "maybe"}').status == "unstructured"


# --- Claim checks --------------------------------------------------------------------------------


def test_claims_are_checked_against_the_passages_they_cite() -> None:
    passages = [
        "Error E-4012 means the cooling fan has stopped. Replace the fan module.",
        "Refunds take 5 to 7 business days.",
    ]
    checked = check_answer("The cooling fan has stopped [1]. Refunds take 9 days [2].", 2)

    grounding = check_claims(checked, passages, OverlapNli())

    supported, contradicted = grounding.claims
    assert supported.claim == "The cooling fan has stopped" and supported.entailment == 1.0
    assert contradicted.contradiction == 0.9  # 9 isn't in the cited passage
    assert grounding.contradiction == 0.9
    assert grounding.entailment == pytest.approx(0.5)


def test_the_premise_is_the_closest_sentences() -> None:
    passage = "Alpha one. The fan has stopped. Gamma three. Delta four. Unplug the fan now."

    premise = premise_for("the fan stopped", passage)

    assert premise == "Alpha one. The fan has stopped. Unplug the fan now."


# --- Policy --------------------------------------------------------------------------------------


def signals(**values: object) -> Signals:
    base: dict = {"passages": 5, "relevance": 0.9, "coverage": 1.0}
    return Signals(**{**base, **values})


def test_decisions_before_generation() -> None:
    t = Thresholds(min_relevance=0.3, min_coverage=0.5)

    assert decide_before(signals(passages=0), t).reason == "no_passages"
    assert decide_before(signals(relevance=0.1), t).reason == "low_relevance"
    assert decide_before(signals(vague=True), t).decision is Decision.CLARIFY
    assert decide_before(signals(vague=True), Thresholds(clarify_vague=False)) is None
    assert decide_before(signals(coverage=0.4), t).reason == "low_coverage"
    assert decide_before(signals(), t) is None


@pytest.mark.parametrize(
    ("values", "decision", "reason"),
    [
        ({"model_status": "ambiguous"}, Decision.CLARIFY, "model_ambiguous"),
        ({"model_status": "conflicting"}, Decision.ESCALATE, "conflicting_sources"),
        ({"model_status": "not_found"}, Decision.ABSTAIN, "model_not_found"),
        ({"model_status": "partial", "citations": 1}, Decision.ABSTAIN, "incomplete_evidence"),
        ({"model_status": "answered"}, Decision.ABSTAIN, "no_citations"),
        (
            {"model_status": "answered", "citations": 2, "contradiction": 0.99},
            Decision.ABSTAIN,
            "contradicted_by_source",
        ),
        (
            {"model_status": "answered", "citations": 2, "grounding": 0.1},
            Decision.ABSTAIN,
            "weak_grounding",
        ),
        (
            {"model_status": "answered", "citations": 2, "grounding": 0.8},
            Decision.ANSWER,
            "supported",
        ),
    ],
)
def test_decisions_after_generation(values: dict, decision: Decision, reason: str) -> None:
    verdict = decide_after(signals(generated=True, **values), Thresholds(min_grounding=0.5))

    assert (verdict.decision, verdict.reason) == (decision, reason)


def test_partial_answers_can_be_allowed() -> None:
    s = signals(generated=True, model_status="partial", citations=1)

    assert decide_after(s, Thresholds(abstain_on_partial=False)).decision is Decision.ANSWER


def test_replaying_the_policy_needs_a_generated_answer() -> None:
    assert decide(signals(), Thresholds()) is None
    assert decide(signals(vague=True), Thresholds()).decision is Decision.CLARIFY


def test_evidence_score() -> None:
    assert evidence_score(signals(passages=0)) is None
    strong = evidence_score(
        signals(generated=True, relevance=1.0, model_status="answered", grounding=1.0)
    )
    conflicted = evidence_score(
        signals(generated=True, relevance=1.0, model_status="conflicting", grounding=1.0)
    )
    assert strong == 1.0 and conflicted == 0.75


# --- The NLI model ---------------------------------------------------------------------------


class PaddingTokenizer:
    """Pads a batch to its longest pair, as the real tokenizer does (one token per word)."""

    def encode_batch(self, pairs: list[tuple[str, str]]) -> list[SimpleNamespace]:
        lengths = [len(f"{p} {h}".split()) for p, h in pairs]
        width = max(lengths)
        return [
            SimpleNamespace(
                ids=[1] * n + [0] * (width - n),
                attention_mask=[1] * n + [0] * (width - n),
                type_ids=[0] * width,
            )
            for n in lengths
        ]


class PaddingSensitiveSession:
    """Like the int8 model: a pair's output shifts with the padded width of its batch."""

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def get_inputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name="input_ids"), SimpleNamespace(name="attention_mask")]

    def run(self, _: object, feeds: dict) -> list[np.ndarray]:
        ids, masks = feeds["input_ids"], feeds["attention_mask"]
        self.batch_sizes.append(ids.shape[0])
        width = ids.shape[1]
        return [np.array([[mask.sum() / 10, 0.0, width / 10] for mask in masks])]


def test_a_pairs_nli_score_does_not_depend_on_what_it_is_scored_with() -> None:
    model = OnnxNliModel("test/padding-sensitive", cache_dir=Path("unused"))
    session = PaddingSensitiveSession()
    model._loaded = (  # type: ignore[assignment]
        PaddingTokenizer(),
        session,
        {0: "entailment", 1: "neutral", 2: "contradiction"},
    )
    short = ("The link is valid", "for 30 minutes")
    long = ("Opened products can be returned within 14 days of delivery", "minus a 15% fee")

    alone = model.score([short])[0]
    together = model.score([short, long])

    assert together[0] == alone
    assert together[1] == model.score([long])[0]
    assert session.batch_sizes == [1, 1, 1, 1]
    assert model.score([]) == []
