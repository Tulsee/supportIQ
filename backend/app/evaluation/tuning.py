"""Tune the evidence policy's thresholds from recorded signals.

An ``observe`` run asks the model for every question and records every signal, so
the policy can be replayed offline under any thresholds: no model calls, a few
thousand combinations in seconds. Thresholds are chosen on the dev split only;
report the test split with ``evaluate --policy assessed --split test``.

Objective: the most questions handled as labelled (decision accuracy); ties go to
fewer unsupported answers, then to more answered answerable questions, then to
fewer model calls (decisions made before generation save ~20-40 s each on a local
model), then to the loosest thresholds.
"""

import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_engine.evidence.policy import Decision, Signals, Thresholds, decide

GRID: dict[str, list[Any]] = {
    "min_relevance": [0.0, 0.05, 0.1, 0.2, 0.3, 0.5],
    "clarify_vague": [True, False],
    "min_coverage": [0.0, 0.3, 0.5, 0.7],
    "abstain_on_partial": [True, False],
    "max_contradiction": [0.5, 0.8, 0.95, 1.01],
    "min_grounding": [0.0, 0.1, 0.3, 0.5],
}


@dataclass(frozen=True)
class Replayed:
    accuracy: float
    unsupported: int
    answered_correct: int
    answered: int
    questions: int
    skipped_generations: int = 0


def _signals(record: dict[str, Any]) -> Signals:
    known = Signals.__dataclass_fields__
    return Signals(**{k: v for k, v in record["signals"].items() if k in known})


def replay(records: list[dict[str, Any]], thresholds: Thresholds) -> Replayed:
    correct_decisions = unsupported = answered_correct = answered = skipped = 0
    for r in records:
        verdict = decide(_signals(r), thresholds)
        action = verdict.decision.value if verdict else "abstain"
        skipped += verdict is not None and verdict.stage == "before"
        if action == r["expected_action"]:
            correct_decisions += 1
        if action == Decision.ANSWER.value:
            answered += 1
            good = r["expected_action"] == "answer" and bool(r.get("draft_correct"))
            answered_correct += good
            unsupported += not good
    return Replayed(
        accuracy=correct_decisions / len(records),
        unsupported=unsupported,
        answered_correct=answered_correct,
        answered=answered,
        questions=len(records),
        skipped_generations=skipped,
    )


def tune(report: dict[str, Any], split: str = "dev") -> dict[str, Any]:
    if report["pipeline"].get("policy") != "observe":
        raise ValueError("Tune from an observe run (evaluate --policy observe)")
    records = [
        q
        for q in report["questions"]
        if q.get("signals") is not None and (split == "all" or q["split"] == split)
    ]
    if not records:
        raise ValueError(f"No recorded signals for the {split} split")

    scored = []
    for values in itertools.product(*GRID.values()):
        thresholds = Thresholds(**dict(zip(GRID.keys(), values, strict=True)))
        result = replay(records, thresholds)
        key = (result.accuracy, -result.unsupported, result.answered_correct)
        scored.append((key, thresholds, result))
    scored.sort(key=lambda item: item[0], reverse=True)
    best_key = scored[0][0]
    # Among equally good settings: the fewest model calls, then the loosest.
    ties = [s for s in scored if s[0] == best_key]
    best = min(
        ties,
        key=lambda s: (
            -s[2].skipped_generations,
            s[1].min_relevance,
            s[1].min_coverage,
            s[1].min_grounding,
            -s[1].max_contradiction,
        ),
    )
    default = replay(records, Thresholds())
    return {
        "split": split,
        "questions": len(records),
        "source_run": report["name"],
        "best": {"thresholds": best[1].as_dict(), **best[2].__dict__},
        "equally_good": len(ties),
        "defaults": {"thresholds": Thresholds().as_dict(), **default.__dict__},
        "top": [{"thresholds": t.as_dict(), **r.__dict__} for _, t, r in scored[:10]],
    }


def env_lines(thresholds: dict[str, Any]) -> list[str]:
    return [f"POLICY_{name.upper()}={str(value).lower()}" for name, value in thresholds.items()]


def tune_file(path: Path, split: str = "dev") -> dict[str, Any]:
    return tune(json.loads(path.read_text(encoding="utf-8")), split)
