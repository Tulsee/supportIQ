"""Knowledge gaps to articles (Module 13) end to end, with a scripted model, the hashing
embedder and in-memory Qdrant: unanswered questions grouped into gaps that keep their
identity, drafts written only from verified agent replies, private details and
contradictions with the knowledge base left out, publishing as a document, rechecks
that never count as gaps, and staff-only, per-organization data."""

import json
import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.contradictions import judge
from ai_engine.evidence.nli import NliScores
from ai_engine.gaps import draft as drafting
from ai_engine.generation.llm import LLMError, Prompt
from app.models import KnowledgeGap, KnowledgeGapJob
from app.services.knowledge_gaps import JobTools, run_job
from app.workers.queue import QueueUnavailableError
from tests.factories import (
    API,
    AuthedUser,
    RecordingGapQueue,
    ScriptedLLM,
    Workspace,
    create_customer,
    create_org,
    create_ticket,
    in_org,
)
from tests.test_assessment import passage_number, reply
from tests.test_assistant import ASSISTANT, set_org
from tests.test_document_visibility import set_visibility
from tests.test_retrieval import upload_and_index

pytestmark = pytest.mark.anyio

GAPS = f"{API}/knowledge/gaps"
POLICY = b"""# Returns policy

## Returning a product

You can return an unopened Quillon router within 30 days of delivery for a full refund.
Opened routers can be returned within 14 days if they are faulty.
"""
PORT = "How do I set up port forwarding on the Q3 router?"
PORT_2 = "How do I set up port forwarding on my Q3 router"
PORT_3 = "Set up port forwarding on the Q3 router"
HOMEKIT = "Does the QC2 camera work with Apple HomeKit?"
HOMEKIT_2 = "Is the QC2 camera compatible with Apple HomeKit"
STEPS = (
    "In the Quillon Home app, go to Settings, then Advanced, then Port forwarding, and tap "
    "Add rule."
)
PORT_REPLY = (
    f"Hi Priya, thanks for waiting. {STEPS} You can add up to 20 rules. "
    "Your order QN-48213 is not affected."
)
NOT_FOUND = reply("not_found")


@pytest.fixture(autouse=True)
def gap_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Thresholds for the hashing embedder (bag of words), and a small conflict check."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "gap_join_similarity", 0.6)
    monkeypatch.setattr(settings, "gap_cluster_similarity", 0.6)
    monkeypatch.setattr(settings, "gap_ticket_min_similarity", 0.5)
    monkeypatch.setattr(settings, "gap_conflict_passages", 2)


@pytest.fixture
async def kb(api: httpx2.AsyncClient, ws: Workspace, ingest) -> str:
    return await upload_and_index(api, ws.admin, ws.org_id, ingest, "returns.md", POLICY)


async def ask(api: httpx2.AsyncClient, ws: Workspace, llm: ScriptedLLM, query: str) -> dict:
    """An agent's question the knowledge base can't answer (recorded as abstained)."""
    llm.reply = NOT_FOUND
    response = await api.post(
        f"{API}/ai/chat", json={"query": query}, headers=in_org(ws.agent, ws.org_id)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def resolved_ticket(
    api: httpx2.AsyncClient,
    ws: Workspace,
    *,
    subject: str,
    message: str,
    answer: str | None,
    customer: str = "Priya Sharma",
    email: str | None = None,
    resolve: bool = True,
    internal: bool = False,
) -> dict[str, Any]:
    email = email or f"priya.sharma+{uuid.uuid4().hex[:8]}@example.com"
    person = await create_customer(api, ws.agent, ws.org_id, name=customer, email=email)
    ticket = await create_ticket(
        api, ws.agent, ws.org_id, subject=subject, message=message, customer_id=person["id"]
    )
    url = f"{API}/tickets/{ticket['id']}"
    headers = in_org(ws.agent, ws.org_id)
    for body in ({"assignee_id": ws.agent.id}, {"status": "in_progress"}):
        assert (await api.patch(url, json=body, headers=headers)).status_code == 200
    if answer is not None:
        response = await api.post(
            f"{url}/messages", json={"body": answer, "is_internal": internal}, headers=headers
        )
        assert response.status_code == 201, response.text
    if resolve:
        current = (await api.get(url, headers=headers)).json()
        if "resolved" not in current["allowed_statuses"]:
            await api.patch(url, json={"status": "in_progress"}, headers=headers)
        response = await api.patch(url, json={"status": "resolved"}, headers=headers)
        assert response.status_code == 200, response.text
    return ticket


async def start(
    api: httpx2.AsyncClient, ws: Workspace, path: str, expect: int = 202, user=None
) -> dict:
    response = await api.post(f"{GAPS}{path}", headers=in_org(user or ws.admin, ws.org_id))
    assert response.status_code == expect, response.text
    return response.json()


async def run_last(
    db_session: AsyncSession, queue: RecordingGapQueue, llm, embedder, vector_store, **tools
) -> KnowledgeGapJob:
    job = await run_job(
        db_session,
        queue.ids[-1],
        JobTools(llm=llm, embedder=embedder, vectors=vector_store, **tools),
    )
    assert job is not None
    return job


async def analyze(api, ws, db_session, gap_queue, llm, embedder, vector_store) -> KnowledgeGapJob:
    await start(api, ws, "/analyses")
    job = await run_last(db_session, gap_queue, llm, embedder, vector_store)
    assert job.status == "finished", job.error
    return job


async def gaps(api: httpx2.AsyncClient, ws: Workspace, **params: Any) -> list[dict]:
    response = await api.get(GAPS, params=params, headers=in_org(ws.agent, ws.org_id))
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def gap(api: httpx2.AsyncClient, ws: Workspace, gap_id: str) -> dict:
    response = await api.get(f"{GAPS}/{gap_id}", headers=in_org(ws.agent, ws.org_id))
    assert response.status_code == 200, response.text
    return response.json()


async def summary(api: httpx2.AsyncClient, ws: Workspace) -> dict:
    response = await api.get(f"{GAPS}/summary", headers=in_org(ws.agent, ws.org_id))
    assert response.status_code == 200, response.text
    return response.json()


def draft_reply(statements: list[dict], verdict: str = "answered") -> str:
    return json.dumps({"verdict": verdict, "title": "Port forwarding", "statements": statements})


GOOD = {"text": STEPS, "ticket": 1, "quote": STEPS}
PRIVATE = {
    "text": "Priya, your order QN-48213 is not affected.",
    "ticket": 1,
    "quote": "Your order QN-48213 is not affected.",
}
INVENTED = {
    "text": "Port forwarding needs a Secure Plus plan.",
    "ticket": 1,
    "quote": "Port forwarding requires a Secure Plus plan.",
}
CONSISTENT = json.dumps({"verdict": "consistent", "confidence": "high", "conflicts": []})


def scripted(statements: list[dict], verdict: str = "answered", judged: str = CONSISTENT):
    """The drafting call gets ``statements``; the contradiction checks get ``judged``."""

    def answer(prompt: Prompt) -> str:
        if prompt.system == drafting.SYSTEM:
            number = int(prompt.user.split('<ticket number="')[1].split('"')[0])
            return draft_reply([{**s, "ticket": number} for s in statements], verdict)
        if prompt.system == judge.SYSTEM:
            return judged
        return NOT_FOUND

    return answer


@pytest.fixture
async def port_gap(api, ws, kb, llm, db_session, gap_queue, embedder, vector_store) -> str:
    """One gap about port forwarding (asked three times), analyzed."""
    for query in (PORT, PORT, PORT_2):
        await ask(api, ws, llm, query)
    await analyze(api, ws, db_session, gap_queue, llm, embedder, vector_store)
    [found] = await gaps(api, ws)
    return found["id"]


async def draft_now(api, ws, gap_id, db_session, gap_queue, llm, embedder, vector_store) -> dict:
    await start(api, ws, f"/{gap_id}/drafts")
    job = await run_last(db_session, gap_queue, llm, embedder, vector_store)
    assert job.status == "finished", job.error
    return await gap(api, ws, gap_id)


# --- Analysis --------------------------------------------------------------------------------


async def test_unanswered_questions_become_gaps_and_new_phrasings_join_them(
    api, ws, kb, llm, db_session, gap_queue, embedder, vector_store
) -> None:
    for query in (PORT, PORT, PORT_2, HOMEKIT):
        await ask(api, ws, llm, query)
    # Escalations are the contradiction scanner's business, not gaps.
    llm.reply = lambda p: reply("conflicting", conflicting=[passage_number(p, "30 days")])
    await api.post(
        f"{API}/ai/chat",
        json={"query": "What is the return window?"},
        headers=in_org(ws.agent, ws.org_id),
    )

    job = await analyze(api, ws, db_session, gap_queue, llm, embedder, vector_store)
    assert job.result == {
        "questions": 3,
        "new_questions": 3,
        "joined": 0,
        "gaps_created": 2,
        "left_for_later": 0,
    }
    assert llm.prompts[-1].system != drafting.SYSTEM  # analysis never calls the model
    port, homekit = await gaps(api, ws)
    assert port["label"] == PORT and port["asked"] == 3 and port["question_count"] == 2
    assert [q["query"] for q in port["questions"]] == [PORT, PORT_2]
    assert port["questions"][0]["kinds"] == ["answer"]
    assert port["questions"][0]["last_status"] == "abstained"
    assert homekit["label"] == HOMEKIT and homekit["asked"] == 1
    state = await summary(api, ws)
    assert state["open"] == 2 and state["questions"] == 3
    assert state["latest_analysis"]["status"] == "finished"

    # Dismissed: new phrasings still join it, quietly.
    response = await api.patch(
        f"{GAPS}/{homekit['id']}",
        json={"status": "dismissed", "note": "Not supported; no article needed."},
        headers=in_org(ws.admin, ws.org_id),
    )
    assert response.status_code == 200 and response.json()["status"] == "dismissed"
    for query in (PORT_3, HOMEKIT_2):
        await ask(api, ws, llm, query)
    job = await analyze(api, ws, db_session, gap_queue, llm, embedder, vector_store)
    assert job.result["joined"] == 2 and job.result["gaps_created"] == 0
    [port] = await gaps(api, ws, status="open")
    assert port["question_count"] == 3 and port["asked"] == 4
    [homekit] = await gaps(api, ws, status="dismissed")
    assert homekit["asked"] == 2
    detail = await gap(api, ws, homekit["id"])
    assert {q["query"] for q in detail["questions"]} == {HOMEKIT, HOMEKIT_2}

    audit = await api.get(
        f"{API}/audit-log",
        params={"entity_type": "knowledge_gap"},
        headers=in_org(ws.admin, ws.org_id),
    )
    assert [e["action"] for e in audit.json()["items"]] == ["knowledge_gap.dismissed"]


# --- Drafts ----------------------------------------------------------------------------------


async def test_a_draft_uses_only_verified_public_agent_replies(
    api, ws, port_gap, llm, db_session, gap_queue, embedder, vector_store
) -> None:
    source = await resolved_ticket(
        api,
        ws,
        subject="Port forwarding for my game server",
        message="Hi, how do I forward a port on my Q3 router for a game server?",
        answer=PORT_REPLY,
    )
    # Not sources: an unrelated ticket, an open one, one answered in an internal note only.
    await resolved_ticket(
        api, ws, subject="Amber light", message="My router light is amber.", answer="Restart it."
    )
    await resolved_ticket(
        api,
        ws,
        subject="Port forwarding question",
        message="How do I set up port forwarding on the Q3?",
        answer="Still checking.",
        resolve=False,
    )
    await resolved_ticket(
        api,
        ws,
        subject="Port forwarding on the Q3 router",
        message="How do I set up port forwarding on the Q3 router?",
        answer="INTERNAL: port forwarding is under Advanced, ask engineering first.",
        internal=True,
    )
    llm.reply = scripted([GOOD, PRIVATE, INVENTED])

    found = await draft_now(api, ws, port_gap, db_session, gap_queue, llm, embedder, vector_store)

    [used] = found["sources"]
    assert used["id"] == source["id"] and used["via"] == "similar" and used["similarity"] >= 0.5
    draft = found["draft"]
    assert draft["status"] == "drafted" and draft["verdict"] == "answered"
    assert draft["title"] == "Port forwarding"
    assert draft["body"] == STEPS
    assert [(s["included"], s["problems"]) for s in draft["statements"]] == [
        (True, []),
        (False, ["private"]),
        (False, ["quote_not_found"]),
    ]
    assert {"Priya", "QN-48213"} <= set(draft["statements"][1]["private"])
    assert draft["passages_checked"] == 2 and draft["conflicts"] == []  # gap_conflict_passages
    prompt = next(p for p in llm.prompts if p.system == drafting.SYSTEM)
    assert PORT in prompt.user and PORT_REPLY in prompt.user
    assert "INTERNAL" not in prompt.user and "Restart it." not in prompt.user
    assert (await summary(api, ws))["drafted"] == 1

    # Agents can read but not draft.
    await start(api, ws, f"/{port_gap}/drafts", expect=403, user=ws.agent)


async def test_a_draft_that_contradicts_the_knowledge_base_leaves_its_side_out(
    api, ws, port_gap, llm, db_session, gap_queue, embedder, vector_store
) -> None:
    outdated = "Unopened routers can be returned within 60 days for a full refund."
    await resolved_ticket(
        api,
        ws,
        subject="Port forwarding on my Q3 router",
        message="How do I set up port forwarding on my Q3 router? Can I return it otherwise?",
        answer=f"{STEPS} {outdated}",
    )
    conflict = json.dumps(
        {
            "verdict": "conflict",
            "confidence": "high",
            "conflicts": [
                {
                    "topic": "return window",
                    "quote_a": outdated,
                    "quote_b": "You can return an unopened Quillon router within 30 days of "
                    "delivery for a full refund.",
                    "explanation": "60 days against 30 days.",
                }
            ],
        }
    )
    llm.reply = scripted(
        [GOOD, {"text": outdated, "ticket": 1, "quote": outdated}], judged=conflict
    )

    draft = (
        await draft_now(api, ws, port_gap, db_session, gap_queue, llm, embedder, vector_store)
    )["draft"]

    assert draft["status"] == "drafted" and draft["body"] == STEPS
    [found] = draft["conflicts"]
    assert found["document_title"] == "Returns" and found["quote_draft"] == outdated
    assert [s["problems"] for s in draft["statements"]] == [[], ["conflict"]]


class TurnedAroundNli:
    """Contradiction only for the statement that turns its quote around."""

    model_name = "test/turned-around"

    def score(self, pairs: list[tuple[str, str]]) -> list[NliScores]:
        return [
            NliScores(0.0, 0.1, 0.9)
            if "does not support IPv6" in statement
            else NliScores(0.9, 0.1, 0.0)
            for _, statement in pairs
        ]


async def test_a_statement_its_own_quote_contradicts_is_left_out(
    api, ws, port_gap, llm, db_session, gap_queue, embedder, vector_store
) -> None:
    promise = "I've asked our engineering team about IPv6 and I'll get back to you."
    await resolved_ticket(
        api,
        ws,
        subject="Port forwarding on my Q3 router",
        message="How do I set up port forwarding on my Q3 router? Does it support IPv6?",
        answer=f"{STEPS} {promise}",
    )
    llm.reply = scripted(
        [GOOD, {"text": "The Q3 does not support IPv6.", "ticket": 1, "quote": promise}]
    )

    await start(api, ws, f"/{port_gap}/drafts")
    job = await run_last(db_session, gap_queue, llm, embedder, vector_store, nli=TurnedAroundNli())

    assert job.status == "finished", job.error
    draft = (await gap(api, ws, port_gap))["draft"]
    assert draft["status"] == "drafted" and draft["body"] == STEPS
    assert [s["problems"] for s in draft["statements"]] == [[], ["quote_contradicts"]]


async def test_without_sources_nothing_is_drafted_and_failures_are_recorded(
    api, ws, port_gap, llm, db_session, gap_queue, embedder, vector_store
) -> None:
    llm.prompts.clear()
    draft = (
        await draft_now(api, ws, port_gap, db_session, gap_queue, llm, embedder, vector_store)
    )["draft"]
    assert draft["status"] == "no_sources" and draft["body"] is None
    assert llm.prompts == []  # no ticket, no model call

    await resolved_ticket(
        api,
        ws,
        subject="Port forwarding on the Q3 router",
        message="How do I set up port forwarding on the Q3 router?",
        answer="I've passed your question to the product team.",
    )
    llm.reply = scripted([], verdict="not_answered")
    draft = (
        await draft_now(api, ws, port_gap, db_session, gap_queue, llm, embedder, vector_store)
    )["draft"]
    assert draft["status"] == "not_answered" and draft["statements"] == []

    llm.error = LLMError("The model timed out")
    draft = (
        await draft_now(api, ws, port_gap, db_session, gap_queue, llm, embedder, vector_store)
    )["draft"]
    assert draft["status"] == "failed" and draft["error"] == "The model timed out"


async def test_assistant_handoffs_link_their_tickets_as_sources(
    api, ws, llm, db_session, gap_queue, embedder, vector_store, ingest
) -> None:
    public = await upload_and_index(api, ws.admin, ws.org_id, ingest, "returns.md", POLICY)
    assert (await set_visibility(api, ws.admin, ws.org_id, public, "public")).status_code == 200
    await set_org(api, ws, self_service="on")
    llm.reply = NOT_FOUND
    response = await api.post(
        f"{ASSISTANT}/questions", json={"question": PORT}, headers=in_org(ws.customer, ws.org_id)
    )
    conversation = response.json()
    response = await api.post(
        f"{ASSISTANT}/conversations/{conversation['id']}/handoff",
        json={},
        headers=in_org(ws.customer, ws.org_id),
    )
    assert response.status_code == 200, response.text
    ticket_id = response.json()["ticket"]["id"]
    url = f"{API}/tickets/{ticket_id}"
    headers = in_org(ws.agent, ws.org_id)
    for body in ({"assignee_id": ws.agent.id}, {"status": "in_progress"}):
        assert (await api.patch(url, json=body, headers=headers)).status_code == 200
    await api.post(f"{url}/messages", json={"body": STEPS}, headers=headers)
    await api.patch(url, json={"status": "in_progress"}, headers=headers)
    assert (await api.patch(url, json={"status": "resolved"}, headers=headers)).status_code == 200

    await analyze(api, ws, db_session, gap_queue, llm, embedder, vector_store)
    [found] = await gaps(api, ws)
    [question] = (await gap(api, ws, found["id"]))["questions"]
    assert question["kinds"] == ["self_service"] and question["ticket_ids"] == [ticket_id]

    llm.reply = scripted([GOOD])
    detail = await draft_now(
        api, ws, found["id"], db_session, gap_queue, llm, embedder, vector_store
    )
    assert [(s["id"], s["via"]) for s in detail["sources"]] == [(ticket_id, "linked")]
    assert detail["draft"]["body"] == STEPS


# --- Publishing and rechecks -----------------------------------------------------------------


async def test_publishing_indexes_the_article_and_a_recheck_measures_it(
    api,
    ws,
    port_gap,
    llm,
    db_session,
    gap_queue,
    embedder,
    vector_store,
    reranker,
    nli,
    ingest,
    ingestion_queue,
) -> None:
    await resolved_ticket(
        api,
        ws,
        subject="Port forwarding for my game server",
        message="Hi, how do I forward a port on my Q3 router for a game server?",
        answer=PORT_REPLY,
    )
    llm.reply = scripted([GOOD])
    await draft_now(api, ws, port_gap, db_session, gap_queue, llm, embedder, vector_store)
    body = f"{STEPS} Rules apply within a minute."  # edited by the admin

    response = await api.post(
        f"{GAPS}/{port_gap}/publish",
        json={"title": "Port forwarding on the Q3", "body": body, "visibility": "public"},
        headers=in_org(ws.agent, ws.org_id),
    )
    assert response.status_code == 403
    response = await api.post(
        f"{GAPS}/{port_gap}/publish",
        json={"title": "Port forwarding on the Q3", "body": body, "visibility": "public"},
        headers=in_org(ws.admin, ws.org_id),
    )
    assert response.status_code == 200, response.text
    published = response.json()
    assert published["status"] == "published"
    article = published["article"]
    assert article["title"] == "Port forwarding on the Q3" and article["visibility"] == "public"
    assert article["status"] == "queued" and article["published_by"]["full_name"] == "Ada Admin"
    document_id = article["document_id"]
    assert str(ingestion_queue.jobs[-1][0]) == document_id
    original = await api.get(
        f"{API}/documents/{document_id}/file", headers=in_org(ws.admin, ws.org_id)
    )
    assert original.text == f"# Port forwarding on the Q3\n\n{body}\n"

    # Not before it is indexed; never twice; can't be dismissed once published.
    response = await api.post(f"{GAPS}/{port_gap}/rechecks", headers=in_org(ws.admin, ws.org_id))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "article_not_indexed"
    for path, expected in (("/drafts", "gap_not_open"),):
        response = await api.post(f"{GAPS}/{port_gap}{path}", headers=in_org(ws.admin, ws.org_id))
        assert response.json()["error"]["code"] == expected
    response = await api.patch(
        f"{GAPS}/{port_gap}", json={"status": "dismissed"}, headers=in_org(ws.admin, ws.org_id)
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "gap_published"

    await ingest(document_id)
    llm.reply = lambda p: reply("answered", f"Tap Add rule [{passage_number(p, 'Add rule')}].")
    await start(api, ws, f"/{port_gap}/rechecks")
    job = await run_last(
        db_session, gap_queue, llm, embedder, vector_store, reranker=reranker, nli=nli
    )
    assert job.status == "finished", job.error
    assert job.result == {"total": 2, "answered": 2}
    recheck = (await gap(api, ws, port_gap))["recheck"]
    assert recheck["total"] == 2 and recheck["answered"] == recheck["cited_article"] == 2
    assert [(q["before"], q["after"]) for q in recheck["questions"]] == [
        ("abstained", "answered"),
        ("abstained", "answered"),
    ]

    # Recheck runs are never collected as gaps, even when they fail.
    llm.reply = NOT_FOUND
    await start(api, ws, f"/{port_gap}/rechecks")
    await run_last(db_session, gap_queue, llm, embedder, vector_store, reranker=reranker, nli=nli)
    await analyze(api, ws, db_session, gap_queue, llm, embedder, vector_store)
    [found] = await gaps(api, ws)
    assert found["asked"] == 3 and found["asked_since_published"] == 0
    analytics = await api.get(
        f"{API}/analytics/ai-performance", params={"days": 7}, headers=in_org(ws.agent, ws.org_id)
    )
    assert [g["count"] for g in analytics.json()["knowledge_gaps"]] == [2, 1]

    audit = await api.get(
        f"{API}/audit-log",
        params={"entity_type": "knowledge_gap"},
        headers=in_org(ws.admin, ws.org_id),
    )
    [entry] = audit.json()["items"]
    assert entry["action"] == "knowledge_gap.published"
    assert entry["changes"]["edited"]["to"] is True


# --- Jobs and access -------------------------------------------------------------------------


async def test_one_job_at_a_time_and_a_down_queue_fails_cleanly(
    api, ws, kb, gap_queue: RecordingGapQueue
) -> None:
    await start(api, ws, "/analyses")
    response = await api.post(f"{GAPS}/analyses", headers=in_org(ws.admin, ws.org_id))
    assert response.status_code == 409 and response.json()["error"]["code"] == "job_running"


async def test_an_unavailable_queue_fails_the_job(api, ws, gap_queue: RecordingGapQueue) -> None:
    gap_queue.error = QueueUnavailableError("redis down")
    response = await api.post(f"{GAPS}/analyses", headers=in_org(ws.admin, ws.org_id))
    assert response.status_code == 503
    state = await summary(api, ws)
    assert state["latest_job"]["status"] == "failed"
    assert state["latest_job"]["error"] == "The job queue is unavailable."
    gap_queue.error = None
    await start(api, ws, "/analyses")  # the failed job doesn't block the next


async def test_gaps_are_staff_only_and_per_organization(
    api, ws, port_gap, outbox, make_client
) -> None:
    response = await api.get(GAPS, headers=in_org(ws.customer, ws.org_id))
    assert response.status_code == 403
    response = await api.get(f"{GAPS}/{port_gap}", headers=in_org(ws.customer, ws.org_id))
    assert response.status_code == 403
    await start(api, ws, "/analyses", expect=403, user=ws.agent)

    other_admin: AuthedUser = ws.admin
    other = await create_org(api, other_admin, name="Other", slug=f"other-{uuid.uuid4().hex[:6]}")
    response = await api.get(GAPS, headers=in_org(other_admin, other["id"]))
    assert response.status_code == 200 and response.json()["items"] == []
    response = await api.get(f"{GAPS}/{port_gap}", headers=in_org(other_admin, other["id"]))
    assert response.status_code == 404


async def test_a_failed_contradiction_check_keeps_the_draft(
    api, ws, port_gap, llm, db_session, gap_queue, embedder, vector_store
) -> None:
    await resolved_ticket(
        api,
        ws,
        subject="Port forwarding for my game server",
        message="Hi, how do I forward a port on my Q3 router for a game server?",
        answer=PORT_REPLY,
    )
    drafted = scripted([GOOD])

    def judge_down(prompt: Prompt) -> str:
        if prompt.system == judge.SYSTEM:
            raise LLMError("The model timed out")
        return drafted(prompt)

    llm.reply = judge_down
    await draft_now(api, ws, port_gap, db_session, gap_queue, llm, embedder, vector_store)

    [gap_row] = (
        await db_session.execute(
            select(KnowledgeGap.draft_status, KnowledgeGap.draft_body, KnowledgeGap.draft).where(
                KnowledgeGap.id == uuid.UUID(port_gap)
            )
        )
    ).all()
    assert gap_row.draft_status == "drafted" and gap_row.draft_body == STEPS
    checks = gap_row.draft["conflict_checks"]
    assert checks and all(c["error"] == "The model timed out" for c in checks)
