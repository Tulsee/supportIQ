"""The triage evaluation (experiments T1-T4) on a tiny set: replies cached and resumed,
the four configurations, tuning on dev, and the report. No real model."""

import json
from pathlib import Path

import pytest

from ai_engine.generation.llm import Prompt
from ai_engine.triage.llm import CategoryOption
from app.core.config import REPO_ROOT
from app.evaluation import triage as evaluation
from tests.factories import ScriptedLLM

pytestmark = pytest.mark.anyio

CATEGORIES = [CategoryOption("billing", "Billing"), CategoryOption("returns", "Returns")]


def item(id_: str, split: str, category: str, *, ambiguous: bool = False) -> evaluation.TriageItem:
    return evaluation.TriageItem(
        id_, split, category, "normal", "en", ambiguous, f"Subject {id_}", f"Body {id_}"
    )


ITEMS = [
    item("d1", "dev", "billing"),
    item("d2", "dev", "billing"),
    item("d3", "dev", "returns"),
    item("d4", "dev", "returns"),
    item("t1", "test", "billing"),
    item("t2", "test", "returns", ambiguous=True),
]
VECTORS = {
    "d1": [1.0, 0.0],
    "d2": [0.95, 0.05],
    "d3": [0.0, 1.0],
    "d4": [0.05, 0.95],
    "t1": [0.9, 0.1],
    "t2": [0.6, 0.4],  # closer to billing: the neighbours get it wrong
}


async def test_replies_are_cached_and_resumed(tmp_path: Path) -> None:
    def answer(prompt: Prompt) -> str:
        category = "returns" if "t2" in prompt.user else "billing"
        return json.dumps(
            {
                "category": category,
                "confidence": "high",
                "priority": "normal",
                "language": "en",
                "urgency_cues": [],
            }
        )

    llm = ScriptedLLM(reply=answer)
    cache = tmp_path / "replies.jsonl"
    replies = await evaluation.collect_votes(ITEMS[:3], CATEGORIES, llm, cache)
    assert len(llm.prompts) == 3 and replies["d3"]["vote"]["category"] == "billing"
    replies = await evaluation.collect_votes(ITEMS, CATEGORIES, llm, cache)
    assert len(llm.prompts) == 6  # only the three new ones were asked
    assert set(replies) == {i.id for i in ITEMS}
    assert len(cache.read_text(encoding="utf-8").splitlines()) == 6


def test_the_four_configurations_and_tuning() -> None:
    def reply(category: str, confidence: float) -> dict:
        return {
            "vote": {
                "category": category,
                "confidence": confidence,
                "priority": "normal",
                "language": "en",
                "urgency_cues": [],
            },
            "latency_ms": 100,
        }

    replies = {
        "d1": reply("billing", 0.9),
        "d2": reply("billing", 0.65),
        "d3": reply("returns", 0.9),
        "d4": reply("billing", 0.35),  # wrong and unsure: abstention should catch it
        "t1": reply("billing", 0.9),
        "t2": reply("returns", 0.65),
    }
    results = evaluation.evaluate(
        ITEMS,
        CATEGORIES,
        replies,
        VECTORS,
        target=0.95,
        neighbour_settings=evaluation.NeighbourSettings(k=3, min_similarity=0.5, min_neighbours=2),
    )
    tuning = results["tuning"]
    assert tuning["meets_target"] is True
    assert tuning["dev_accuracy"] == 1.0
    configs = results["configs"]
    # T1 applies every model answer: one dev mistake (d4).
    assert configs["T1"]["dev"]["accuracy_applied"]["value"] == 0.75
    # T4 abstains on the mistake and is right on everything it applies.
    t4_dev = configs["T4"]["dev"]
    assert t4_dev["accuracy_applied"]["value"] == 1.0
    assert t4_dev["coverage"]["value"] < 1.0
    # On test the neighbours alone get the ambiguous ticket wrong.
    t2 = configs["T2"]["test"]
    assert (t2["accuracy_applied"]["value"], t2["ambiguous"]["n"]) == (0.5, 1)
    assert results["secondary_test"]["priority_exact"]["value"] == 1.0
    assert len(results["t4_test_predictions"]) == 2

    report = evaluation.build_report(
        "tiny", "datasets/x.jsonl", "test/scripted", "test/hash", results, {"commit": "abc"}, {}
    )
    text = evaluation.markdown(report)
    assert "| T4 | Combined with abstention |" in text
    assert f"alpha {tuning['alpha']}, threshold {tuning['threshold']}" in text


def test_the_triage_set_is_well_formed() -> None:
    items = evaluation.load_items(REPO_ROOT / "datasets" / "triage" / "quillon_triage_v1.jsonl")
    categories = evaluation.load_categories(REPO_ROOT / "datasets" / "triage" / "categories.json")
    keys = {c.key for c in categories}
    assert len(items) >= 150
    assert len({i.id for i in items}) == len(items)
    assert {i.category for i in items} == keys
    assert {i.split for i in items} == {"dev", "test"}
    assert {i.priority for i in items} <= {"low", "normal", "high", "urgent"}
    for key in keys:  # every category is in both splits
        assert {i.split for i in items if i.category == key} == {"dev", "test"}
    assert sum(i.ambiguous for i in items) >= 10
