"""Evaluation metrics on hand-made outcomes."""

import pytest

from ai_engine.evaluation.dataset import EXPECTED_ACTION, Evidence, Question
from ai_engine.evaluation.metrics import (
    QuestionOutcome,
    answer_metrics,
    percentile,
    retrieval_metrics,
    wilson,
)

FAN = "E-4012: The cooling fan has stopped."
REFUND = "Refunds are issued within 5 to 7 business days."
OTHER = "The router supports WPA3."


def question(category: str = "factual", **overrides: object) -> Question:
    values: dict = {
        "id": "q",
        "category": category,
        "expected_action": EXPECTED_ACTION[category],
        "split": "dev",
        "question": "?",
        "evidence": [Evidence("codes.md", "The cooling fan has stopped")]
        if EXPECTED_ACTION[category] == "answer"
        else [],
        "key_facts": [["fan"], ["stopped", "not spinning"]]
        if EXPECTED_ACTION[category] == "answer"
        else [],
    }
    return Question(**{**values, **overrides})


def outcome(q: Question, passages: list[str], status: str | None = None, **extra: object):
    return QuestionOutcome(
        question=q,
        ranked_passages=passages,
        top_k=5,
        run_id="r",
        latency_ms=100.0,
        status=status,
        **extra,  # type: ignore[arg-type]
    )


def test_evidence_matching_ignores_case_spacing_and_citation_markers() -> None:
    o = outcome(question(), [OTHER, "e-4012:  the COOLING fan\nhas stopped [1]."])

    assert o.evidence_rank == 2
    assert o.evidence_recall(1) == 0.0
    assert o.evidence_recall(2) == 1.0


def test_retrieval_metrics() -> None:
    first = outcome(question(), [FAN, OTHER])
    third = outcome(question(), [OTHER, OTHER, FAN])
    missed = outcome(question(), [OTHER])
    unanswerable = outcome(question("unanswerable"), [OTHER])  # not scored

    metrics = retrieval_metrics([first, third, missed, unanswerable], k=2)

    assert metrics["questions"] == 3
    assert metrics["hit@2"]["value"] == pytest.approx(1 / 3, abs=1e-4)
    assert metrics["mrr@10"] == pytest.approx((1 + 1 / 3 + 0) / 3, abs=1e-4)


def test_key_facts_accept_any_alternative() -> None:
    q = question()

    assert outcome(q, [FAN], "answered", answer="The fan is not spinning [1].").correct
    missing = outcome(q, [FAN], "answered", answer="The fan has a problem [1].")
    assert missing.missing_facts == ["stopped"]
    assert not missing.correct and missing.unsupported


def test_answer_metrics() -> None:
    good = outcome(
        question(), [FAN], "answered", answer="The fan stopped [1].", cited_passages=[FAN]
    )
    wrong_source = outcome(
        question(), [FAN, OTHER], "answered", answer="The fan stopped [2].", cited_passages=[OTHER]
    )
    missed = outcome(question(), [FAN], "declined", answer="I couldn't find this.")
    declined_right = outcome(question("unanswerable"), [OTHER], "declined")
    answered_wrongly = outcome(question("conflicting"), [REFUND], "uncited", answer="5 to 7 days.")
    failed = outcome(question(), [FAN], "failed", error="down")

    metrics = answer_metrics([good, wrong_source, missed, declined_right, answered_wrongly, failed])

    assert metrics["coverage"]["value"] == pytest.approx(
        2 / 4, abs=1e-4
    )  # failed counts as not answered
    assert metrics["correctness"]["value"] == 1.0
    assert metrics["unsupported_answers"]["successes"] == 1  # the conflicting one
    assert metrics["unwarranted_answers"]["value"] == pytest.approx(1 / 2, abs=1e-4)
    assert metrics["abstention_precision"]["value"] == pytest.approx(1 / 2, abs=1e-4)
    assert metrics["abstention_recall"]["value"] == 1.0
    assert metrics["citation_precision"]["value"] == pytest.approx(1 / 2, abs=1e-4)
    assert metrics["uncited_answers"]["value"] == pytest.approx(1 / 3, abs=1e-4)
    assert metrics["failures"] == 1


def test_wilson_interval() -> None:
    assert wilson(0, 0) is None
    low, high = wilson(8, 10)
    assert low == pytest.approx(0.4902, abs=1e-3) and high == pytest.approx(0.9433, abs=1e-3)
    assert wilson(10, 10)[1] == 1.0


def test_percentiles() -> None:
    assert percentile([], 50) is None
    assert percentile([5.0], 95) == 5.0
    assert percentile([float(v) for v in range(1, 101)], 50) == pytest.approx(50.5, abs=0.5)
