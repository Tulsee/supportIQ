"""AI in ticket conversations: reply suggestions, summaries, escalation, feedback,
and follow-up questions in the knowledge base chat."""

import json
from collections.abc import Awaitable, Callable

import httpx2
import pytest

from ai_engine.generation.llm import Prompt
from ai_engine.generation.prompt import CUSTOMER_REPLY_INSTRUCTION
from app.services.ingestion import Outcome
from tests.factories import API, ScriptedLLM, Workspace, create_ticket, in_org
from tests.test_retrieval import MANUAL, upload_and_index

pytestmark = pytest.mark.anyio

Ingest = Callable[..., Awaitable[Outcome]]


def structured(status: str, answer: str = "", conflicting: list[int] | None = None) -> str:
    return json.dumps(
        {
            "status": status,
            "answer": answer,
            "conflicting_passages": conflicting or [],
            "clarifying_question": "",
        }
    )


def cite(phrase: str, text: str) -> Callable[[Prompt], str]:
    def reply(prompt: Prompt) -> str:
        for block in prompt.user.split("<passage ")[1:]:
            if phrase in block:
                return structured("answered", text.format(n=block.split('"')[1]))
        return structured("not_found")

    return reply


@pytest.fixture
async def ticket(api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest) -> dict:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    return await create_ticket(
        api,
        ws.customer,
        ws.org_id,
        subject="Router shows E-4012",
        message="My router shows error E-4012. What does it mean?",
    )


async def test_a_reply_is_suggested_from_the_knowledge_base(
    api: httpx2.AsyncClient, ws: Workspace, ticket: dict, llm: ScriptedLLM
) -> None:
    llm.reply = cite("E-4012", "It means the cooling fan has stopped [{n}].")

    response = await api.post(
        f"{API}/tickets/{ticket['id']}/ai/suggestion", headers=in_org(ws.agent, ws.org_id)
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["kind"] == "suggestion"
    assert run["answer"]["status"] == "answered"
    assert run["answer"]["text"].startswith("It means the cooling fan has stopped")
    assert run["answer"]["prompt_version"] == "grounded-reply-v2-structured"
    assert CUSTOMER_REPLY_INSTRUCTION in llm.prompts[0].system
    assert "Router shows E-4012" in run["query"]  # searched with the subject
    assert llm.prompts[0].user.endswith(
        "Question: My router shows error E-4012. What does it mean?"
    )

    latest = await api.get(f"{API}/tickets/{ticket['id']}/ai", headers=in_org(ws.agent, ws.org_id))
    assert latest.json()["suggestion"]["id"] == run["id"]
    assert latest.json()["summary"] is None
    events = await api.get(
        f"{API}/tickets/{ticket['id']}/events", headers=in_org(ws.agent, ws.org_id)
    )
    assert "ai_escalated" not in [e["type"] for e in events.json()]


async def test_no_supported_reply_escalates_the_ticket_to_staff(
    api: httpx2.AsyncClient, ws: Workspace, ticket: dict, llm: ScriptedLLM
) -> None:
    llm.reply = structured("not_found")

    run = (
        await api.post(
            f"{API}/tickets/{ticket['id']}/ai/suggestion", headers=in_org(ws.agent, ws.org_id)
        )
    ).json()

    assert run["answer"]["status"] == "abstained" and run["answer"]["requires_human_review"]
    staff_events = (
        await api.get(f"{API}/tickets/{ticket['id']}/events", headers=in_org(ws.agent, ws.org_id))
    ).json()
    escalated = [e for e in staff_events if e["type"] == "ai_escalated"]
    assert len(escalated) == 1
    assert escalated[0]["data"]["run_id"] == run["id"]
    assert escalated[0]["data"]["reason"] == "model_not_found"
    customer_events = (
        await api.get(
            f"{API}/tickets/{ticket['id']}/events", headers=in_org(ws.customer, ws.org_id)
        )
    ).json()
    assert "ai_escalated" not in [e["type"] for e in customer_events]


async def test_an_escalation_notifies_the_tickets_assignee(
    api: httpx2.AsyncClient, ws: Workspace, ticket: dict, llm: ScriptedLLM
) -> None:
    assigned = await api.patch(
        f"{API}/tickets/{ticket['id']}",
        json={"assignee_id": ws.admin.id},
        headers=in_org(ws.agent, ws.org_id),
    )
    assert assigned.status_code == 200, assigned.text
    llm.reply = structured("not_found")

    run = (
        await api.post(
            f"{API}/tickets/{ticket['id']}/ai/suggestion", headers=in_org(ws.agent, ws.org_id)
        )
    ).json()

    items = (await api.get(f"{API}/notifications", headers=in_org(ws.admin, ws.org_id))).json()[
        "items"
    ]
    escalations = [i for i in items if i["kind"] == "ai_escalated"]
    assert len(escalations) == 1
    assert escalations[0]["data"] == {"run_id": run["id"]}
    assert escalations[0]["title"] == f"The AI couldn't draft a reply for #{ticket['number']}"


async def test_a_ticket_is_summarized_with_internal_notes(
    api: httpx2.AsyncClient, ws: Workspace, ticket: dict, llm: ScriptedLLM
) -> None:
    await api.post(
        f"{API}/tickets/{ticket['id']}/messages",
        json={"body": "Checked: unit is under warranty.", "is_internal": True},
        headers=in_org(ws.agent, ws.org_id),
    )
    llm.reply = "- The customer reports error E-4012.\n- Warranty confirmed internally."

    response = await api.post(
        f"{API}/tickets/{ticket['id']}/ai/summary", headers=in_org(ws.agent, ws.org_id)
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["kind"] == "summary" and run["results"] == []
    assert run["answer"]["text"].startswith("- The customer reports error E-4012.")
    assert run["answer"]["prompt_version"] == "ticket-summary-v1"
    prompt = llm.prompts[0].user
    assert "Customer: My router shows error E-4012" in prompt
    assert "Agent (internal note): Checked: unit is under warranty." in prompt
    latest = await api.get(f"{API}/tickets/{ticket['id']}/ai", headers=in_org(ws.agent, ws.org_id))
    assert latest.json()["summary"]["id"] == run["id"]


async def test_ticket_ai_is_for_staff(api: httpx2.AsyncClient, ws: Workspace, ticket: dict) -> None:
    for path in ("suggestion", "summary"):
        response = await api.post(
            f"{API}/tickets/{ticket['id']}/ai/{path}", headers=in_org(ws.customer, ws.org_id)
        )
        assert response.status_code == 403
    latest = await api.get(
        f"{API}/tickets/{ticket['id']}/ai", headers=in_org(ws.customer, ws.org_id)
    )
    assert latest.status_code == 403


async def test_runs_can_be_rated(
    api: httpx2.AsyncClient, ws: Workspace, ticket: dict, llm: ScriptedLLM
) -> None:
    llm.reply = "- Summary."
    run = (
        await api.post(
            f"{API}/tickets/{ticket['id']}/ai/summary", headers=in_org(ws.agent, ws.org_id)
        )
    ).json()
    url = f"{API}/ai/runs/{run['id']}/feedback"

    first = await api.put(url, json={"rating": "helpful"}, headers=in_org(ws.agent, ws.org_id))
    changed = await api.put(
        url,
        json={"rating": "not_helpful", "comment": "Missed the warranty."},
        headers=in_org(ws.agent, ws.org_id),
    )
    other = await api.put(url, json={"rating": "helpful"}, headers=in_org(ws.admin, ws.org_id))
    mine = await api.get(url, headers=in_org(ws.agent, ws.org_id))
    customer = await api.put(
        url, json={"rating": "helpful"}, headers=in_org(ws.customer, ws.org_id)
    )

    assert first.json() == {"helpful": 1, "not_helpful": 0, "mine": "helpful", "my_comment": None}
    assert changed.json()["not_helpful"] == 1 and changed.json()["helpful"] == 0
    assert other.json()["helpful"] == 1 and other.json()["mine"] == "helpful"
    assert mine.json() == {
        "helpful": 1,
        "not_helpful": 1,
        "mine": "not_helpful",
        "my_comment": "Missed the warranty.",
    }
    assert customer.status_code == 403


async def test_follow_up_questions_are_searched_with_the_previous_one(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest, llm: ScriptedLLM
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    llm.reply = cite("E-5020", "Unplug the device for an hour [{n}].")

    response = await api.post(
        f"{API}/ai/chat",
        json={
            "query": "and what should I do?",
            "history": [
                {
                    "question": "What does error E-5020 mean?",
                    "answer": "The power supply is overheating [1].",
                }
            ],
        },
        headers=in_org(ws.agent, ws.org_id),
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["query"] == "What does error E-5020 mean? and what should I do?"
    assert run["assessment"]["reason"] == "supported"  # not clarified: it has context
    user_prompt = llm.prompts[0].user
    assert user_prompt.startswith("<conversation>\nQ: What does error E-5020 mean?")
    assert user_prompt.endswith("Question: and what should I do?")
