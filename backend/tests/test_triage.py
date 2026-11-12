"""AI ticket triage (Module 11) end to end with a scripted model and the hashing
embedder: the triage → routing hand-off, confident predictions applied, unsure ones
suggested and accepted, the neighbour vote learning from people, shadow mode,
corrections, failures, analytics, and staff-only data."""

import json
import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.embeddings.embedder import HashingEmbedder
from ai_engine.generation.llm import LLMError, Prompt
from app.core.database import set_rls_context
from app.models import TriageLabel
from app.services.triage import triage_and_route
from app.websocket.broker import InMemoryEventBroker
from app.websocket.presence import InMemoryPresence
from tests.factories import (
    API,
    RecordingMailer,
    RecordingRoutingQueue,
    RecordingTriageQueue,
    ScriptedLLM,
    Workspace,
    create_ticket,
    in_org,
)
from tests.test_categories import add_category
from tests.test_routing import add_rule, condition, fetch, set_mode
from tests.test_teams import add_team

pytestmark = pytest.mark.anyio


def answer(category: str = "billing", confidence: str = "high", **extra: Any) -> str:
    return json.dumps(
        {
            "category": category,
            "confidence": confidence,
            "priority": extra.get("priority", "high"),
            "language": extra.get("language", "en"),
            "urgency_cues": extra.get("urgency_cues", []),
        }
    )


async def set_triage(api: httpx2.AsyncClient, ws: Workspace, mode: str) -> None:
    response = await api.patch(
        f"{API}/organizations/{ws.org_id}",
        json={"settings": {"triage_mode": mode}},
        headers=ws.admin.headers,
    )
    assert response.status_code == 200, response.text


@pytest.fixture(autouse=True)
def policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round numbers for these tests' arithmetic (production uses the tuned values)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "triage_alpha", 0.5)
    monkeypatch.setattr(settings, "triage_threshold", 0.7)


@pytest.fixture
async def categories(api: httpx2.AsyncClient, ws: Workspace) -> dict[str, dict]:
    return {
        name.lower(): await add_category(api, ws, name)
        for name in ("Billing", "Returns", "Connectivity")
    }


class Triage:
    """Runs the triage job in-process, as a worker would."""

    def __init__(self, session, llm, embedder, presence, broker, mailer) -> None:
        self.args = {
            "llm": llm,
            "embedder": embedder,
            "presence": presence,
            "events": broker,
            "mailer": mailer,
        }
        self.session = session

    async def run(self, ticket: dict[str, Any]):
        return await triage_and_route(self.session, uuid.UUID(ticket["id"]), **self.args)


@pytest.fixture
def triage(
    db_session: AsyncSession,
    llm: ScriptedLLM,
    embedder: HashingEmbedder,
    presence: InMemoryPresence,
    broker: InMemoryEventBroker,
    mailer: RecordingMailer,
) -> Triage:
    return Triage(db_session, llm, embedder, presence, broker, mailer)


async def get_triage(api: httpx2.AsyncClient, ws: Workspace, ticket: dict, user=None):
    return await api.get(
        f"{API}/tickets/{ticket['id']}/triage", headers=in_org(user or ws.agent, ws.org_id)
    )


# --- The hand-off ---------------------------------------------------------------------------


async def test_new_tickets_are_triaged_first_when_triage_is_on(
    api: httpx2.AsyncClient,
    ws: Workspace,
    categories: dict,
    triage_queue: RecordingTriageQueue,
    routing_queue: RecordingRoutingQueue,
) -> None:
    await set_mode(api, ws, "on")
    first = await create_ticket(api, ws.customer, ws.org_id)
    assert (triage_queue.ids, routing_queue.ids) == ([], [uuid.UUID(first["id"])])

    await set_triage(api, ws, "shadow")
    second = await create_ticket(api, ws.customer, ws.org_id)
    assert triage_queue.ids == [uuid.UUID(second["id"])]  # triage routes it afterwards
    # A category a person chose skips triage.
    chosen = await create_ticket(
        api,
        ws.agent,
        ws.org_id,
        customer_id=first["customer"]["id"],
        category_id=categories["billing"]["id"],
    )
    assert uuid.UUID(chosen["id"]) not in triage_queue.ids
    assert uuid.UUID(chosen["id"]) in routing_queue.ids


# --- Mode on ------------------------------------------------------------------------------


async def test_a_confident_category_is_applied_and_routing_uses_it(
    api: httpx2.AsyncClient,
    ws: Workspace,
    categories: dict,
    triage: Triage,
    llm: ScriptedLLM,
    presence: InMemoryPresence,
) -> None:
    await set_triage(api, ws, "on")
    await set_mode(api, ws, "on")
    team = await add_team(api, ws, "Billing desk", [{"user_id": ws.agent.id}])
    await add_rule(
        api,
        ws,
        "Billing",
        team["id"],
        conditions={"all": [condition("category", "eq", "billing")]},
    )
    await presence.touch(uuid.UUID(ws.org_id), uuid.UUID(ws.agent.id))
    llm.reply = answer(
        "billing", "high", priority="urgent", language="ne", urgency_cues=["charged twice"]
    )

    ticket = await create_ticket(
        api, ws.customer, ws.org_id, subject="Charged twice", message="Please refund one."
    )
    row = await triage.run(ticket)
    assert (row.decision, row.applied, row.category_key) == ("confident", True, "billing")
    # Only the ticket's content is in the prompt, fenced as data.
    prompt: Prompt = llm.prompts[-1]
    assert "<ticket>" in prompt.user and "Charged twice" in prompt.user
    assert prompt.json_schema["properties"]["category"]["enum"] == [
        "billing",
        "connectivity",
        "returns",
        "unsure",
    ]

    routed = await fetch(api, ws, ticket)
    assert routed["category"]["key"] == "billing"
    assert routed["language"] == "ne"
    assert routed["priority"] == "normal"  # suggested, never applied
    assert (routed["team"]["name"], routed["assignee"]["full_name"]) == (
        "Billing desk",
        "Abe Agent",
    )
    events = (
        await api.get(f"{API}/tickets/{ticket['id']}/events", headers=in_org(ws.agent, ws.org_id))
    ).json()
    category_event = next(e for e in events if e["type"] == "category_changed")
    assert category_event["actor"] is None
    assert category_event["data"]["source"] == "triage"
    assert category_event["data"]["automatic"] is True

    shown = (await get_triage(api, ws, ticket)).json()
    assert shown["decision"] == "confident" and shown["applied"] is True
    assert shown["category"]["name"] == "Billing"
    assert (shown["priority"], shown["language"], shown["urgency_cues"]) == (
        "urgent",
        "ne",
        ["charged twice"],
    )
    assert shown["llm"]["confidence"] == 0.9
    assert shown["neighbours"] is None  # no labelled tickets yet
    assert (await get_triage(api, ws, ticket, ws.customer)).status_code == 403

    # Running the job again (a redelivery) changes nothing.
    assert await triage.run(ticket) is None


async def test_unsure_predictions_are_suggestions_people_can_accept(
    api: httpx2.AsyncClient,
    ws: Workspace,
    categories: dict,
    triage: Triage,
    llm: ScriptedLLM,
    db_session: AsyncSession,
) -> None:
    await set_triage(api, ws, "on")
    llm.reply = answer("returns", "medium")  # 0.65: below the 0.7 threshold
    ticket = await create_ticket(api, ws.customer, ws.org_id, subject="Return my router")
    row = await triage.run(ticket)
    assert (row.decision, row.applied) == ("unsure", False)
    assert (await fetch(api, ws, ticket))["category"] is None

    accepted = await api.post(
        f"{API}/tickets/{ticket['id']}/triage/accept", headers=in_org(ws.agent, ws.org_id)
    )
    assert accepted.status_code == 200, accepted.text
    review = accepted.json()["review"]
    assert (review["outcome"], review["final_category_key"]) == ("confirmed", "returns")
    assert review["reviewed_by"]["full_name"] == "Abe Agent"
    assert (await fetch(api, ws, ticket))["category"]["key"] == "returns"
    # It's now an example for the neighbour vote.
    label = await db_session.get(TriageLabel, uuid.UUID(ticket["id"]))
    assert label is not None and label.category_key == "returns"

    plain = await create_ticket(api, ws.customer, ws.org_id)
    missing = await api.post(
        f"{API}/tickets/{plain['id']}/triage/accept", headers=in_org(ws.agent, ws.org_id)
    )
    assert missing.status_code == 404


async def test_the_neighbour_vote_learns_from_people(
    api: httpx2.AsyncClient,
    ws: Workspace,
    categories: dict,
    triage: Triage,
    llm: ScriptedLLM,
) -> None:
    await set_triage(api, ws, "on")
    customer_id = (await create_ticket(api, ws.customer, ws.org_id))["customer"]["id"]
    returns = categories["returns"]["id"]
    for _ in range(3):  # staff file return requests under Returns
        await create_ticket(
            api,
            ws.agent,
            ws.org_id,
            customer_id=customer_id,
            subject="Return the router for a refund",
            message="I want to return the router and get a refund",
            category_id=returns,
        )

    # The model fails; the neighbours alone are sure.
    llm.error = LLMError("model unreachable")
    alone = await create_ticket(
        api,
        ws.customer,
        ws.org_id,
        subject="Return the router for a refund",
        message="I want to return the router and get a refund please",
    )
    row = await triage.run(alone)
    assert (row.decision, row.category_key, row.applied) == ("confident", "returns", True)
    assert row.error == "model unreachable"
    neighbours = (await get_triage(api, ws, alone)).json()["neighbours"]
    assert neighbours["shares"] == {"returns": 1.0}
    assert len(neighbours["examples"]) == 3 and neighbours["examples"][0]["number"]

    # The model disagrees (billing, medium): returns still wins, but not confidently.
    llm.error = None
    llm.reply = answer("billing", "medium")
    split = await create_ticket(
        api,
        ws.customer,
        ws.org_id,
        subject="Return the router for a refund",
        message="I want to return the router and get a refund",
    )
    row = await triage.run(split)
    assert (row.decision, row.category_key, row.agree) == ("unsure", "returns", False)


# --- Shadow mode, corrections, analytics --------------------------------------------------


async def test_shadow_mode_is_silent_and_measured(
    api: httpx2.AsyncClient,
    ws: Workspace,
    categories: dict,
    triage: Triage,
    llm: ScriptedLLM,
) -> None:
    await set_triage(api, ws, "shadow")
    staff = in_org(ws.agent, ws.org_id)
    llm.reply = answer("billing", "high", language="ne")
    right = await create_ticket(api, ws.customer, ws.org_id, subject="Invoice")
    wrong = await create_ticket(api, ws.customer, ws.org_id, subject="Wi-Fi")
    for ticket in (right, wrong):
        row = await triage.run(ticket)
        assert (row.mode, row.decision, row.applied) == ("shadow", "confident", False)
        untouched = await fetch(api, ws, ticket)
        assert (untouched["category"], untouched["language"]) == (None, None)

    # People categorize as usual; that's what shadow predictions are measured against.
    await api.patch(
        f"{API}/tickets/{right['id']}",
        json={"category_id": categories["billing"]["id"]},
        headers=staff,
    )
    await api.patch(
        f"{API}/tickets/{wrong['id']}",
        json={"category_id": categories["connectivity"]["id"]},
        headers=staff,
    )
    assert (await get_triage(api, ws, right)).json()["review"]["outcome"] == "confirmed"
    assert (await get_triage(api, ws, wrong)).json()["review"]["outcome"] == "changed"

    # An unsure one, whose top guess a person accepts.
    llm.reply = answer("returns", "low")
    unsure = await create_ticket(  # nothing like the labelled ones: the model decides alone
        api, ws.customer, ws.org_id, subject="Box", message="Cardboard packaging question"
    )
    await triage.run(unsure)
    await api.patch(
        f"{API}/tickets/{unsure['id']}",
        json={"category_id": categories["returns"]["id"]},
        headers=staff,
    )

    report = (await api.get(f"{API}/analytics/triage", headers=staff)).json()
    assert report["mode"] == "shadow"
    assert (report["predictions"], report["confident"], report["unsure"]) == (3, 2, 1)
    assert report["coverage"] == pytest.approx(2 / 3)
    assert (report["confident_reviewed"], report["confident_correct"]) == (2, 1)
    assert report["accuracy"] == 0.5
    assert (report["unsure_reviewed"], report["unsure_accuracy"]) == (1, 1.0)
    assert report["applied"] == 0
    assert report["by_category"] == [
        {
            "key": "billing",
            "name": "Billing",
            "predicted": 2,
            "reviewed": 2,
            "correct": 1,
            "accuracy": 0.5,
        }
    ]


async def test_failures_and_too_few_categories(
    api: httpx2.AsyncClient,
    ws: Workspace,
    triage: Triage,
    llm: ScriptedLLM,
) -> None:
    await set_triage(api, ws, "on")
    await add_category(api, ws, "Billing")
    lonely = await create_ticket(api, ws.customer, ws.org_id)
    assert await triage.run(lonely) is None  # one category: nothing to choose

    await add_category(api, ws, "Returns")
    llm.reply = "Sorry, I can't help with that."
    garbled = await create_ticket(api, ws.customer, ws.org_id)
    row = await triage.run(garbled)
    assert (row.decision, row.category_key) == ("failed", None)
    assert row.error == "The model's reply wasn't the expected JSON"


async def test_routing_rules_can_use_the_language(
    api: httpx2.AsyncClient,
    ws: Workspace,
    categories: dict,
    triage: Triage,
    llm: ScriptedLLM,
    presence: InMemoryPresence,
) -> None:
    await set_triage(api, ws, "on")
    await set_mode(api, ws, "on")
    team = await add_team(api, ws, "Nepali desk", [{"user_id": ws.agent.id}])
    rule = await add_rule(
        api,
        ws,
        "Nepali",
        team["id"],
        conditions={"all": [condition("language", "in", ["NE", "hi"])]},
    )
    assert rule["conditions"]["all"][0]["value"] == ["ne", "hi"]
    await presence.touch(uuid.UUID(ws.org_id), uuid.UUID(ws.agent.id))
    llm.reply = answer("unsure", "low", language="ne")
    ticket = await create_ticket(api, ws.customer, ws.org_id, subject="मेरो इन्टरनेट चल्दैन")
    await triage.run(ticket)
    routed = await fetch(api, ws, ticket)
    assert (routed["team"]["name"], routed["category"]) == ("Nepali desk", None)


async def test_triage_tables_are_staff_only(
    api: httpx2.AsyncClient,
    ws: Workspace,
    categories: dict,
    triage: Triage,
    llm: ScriptedLLM,
    db_session: AsyncSession,
) -> None:
    await set_triage(api, ws, "on")
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    await triage.run(ticket)
    await api.patch(
        f"{API}/tickets/{ticket['id']}",
        json={"category_id": categories["returns"]["id"]},
        headers=in_org(ws.agent, ws.org_id),
    )

    async def visible(user) -> list[int]:
        await set_rls_context(
            db_session,
            organization_id=uuid.UUID(ws.org_id),
            user_id=uuid.UUID(user.id),
            bypass=False,
        )
        return [
            (await db_session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            for table in ("ticket_triage", "triage_labels")
        ]

    assert await visible(ws.agent) == [1, 1]
    assert await visible(ws.customer) == [0, 0]
    await set_rls_context(db_session, bypass=True)
    labels = (await db_session.scalars(select(TriageLabel))).all()
    assert [label.category_key for label in labels] == ["returns"]


async def test_backfill_labels_person_chosen_categories(
    api: httpx2.AsyncClient,
    ws: Workspace,
    categories: dict,
    triage: Triage,
    llm: ScriptedLLM,
    db_session: AsyncSession,
) -> None:
    from sqlalchemy import delete

    from app.services.triage import backfill_labels

    await set_triage(api, ws, "on")
    customer_id = (await create_ticket(api, ws.customer, ws.org_id))["customer"]["id"]
    by_person = await create_ticket(
        api, ws.agent, ws.org_id, customer_id=customer_id, category_id=categories["returns"]["id"]
    )
    llm.reply = answer("billing", "high")
    by_ai = await create_ticket(api, ws.customer, ws.org_id, subject="Invoice question")
    await triage.run(by_ai)
    # As if both had been categorized before triage existed.
    await set_rls_context(db_session, bypass=True)
    await db_session.execute(delete(TriageLabel))

    assert await backfill_labels(db_session) == 1
    labels = (await db_session.scalars(select(TriageLabel))).all()
    assert [(str(label.ticket_id), label.category_key) for label in labels] == [
        (by_person["id"], "returns")
    ]
    assert await backfill_labels(db_session) == 0  # nothing new


async def test_a_category_chosen_while_the_model_thinks_wins(
    api: httpx2.AsyncClient,
    ws: Workspace,
    categories: dict,
    triage: Triage,
) -> None:
    from ai_engine.generation.llm import Completion

    await set_triage(api, ws, "on")
    ticket = await create_ticket(api, ws.customer, ws.org_id)

    class SlowModel(ScriptedLLM):
        async def complete(self, prompt: Prompt) -> Completion:
            # A person sets the category while the model is still thinking.
            await api.patch(
                f"{API}/tickets/{ticket['id']}",
                json={"category_id": categories["returns"]["id"]},
                headers=in_org(ws.agent, ws.org_id),
            )
            return await super().complete(prompt)

    triage.args["llm"] = SlowModel(reply=answer("billing", "high"))
    assert await triage.run(ticket) is None  # nothing recorded or applied
    assert (await fetch(api, ws, ticket))["category"]["key"] == "returns"
    assert (await get_triage(api, ws, ticket)).json() is None


async def test_neighbours_are_skipped_for_languages_the_embedder_cant_read(
    api: httpx2.AsyncClient,
    ws: Workspace,
    categories: dict,
    triage: Triage,
    llm: ScriptedLLM,
) -> None:
    await set_triage(api, ws, "on")
    customer_id = (await create_ticket(api, ws.customer, ws.org_id))["customer"]["id"]
    for _ in range(3):  # English examples a person filed under Returns
        await create_ticket(
            api,
            ws.agent,
            ws.org_id,
            customer_id=customer_id,
            subject="Router",
            message="router router router",
            category_id=categories["returns"]["id"],
        )
    llm.reply = answer("connectivity", "high", language="ne")
    ticket = await create_ticket(
        api, ws.customer, ws.org_id, subject="Router", message="router router router"
    )
    row = await triage.run(ticket)
    # Without the rule the English neighbours (all Returns) would have outvoted it.
    assert (row.category_key, row.decision) == ("connectivity", "confident")
    assert row.signals["neighbours"] is None
    assert row.signals["neighbours_skipped"] == "language"
