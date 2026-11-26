"""Document freshness (Module 13): owners, review dates, reminders, citation counts."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import set_rls_context
from app.core.security import utcnow
from app.models import Document, OrgRole
from app.services.document_freshness import (
    next_review,
    remind_due_reviews,
    reminder_recipients,
)
from app.services.ingestion import Outcome
from tests.factories import API, Outbox, Workspace, add_member, create_user, in_org
from tests.test_assessment import passage_number, reply
from tests.test_retrieval import upload_and_index

pytestmark = pytest.mark.anyio

Ingest = Callable[..., Awaitable[Outcome]]
DOCS = f"{API}/documents"

MANUAL = b"""# Error codes

Error E-4012 means the cooling fan has stopped. Unplug the router for ten seconds.
"""
RETURNS = b"""# Returns

You can return an unopened router within 30 days for a full refund.
"""


def today() -> date:
    return utcnow().date()


@pytest.fixture
async def docs(api: httpx2.AsyncClient, ws: Workspace, ingest: Ingest) -> dict[str, str]:
    manual = await upload_and_index(api, ws.admin, ws.org_id, ingest, "manual.md", MANUAL)
    returns = await upload_and_index(api, ws.admin, ws.org_id, ingest, "returns.md", RETURNS)
    return {"manual": manual, "returns": returns}


async def patch(api: httpx2.AsyncClient, user, ws: Workspace, doc_id: str, **body: Any):
    return await api.patch(f"{DOCS}/{doc_id}", json=body, headers=in_org(user, ws.org_id))


async def get(api: httpx2.AsyncClient, ws: Workspace, doc_id: str) -> dict[str, Any]:
    response = await api.get(f"{DOCS}/{doc_id}", headers=in_org(ws.agent, ws.org_id))
    assert response.status_code == 200, response.text
    return response.json()


async def listed(api: httpx2.AsyncClient, ws: Workspace, **params: str) -> set[str]:
    response = await api.get(DOCS, params=params, headers=in_org(ws.agent, ws.org_id))
    assert response.status_code == 200, response.text
    return {d["id"] for d in response.json()["items"]}


async def notices(api: httpx2.AsyncClient, ws: Workspace, user) -> list[tuple[str, str, str]]:
    response = await api.get(f"{API}/notifications", headers=in_org(user, ws.org_id))
    assert response.status_code == 200, response.text
    return [
        (n["kind"], n["title"], n["data"].get("document_id", "")) for n in response.json()["items"]
    ]


# --- Owner and review date ---------------------------------------------------------------


async def test_admins_set_an_owner_and_a_review_date_and_it_is_audited(
    api: httpx2.AsyncClient, ws: Workspace, docs: dict[str, str]
) -> None:
    doc_id = docs["manual"]
    fresh = await get(api, ws, doc_id)
    assert fresh["owner"] is None and fresh["review_due_on"] is None
    assert fresh["review_due"] is False and fresh["reviewed_at"] is None

    due = today() + timedelta(days=10)
    response = await patch(
        api,
        ws.admin,
        ws,
        doc_id,
        owner_id=ws.agent.id,
        review_due_on=due.isoformat(),
        review_interval_days=90,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["owner"]["id"] == ws.agent.id
    assert (body["review_due_on"], body["review_interval_days"]) == (due.isoformat(), 90)
    assert body["review_due"] is False

    # Fields left out stay as they are; null removes one.
    renamed = await patch(api, ws.admin, ws, doc_id, title="Error code guide")
    assert renamed.json()["owner"]["id"] == ws.agent.id
    cleared = await patch(api, ws.admin, ws, doc_id, owner_id=None)
    assert cleared.json()["owner"] is None and cleared.json()["review_due_on"] == due.isoformat()

    # Only admins schedule, and only active staff can own a document.
    assert (await patch(api, ws.agent, ws, doc_id, owner_id=ws.agent.id)).status_code == 403
    for owner in (ws.customer.id, str(uuid.uuid4())):
        refused = await patch(api, ws.admin, ws, doc_id, owner_id=owner)
        assert refused.status_code == 400 and refused.json()["error"]["code"] == "invalid_owner"
    assert (await patch(api, ws.admin, ws, doc_id, review_interval_days=0)).status_code == 422

    log = await api.get(
        f"{API}/audit-log", params={"entity_type": "document"}, headers=in_org(ws.admin, ws.org_id)
    )
    actions = [entry["action"] for entry in log.json()["items"]]
    assert actions[:4] == [
        "document.owner_changed",
        "document.renamed",
        "document.review_scheduled",
        "document.owner_changed",
    ]
    scheduled = log.json()["items"][2]
    assert scheduled["changes"] == {
        "review_due_on": {"from": None, "to": due.isoformat()},
        "review_interval_days": {"from": None, "to": 90},
    }


async def test_the_owner_or_an_admin_marks_a_document_reviewed(
    api: httpx2.AsyncClient, ws: Workspace, docs: dict[str, str], outbox: Outbox
) -> None:
    doc_id = docs["manual"]
    overdue = today() - timedelta(days=1)
    await patch(
        api,
        ws.admin,
        ws,
        doc_id,
        owner_id=ws.agent.id,
        review_due_on=overdue.isoformat(),
        review_interval_days=30,
    )
    assert (await get(api, ws, doc_id))["review_due"] is True
    assert await listed(api, ws, freshness="review_due") == {doc_id}

    other_agent = await create_user(api, full_name="Ola Other")
    await add_member(api, outbox, ws.admin, ws.org_id, other_agent, "agent")
    reviews = f"{DOCS}/{doc_id}/reviews"
    for outsider in (other_agent, ws.customer):
        refused = await api.post(reviews, headers=in_org(outsider, ws.org_id))
        assert refused.status_code == 403

    reviewed = await api.post(reviews, headers=in_org(ws.agent, ws.org_id))
    assert reviewed.status_code == 200, reviewed.text
    body = reviewed.json()
    assert body["reviewed_by"]["id"] == ws.agent.id and body["reviewed_at"]
    assert body["review_due_on"] == (today() + timedelta(days=30)).isoformat()
    assert body["review_due"] is False
    assert await listed(api, ws, freshness="review_due") == set()

    # Without an interval a review clears the date; admins can review any document.
    await patch(api, ws.admin, ws, doc_id, review_interval_days=None)
    by_admin = await api.post(reviews, headers=in_org(ws.admin, ws.org_id))
    assert by_admin.json()["review_due_on"] is None
    assert by_admin.json()["reviewed_by"]["id"] == ws.admin.id


def test_the_next_review_date() -> None:
    assert next_review(date(2026, 11, 26), 90) == date(2027, 2, 24)
    assert next_review(date(2026, 11, 26), None) is None


# --- Reminders ---------------------------------------------------------------------------


async def test_due_reviews_remind_the_owner_or_the_admins_once(
    api: httpx2.AsyncClient, ws: Workspace, docs: dict[str, str], db_session: AsyncSession
) -> None:
    yesterday, tomorrow = today() - timedelta(days=1), today() + timedelta(days=1)
    await patch(
        api,
        ws.admin,
        ws,
        docs["manual"],
        owner_id=ws.agent.id,
        review_due_on=yesterday.isoformat(),
    )
    await patch(api, ws.admin, ws, docs["returns"], review_due_on=today().isoformat())

    written = await remind_due_reviews(db_session)

    assert written == 2
    assert await notices(api, ws, ws.agent) == [
        ("document_review_due", "Review due: Manual", docs["manual"])
    ]
    assert await notices(api, ws, ws.admin) == [
        ("document_review_due", "Review due: Returns", docs["returns"])
    ]
    assert await notices(api, ws, ws.customer) == []

    # Running again reminds nobody twice; a date still to come reminds nobody.
    assert await remind_due_reviews(db_session) == 0
    await patch(api, ws.admin, ws, docs["returns"], review_due_on=tomorrow.isoformat())
    assert await remind_due_reviews(db_session) == 0
    # A new due date is a new reminder.
    await patch(api, ws.admin, ws, docs["returns"], review_due_on=yesterday.isoformat())
    assert await remind_due_reviews(db_session) == 1


def test_reminders_go_to_the_admins_when_the_owner_is_not_active_staff() -> None:
    owner, admin_a, admin_b, agent = (uuid.uuid4() for _ in range(4))
    staff = {admin_a: OrgRole.ADMIN, admin_b: OrgRole.ADMIN, agent: OrgRole.AGENT}
    owned = Document(owner_id=agent, review_due_on=date(2026, 11, 1))
    assert reminder_recipients(owned, staff) == [agent]
    gone = Document(owner_id=owner, review_due_on=date(2026, 11, 1))
    assert set(reminder_recipients(gone, staff)) == {admin_a, admin_b}
    unowned = Document(owner_id=None, review_due_on=date(2026, 11, 1))
    assert set(reminder_recipients(unowned, staff)) == {admin_a, admin_b}


# --- Citations ---------------------------------------------------------------------------


async def test_citation_counts_and_the_not_cited_flag(
    api: httpx2.AsyncClient,
    ws: Workspace,
    docs: dict[str, str],
    llm,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "document_new_days", 0)  # judge documents at once
    llm.reply = lambda p: reply(
        "answered",
        f"Error E-4012 means the cooling fan has stopped [{passage_number(p, 'E-4012')}].",
    )
    response = await api.post(
        f"{API}/ai/chat",
        json={"query": "What does error E-4012 mean?"},
        headers=in_org(ws.agent, ws.org_id),
    )
    assert response.json()["answer"]["status"] == "answered"

    manual, returns = await get(api, ws, docs["manual"]), await get(api, ws, docs["returns"])
    assert manual["citations"]["recent"] == 1 and manual["citations"]["last_cited_at"]
    assert manual["citations"]["not_cited"] is False
    assert manual["citations"]["window_days"] == settings.document_citation_window_days
    assert returns["citations"] == {
        "last_cited_at": None,
        "recent": 0,
        "window_days": settings.document_citation_window_days,
        "not_cited": True,
    }
    assert await listed(api, ws, freshness="not_cited") == {docs["returns"]}

    # Evaluation probes and gap rechecks are not people's questions.
    await set_rls_context(db_session, bypass=True)
    await db_session.execute(
        text('UPDATE ai_runs SET config = config || \'{"origin": "gap_check"}\'::jsonb')
    )
    await db_session.commit()
    assert (await get(api, ws, docs["manual"]))["citations"]["not_cited"] is True
    assert await listed(api, ws, freshness="not_cited") == {docs["manual"], docs["returns"]}


async def test_a_new_document_is_too_new_to_flag(
    api: httpx2.AsyncClient, ws: Workspace, docs: dict[str, str]
) -> None:
    assert settings.document_new_days > 0
    assert (await get(api, ws, docs["returns"]))["citations"]["not_cited"] is False
    assert await listed(api, ws, freshness="not_cited") == set()
