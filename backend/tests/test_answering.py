"""Answers from the knowledge base end to end: retrieval, the prompt, the model
(scripted), citation checks, and the recorded run."""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.generation.llm import LLMError, Prompt
from ai_engine.generation.prompt import NOT_FOUND_ANSWER, PROMPT_VERSION
from app.core.config import settings
from app.core.database import reset_rls_context, set_rls_context
from app.services.ingestion import Outcome
from tests.factories import (
    API,
    AuthedUser,
    ScriptedLLM,
    Workspace,
    create_org,
    create_user,
    in_org,
)
from tests.test_retrieval import MANUAL, upload_and_index

pytestmark = pytest.mark.anyio

Ingest = Callable[..., Awaitable[Outcome]]


async def ask(
    api: httpx2.AsyncClient, user: AuthedUser, org_id: str, query: str, **options: Any
) -> dict[str, Any]:
    response = await api.post(
        f"{API}/ai/chat", json={"query": query, **options}, headers=in_org(user, org_id)
    )
    assert response.status_code == 200, response.text
    return response.json()


def cite_passage_about(phrase: str, answer: str) -> Callable[[Prompt], str]:
    """A model that cites whichever passage number contains ``phrase``."""

    def reply(prompt: Prompt) -> str:
        for block in prompt.user.split("<passage ")[1:]:
            number = block.split('"')[1]
            if phrase in block:
                return answer.format(n=number)
        return NOT_FOUND_ANSWER

    return reply


async def test_answers_cite_retrieved_passages_with_their_provenance(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest, llm: ScriptedLLM
) -> None:
    document_id = await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    llm.reply = cite_passage_about(
        "E-4012",
        "Error E-4012 means the cooling fan has stopped [{n}]. Replace the fan module [{n}].",
    )

    run = await ask(api, ws.agent, ws.org_id, "What does error E-4012 mean?", top_k=3)

    answer = run["answer"]
    assert run["kind"] == "answer" and run["answer_status"] == "answered"
    assert answer["status"] == "answered" and answer["error"] is None
    assert answer["text"].startswith("Error E-4012 means the cooling fan has stopped [")
    assert answer["invalid_citations"] == []
    first, second = answer["citations"]
    cited = run["results"][first["passage_number"] - 1]
    assert cited["final_rank"] == first["passage_number"]
    assert "E-4012" in cited["chunk"]["content"]
    assert first["chunk_id"] == cited["chunk"]["id"]
    assert first["document_id"] == document_id
    assert first["document"]["title"] == cited["document"]["title"]
    assert first["document_version"] == 1
    assert first["section_heading"] == "Troubleshooting"
    assert answer["text"][first["answer_start"] : first["answer_end"]] == (
        "Error E-4012 means the cooling fan has stopped"
    )
    assert answer["text"][second["answer_start"] : second["answer_end"]] == (
        "Replace the fan module"
    )
    # What the model was given, and with what settings, is recorded with the run.
    prompt = llm.prompts[0]
    assert answer["system_prompt"] == prompt.system
    assert answer["user_prompt"] == prompt.user
    assert prompt.user.endswith("Question: What does error E-4012 mean?")
    assert prompt.user.count("<passage ") == answer["passage_count"] == len(run["results"])
    assert answer["provider"] == "test"
    assert answer["model"] == "test/scripted@v1"  # as reported by the model
    assert answer["model_version"] == "sha256:test"
    assert answer["config"] == {"temperature": 0.0}
    assert answer["prompt_version"] == PROMPT_VERSION
    assert (answer["input_tokens"], answer["output_tokens"]) == (321, 12)
    assert answer["stop_reason"] == "stop"
    assert answer["raw_output"] == answer["text"]
    assert {"analysis", "rerank", "generation", "total"} <= set(run["timings_ms"])
    assert run["latency_ms"] == run["timings_ms"]["total"]
    assert run["timings_ms"]["total"] >= run["timings_ms"]["generation"]

    recorded = await api.get(f"{API}/ai/runs/{run['id']}", headers=in_org(ws.admin, ws.org_id))
    assert recorded.status_code == 200
    assert recorded.json()["answer"] == answer
    assert recorded.json()["results"] == run["results"]


async def test_invented_citations_are_removed_and_recorded(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest, llm: ScriptedLLM
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    llm.reply = "Unplug the device for an hour [1][42]. See the warranty [43]."

    answer = (await ask(api, ws.agent, ws.org_id, "power supply overheating"))["answer"]

    assert answer["text"] == "Unplug the device for an hour [1]. See the warranty."
    assert answer["raw_output"] == llm.reply
    assert answer["invalid_citations"] == [42, 43]
    assert [c["passage_number"] for c in answer["citations"]] == [1]
    assert answer["status"] == "answered"


@pytest.mark.parametrize(
    ("reply", "status"),
    [("The router supports WPA3.", "uncited"), (NOT_FOUND_ANSWER, "declined")],
)
async def test_uncited_and_declined_answers(
    api: httpx2.AsyncClient,
    ws: Workspace,
    ingest: Ingest,
    llm: ScriptedLLM,
    reply: str,
    status: str,
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    llm.reply = reply

    answer = (await ask(api, ws.agent, ws.org_id, "wireless network"))["answer"]

    assert answer["status"] == status
    assert answer["text"] == reply
    assert answer["citations"] == []


async def test_without_evidence_the_model_is_not_asked(
    api: httpx2.AsyncClient, ws: Workspace, llm: ScriptedLLM
) -> None:
    run = await ask(api, ws.agent, ws.org_id, "How do I reset my password?")

    assert run["results"] == []
    assert run["answer"]["status"] == "no_passages"
    assert run["answer"]["text"] == NOT_FOUND_ANSWER
    assert run["answer"]["passage_count"] == 0
    assert llm.prompts == []


async def test_a_model_failure_is_recorded_with_the_evidence(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest, llm: ScriptedLLM
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    llm.error = LLMError("Couldn't reach Ollama at http://127.0.0.1:11434. Is it running?")

    run = await ask(api, ws.agent, ws.org_id, "fan module")

    assert run["answer"]["status"] == "failed"
    assert run["answer"]["text"] is None
    assert run["answer"]["error"].startswith("Couldn't reach Ollama")
    assert run["results"], "the retrieved passages are still shown"
    assert run["answer"]["user_prompt"] == llm.prompts[0].user
    listed = await api.get(f"{API}/ai/runs", headers=in_org(ws.agent, ws.org_id))
    assert listed.json()["items"][0]["answer_status"] == "failed"


async def test_passages_beyond_the_context_window_are_left_out(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest, llm: ScriptedLLM
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    llm.prompt_token_budget = 520  # the fixed overhead plus a passage or two
    llm.reply = "Replace the fan module [1]. Also [3]."

    run = await ask(api, ws.agent, ws.org_id, "error fan power supply device refund", top_k=4)

    answer = run["answer"]
    assert len(run["results"]) == 3
    assert 1 <= answer["passage_count"] < 3
    assert answer["omitted_passages"] == 3 - answer["passage_count"]
    assert llm.prompts[0].user.count("<passage ") == answer["passage_count"]
    assert answer["invalid_citations"] == [3], "only passages in the prompt can be cited"


async def test_runs_list_by_kind(api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    answered = await ask(api, ws.agent, ws.org_id, "fan module")
    searched = await api.post(
        f"{API}/ai/retrieve", json={"query": "fan module"}, headers=in_org(ws.agent, ws.org_id)
    )

    def listed(**query: str) -> Awaitable[httpx2.Response]:
        return api.get(f"{API}/ai/runs", params=query, headers=in_org(ws.admin, ws.org_id))

    every = (await listed()).json()["items"]
    answers = (await listed(kind="answer")).json()["items"]
    searches = (await listed(kind="retrieval")).json()["items"]
    # (One test transaction: both runs have the same created_at, so no order is asserted.)
    assert sorted((r["kind"], r["answer_status"] or "") for r in every) == [
        ("answer", "answered"),
        ("retrieval", ""),
    ]
    assert [r["id"] for r in answers] == [answered["id"]]
    assert [r["id"] for r in searches] == [searched.json()["id"]]
    assert searched.json()["answer"] is None


async def test_citations_keep_their_provenance_after_a_reindex(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest, llm: ScriptedLLM
) -> None:
    document_id = await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    llm.reply = cite_passage_about("E-4012", "The fan stopped [{n}].")
    run = await ask(api, ws.agent, ws.org_id, "E-4012")
    before = run["answer"]["citations"][0]

    await api.post(f"{API}/documents/{document_id}/reindex", headers=in_org(ws.admin, ws.org_id))
    await ingest(document_id)

    after = (
        await api.get(f"{API}/ai/runs/{run['id']}", headers=in_org(ws.agent, ws.org_id))
    ).json()["answer"]["citations"][0]
    assert after["chunk_id"] is None  # the passage was replaced...
    assert {k: v for k, v in after.items() if k != "chunk_id"} == {
        k: v for k, v in before.items() if k != "chunk_id"
    }  # ...but where it came from is kept


async def test_answers_are_for_staff_of_the_organization(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest, db_session: AsyncSession
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    run = await ask(api, ws.agent, ws.org_id, "fan module")
    outsider = await create_user(api)
    outsider_org = await create_org(api, outsider, "Outsider Co")

    customer = await api.post(
        f"{API}/ai/chat", json={"query": "fan"}, headers=in_org(ws.customer, ws.org_id)
    )
    customer_model = await api.get(f"{API}/ai/model", headers=in_org(ws.customer, ws.org_id))
    foreign_run = await api.get(
        f"{API}/ai/runs/{run['id']}", headers=in_org(outsider, outsider_org["id"])
    )
    foreign_answer = await ask(api, outsider, outsider_org["id"], "fan module")

    assert customer.status_code == 403
    assert customer_model.status_code == 403
    assert foreign_run.status_code == 404
    assert foreign_answer["answer"]["status"] == "no_passages"

    # Row-level security: another organization's staff see none of these rows.
    async def visible(org_id: str, user: AuthedUser) -> tuple[int, int]:
        await reset_rls_context(db_session)
        await set_rls_context(
            db_session, organization_id=uuid.UUID(org_id), user_id=uuid.UUID(user.id)
        )
        answers = await db_session.scalar(
            text("SELECT count(*) FROM ai_answers WHERE ai_run_id = :id"), {"id": run["id"]}
        )
        citations = await db_session.scalar(
            text("SELECT count(*) FROM citations WHERE ai_run_id = :id"), {"id": run["id"]}
        )
        return answers, citations

    assert await visible(ws.org_id, ws.agent) == (1, len(run["answer"]["citations"]))
    assert await visible(outsider_org["id"], outsider) == (0, 0)
    await reset_rls_context(db_session)


async def test_questions_are_rate_limited(
    api: httpx2.AsyncClient, ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ai_chat_limit", 2)

    statuses = [
        (
            await api.post(
                f"{API}/ai/chat", json={"query": "fan"}, headers=in_org(ws.agent, ws.org_id)
            )
        ).status_code
        for _ in range(3)
    ]

    assert statuses == [200, 200, 429]


@pytest.mark.parametrize("state", ["ready", "unreachable"])
async def test_model_status(
    api: httpx2.AsyncClient, ws: Workspace, llm: ScriptedLLM, state: str
) -> None:
    llm.state = state  # type: ignore[assignment]

    response = await api.get(f"{API}/ai/model", headers=in_org(ws.agent, ws.org_id))

    assert response.status_code == 200
    assert response.json() == {
        "provider": "test",
        "model": "test/scripted",
        "status": state,
        "detail": None if state == "ready" else "not running",
    }
