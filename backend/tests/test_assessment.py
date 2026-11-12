"""The evidence policy end to end: answer, clarify, escalate or abstain, with every
signal recorded. The model is scripted to reply with structured JSON."""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
import pytest

from ai_engine.generation.llm import Prompt
from app.core.config import settings
from app.services.ingestion import Outcome
from tests.factories import API, AuthedUser, ScriptedLLM, Workspace, in_org
from tests.test_retrieval import MANUAL, upload_and_index

pytestmark = pytest.mark.anyio

Ingest = Callable[..., Awaitable[Outcome]]

POLICY = """# Returns policy

## Returns

You can return an unopened device within 30 days for a full refund.
"""
OLD_FAQ = """# Old FAQ

## Returns

You can return any device within 60 days for a full refund.
"""


def reply(status: str, answer: str = "", conflicting: list[int] | None = None, clarify: str = ""):
    return json.dumps(
        {
            "status": status,
            "answer": answer,
            "conflicting_passages": conflicting or [],
            "clarifying_question": clarify,
        }
    )


def passage_number(prompt: Prompt, phrase: str) -> int:
    for block in prompt.user.split("<passage ")[1:]:
        if phrase in block:
            return int(block.split('"')[1])
    raise AssertionError(f"no passage contains {phrase!r}")


async def ask(api: httpx2.AsyncClient, user: AuthedUser, org_id: str, query: str) -> dict[str, Any]:
    response = await api.post(f"{API}/ai/chat", json={"query": query}, headers=in_org(user, org_id))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
async def manual(api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)


async def test_a_supported_answer_is_given_with_its_assessment(
    api: httpx2.AsyncClient, ws: Workspace, manual: None, llm: ScriptedLLM
) -> None:
    llm.reply = lambda p: reply(
        "answered",
        f"Error E-4012 means the cooling fan has stopped [{passage_number(p, 'E-4012')}].",
    )

    run = await ask(api, ws.agent, ws.org_id, "What does error E-4012 mean?")

    answer, assessment = run["answer"], run["assessment"]
    assert answer["status"] == "answered" and not answer["requires_human_review"]
    assert answer["prompt_version"] == "grounded-answer-v2-structured"
    assert "conflicting_passages" in llm.prompts[0].system  # asked for JSON
    assert len(answer["citations"]) == 1
    assert assessment["decision"] == "answer" and assessment["reason"] == "supported"
    assert assessment["stage"] == "after"
    assert assessment["relevance"] is not None and assessment["coverage"] == 1.0
    assert assessment["consistency"] == 1.0
    assert assessment["grounding"] == pytest.approx(1.0)  # every claim word is in the source
    assert assessment["claims"][0]["claim"] == "Error E-4012 means the cooling fan has stopped"
    assert assessment["nli_model"] == "test/word-overlap-nli"
    assert assessment["signals"]["model_status"] == "answered"
    assert 0 < assessment["score"] <= 1

    recorded = await api.get(f"{API}/ai/runs/{run['id']}", headers=in_org(ws.agent, ws.org_id))
    assert recorded.json()["assessment"] == assessment


async def test_conflicting_sources_are_escalated_with_both_sides_cited(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest, llm: ScriptedLLM
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "policy.md", POLICY.encode())
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "faq.md", OLD_FAQ.encode())

    def conflicting(p: Prompt) -> str:
        new, old = passage_number(p, "30 days"), passage_number(p, "60 days")
        return reply(
            "conflicting",
            f"The policy says 30 days [{new}]. The FAQ says 60 days [{old}].",
            conflicting=[new, old],
        )

    llm.reply = conflicting

    run = await ask(api, ws.agent, ws.org_id, "How long do I have to return a device?")

    answer, assessment = run["answer"], run["assessment"]
    assert answer["status"] == "escalated" and answer["requires_human_review"]
    assert {c["document"]["title"] for c in answer["citations"]} == {"Policy", "Faq"}
    assert assessment["decision"] == "escalate"
    assert assessment["reason"] == "conflicting_sources"
    assert assessment["consistency"] == 0.0
    assert assessment["signals"]["conflict_documents"] == 2


async def test_the_model_can_ask_a_clarifying_question(
    api: httpx2.AsyncClient, ws: Workspace, manual: None, llm: ScriptedLLM
) -> None:
    llm.reply = reply("ambiguous", clarify="Do you mean the fan error or the power error?")

    run = await ask(api, ws.agent, ws.org_id, "Which error is this about overheating?")

    assert run["answer"]["status"] == "clarify"
    assert run["answer"]["text"] == "Do you mean the fan error or the power error?"
    assert run["answer"]["citations"] == []
    assert run["assessment"]["reason"] == "model_ambiguous"


async def test_a_question_about_it_is_clarified_without_asking_the_model(
    api: httpx2.AsyncClient, ws: Workspace, manual: None, llm: ScriptedLLM
) -> None:
    run = await ask(api, ws.agent, ws.org_id, "How do I reset it?")

    assert llm.prompts == []
    assert run["answer"]["status"] == "clarify"
    assert run["answer"]["text"].startswith("Could you tell me a bit more")
    assert run["assessment"]["stage"] == "before"
    assert run["assessment"]["reason"] == "vague_question"
    assert run["assessment"]["ambiguity"] == 1.0


@pytest.mark.parametrize(
    ("model_reply", "reason"),
    [
        (reply("not_found"), "model_not_found"),
        (reply("partial", "Part of it [1]."), "incomplete_evidence"),
        (reply("answered", "The fan module is covered."), "no_citations"),
    ],
)
async def test_unsupported_answers_are_withheld(
    api: httpx2.AsyncClient,
    ws: Workspace,
    manual: None,
    llm: ScriptedLLM,
    model_reply: str,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "policy_abstain_on_partial", True)
    llm.reply = model_reply

    run = await ask(api, ws.agent, ws.org_id, "Is the fan module covered by the warranty?")

    assert run["answer"]["status"] == "abstained" and run["answer"]["requires_human_review"]
    assert run["answer"]["text"].startswith("I couldn't find a reliable answer")
    assert run["answer"]["citations"] == []
    assert run["answer"]["raw_output"] == model_reply  # the draft is kept for review
    assert run["assessment"]["reason"] == reason


async def test_a_claim_its_source_contradicts_is_withheld(
    api: httpx2.AsyncClient,
    ws: Workspace,
    manual: None,
    llm: ScriptedLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "policy_max_contradiction", 0.5)
    llm.reply = lambda p: reply(
        "answered", f"Unplug the device for 99 hours [{passage_number(p, 'E-5020')}]."
    )

    run = await ask(api, ws.agent, ws.org_id, "What do I do about error E-5020?")

    assert run["answer"]["status"] == "abstained"
    assert run["assessment"]["reason"] == "contradicted_by_source"
    assert run["assessment"]["contradiction"] == pytest.approx(0.9)


async def test_weakly_relevant_evidence_is_not_sent_to_the_model(
    api: httpx2.AsyncClient,
    ws: Workspace,
    manual: None,
    llm: ScriptedLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "policy_min_relevance", 0.99)

    run = await ask(api, ws.agent, ws.org_id, "What is the capital of Australia?")

    assert llm.prompts == []
    assert run["answer"]["status"] == "abstained"
    assert run["assessment"]["reason"] == "low_relevance"


async def test_observe_policy_always_asks_the_model_and_records_the_decision(
    api: httpx2.AsyncClient,
    ws: Workspace,
    manual: None,
    llm: ScriptedLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ai_decision_policy", "observe")
    llm.reply = reply("ambiguous", clarify="The router or the camera?")

    run = await ask(api, ws.agent, ws.org_id, "How do I reset it?")

    assert len(llm.prompts) == 1  # asked anyway, so every signal is recorded
    assert run["assessment"]["policy"] == "observe"
    assert run["assessment"]["reason"] == "vague_question"  # the earlier decision stands
    assert run["assessment"]["signals"]["model_status"] == "ambiguous"
    assert run["answer"]["text"] == "The router or the camera?"


async def test_a_model_without_structured_output_still_works(
    api: httpx2.AsyncClient, ws: Workspace, manual: None, llm: ScriptedLLM
) -> None:
    llm.reply = lambda p: f"Replace the fan module [{passage_number(p, 'fan module')}]."

    run = await ask(api, ws.agent, ws.org_id, "How do I fix error E-4012?")

    assert run["answer"]["status"] == "answered"
    assert run["assessment"]["signals"]["model_status"] == "answered"


async def test_runs_list_shows_policy_decisions(
    api: httpx2.AsyncClient, ws: Workspace, manual: None, llm: ScriptedLLM
) -> None:
    await ask(api, ws.agent, ws.org_id, "How do I reset it?")

    listed = await api.get(f"{API}/ai/runs", headers=in_org(ws.agent, ws.org_id))

    assert listed.json()["items"][0]["answer_status"] == "clarify"


async def test_partial_answers_are_given_by_default(
    api: httpx2.AsyncClient, ws: Workspace, manual: None, llm: ScriptedLLM
) -> None:
    llm.reply = lambda p: reply(
        "partial", f"Replace the fan module [{passage_number(p, 'fan module')}]."
    )

    run = await ask(api, ws.agent, ws.org_id, "How do I fix E-4012 and what does it cost?")

    assert run["answer"]["status"] == "answered"
    assert run["assessment"]["signals"]["model_status"] == "partial"


async def test_questions_far_from_the_documents_are_declined_without_the_model(
    api: httpx2.AsyncClient, ws: Workspace, manual: None, llm: ScriptedLLM
) -> None:
    run = await ask(api, ws.agent, ws.org_id, "Recommend a sourdough bread recipe")

    assert llm.prompts == []
    assert run["answer"]["status"] == "abstained"
    assert run["assessment"]["reason"] == "low_coverage"
