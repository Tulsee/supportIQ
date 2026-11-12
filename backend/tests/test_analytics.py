"""Analytics: support operations and AI performance, per organization."""

import json
from collections.abc import Awaitable, Callable

import httpx2
import pytest

from app.services.ingestion import Outcome
from tests.factories import (
    API,
    ScriptedLLM,
    Workspace,
    create_customer,
    create_org,
    create_ticket,
    create_user,
    in_org,
)
from tests.test_retrieval import MANUAL, upload_and_index

pytestmark = pytest.mark.anyio

Ingest = Callable[..., Awaitable[Outcome]]


async def test_overview_counts_tickets_and_response_times(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    first = await create_ticket(api, ws.customer, ws.org_id, subject="Fan noise")
    await create_ticket(api, ws.customer, ws.org_id, subject="Refund")
    await api.post(
        f"{API}/tickets/{first['id']}/messages",
        json={"body": "We're on it."},
        headers=in_org(ws.agent, ws.org_id),
    )
    for change in ({"assignee_id": ws.agent.id}, {"status": "in_progress"}, {"status": "resolved"}):
        updated = await api.patch(
            f"{API}/tickets/{first['id']}", json=change, headers=in_org(ws.agent, ws.org_id)
        )
        assert updated.status_code == 200, updated.text

    response = await api.get(f"{API}/analytics/overview", headers=in_org(ws.admin, ws.org_id))

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["tickets"]["created"] == 2
    assert data["tickets"]["resolved"] == 1
    assert data["tickets"]["open"] == 1
    assert data["tickets_replied"] == 1
    assert data["median_first_response_minutes"] is not None
    assert data["median_resolution_minutes"] is not None
    assert len(data["daily"]) == 30
    assert sum(d["created"] for d in data["daily"]) == 2
    assert data["customers"] >= 1


async def test_opening_message_of_a_staff_logged_ticket_is_not_a_first_response(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    # A staff member logs the customer's request: the opening message is written by an
    # agent, but nobody has answered the customer yet.
    customer = await create_customer(api, ws.agent, ws.org_id, name="Phone Caller")
    ticket = await create_ticket(
        api, ws.agent, ws.org_id, subject="Called about a refund", customer_id=customer["id"]
    )

    async def overview() -> dict:
        response = await api.get(f"{API}/analytics/overview", headers=in_org(ws.admin, ws.org_id))
        assert response.status_code == 200, response.text
        return response.json()

    before = await overview()
    assert before["tickets_replied"] == 0
    assert before["median_first_response_minutes"] is None

    await api.post(
        f"{API}/tickets/{ticket['id']}/messages",
        json={"body": "We've started your refund."},
        headers=in_org(ws.agent, ws.org_id),
    )
    after = await overview()
    assert after["tickets_replied"] == 1
    assert after["median_first_response_minutes"] is not None


async def test_ai_performance_reports_outcomes_feedback_and_gaps(
    api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest, llm: ScriptedLLM
) -> None:
    await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    llm.reply = json.dumps(
        {"status": "not_found", "answer": "", "conflicting_passages": [], "clarifying_question": ""}
    )
    asked = []
    for _ in range(2):
        response = await api.post(
            f"{API}/ai/chat",
            json={"query": "Does the fan module support Bluetooth?"},
            headers=in_org(ws.agent, ws.org_id),
        )
        asked.append(response.json())
    await api.put(
        f"{API}/ai/runs/{asked[0]['id']}/feedback",
        json={"rating": "helpful"},
        headers=in_org(ws.agent, ws.org_id),
    )

    response = await api.get(
        f"{API}/analytics/ai-performance", params={"days": 7}, headers=in_org(ws.agent, ws.org_id)
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["answers"]["total"] == 2
    assert data["answers"]["by_status"] == {"abstained": 2}
    assert data["answers"]["human_review_rate"] == 1.0
    assert data["decisions"][0] == {
        "decision": "abstain",
        "reason": "model_not_found",
        "count": 2,
        "mean_score": data["decisions"][0]["mean_score"],
    }
    assert data["latency"]["answer"]["runs"] == 2
    assert data["feedback"] == {"helpful": 1, "not_helpful": 0, "helpful_rate": 1.0}
    assert data["knowledge_gaps"][0]["query"] == "does the fan module support bluetooth?"
    assert data["knowledge_gaps"][0]["count"] == 2
    assert len(data["daily_runs"]) == 7
    assert data["models"][0]["model"] == "test/scripted@v1"


async def test_analytics_are_per_organization_and_for_staff(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    await create_ticket(api, ws.customer, ws.org_id)
    outsider = await create_user(api)
    other = await create_org(api, outsider, "Other Co")

    theirs = await api.get(f"{API}/analytics/overview", headers=in_org(outsider, other["id"]))
    customer = await api.get(f"{API}/analytics/overview", headers=in_org(ws.customer, ws.org_id))

    assert theirs.json()["tickets"]["created"] == 0
    assert customer.status_code == 403
