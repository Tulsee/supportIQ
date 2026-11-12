"""The audit log (Module 9): administrative changes are recorded, readable by the
organization's admins, and can't be edited or deleted."""

import uuid
from collections.abc import Awaitable, Callable

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_rls_context
from app.services.ingestion import Outcome
from tests.factories import (
    API,
    Outbox,
    add_member,
    create_org,
    create_user,
    in_org,
)
from tests.test_retrieval import MANUAL, upload_and_index

pytestmark = pytest.mark.anyio

ORGS = f"{API}/organizations"
Ingest = Callable[..., Awaitable[Outcome]]


async def test_administrative_changes_are_recorded(
    api: httpx2.AsyncClient, outbox: Outbox, ingest: Ingest
) -> None:
    admin = await create_user(api, full_name="Ada Admin")
    agent = await create_user(api)
    customer = await create_user(api)
    org = await create_org(api, admin, name="Acme")
    agent_member = await add_member(api, outbox, admin, org["id"], agent, "agent")
    customer_member = await add_member(api, outbox, admin, org["id"], customer, "customer")
    base = len(
        (await api.get(f"{API}/audit-log", headers=in_org(admin, org["id"]))).json()["items"]
    )

    renamed = await api.patch(
        f"{ORGS}/{org['id']}", json={"name": "Acme Support"}, headers=admin.headers
    )
    assert renamed.status_code == 200, renamed.text
    invited = await api.post(
        f"{ORGS}/{org['id']}/invitations",
        json={"email": "newcomer@example.com", "role": "agent"},
        headers=admin.headers,
    )
    assert invited.status_code == 201, invited.text
    revoked = await api.delete(
        f"{ORGS}/{org['id']}/invitations/{invited.json()['id']}", headers=admin.headers
    )
    assert revoked.status_code == 204, revoked.text
    promoted = await api.patch(
        f"{ORGS}/{org['id']}/members/{agent_member['id']}",
        json={"role": "admin"},
        headers=admin.headers,
    )
    assert promoted.status_code == 200, promoted.text
    removed = await api.delete(
        f"{ORGS}/{org['id']}/members/{customer_member['id']}", headers=admin.headers
    )
    assert removed.status_code == 204, removed.text
    document_id = await upload_and_index(api, admin, org["id"], ingest, "manual.md", MANUAL)
    deleted = await api.delete(f"{API}/documents/{document_id}", headers=in_org(admin, org["id"]))
    assert deleted.status_code == 204, deleted.text

    log = await api.get(f"{API}/audit-log", headers=in_org(admin, org["id"]))
    assert log.status_code == 200, log.text
    entries = log.json()["items"][: len(log.json()["items"]) - base]
    assert [e["action"] for e in entries] == [
        "document.deleted",
        "member.removed",
        "member.role_changed",
        "invitation.revoked",
        "invitation.created",
        "organization.renamed",
    ]
    assert {e["actor_name"] for e in entries} == {"Ada Admin"}
    role_change = entries[2]
    assert role_change["entity_id"] == agent.id
    assert role_change["changes"] == {"role": {"from": "agent", "to": "admin"}}
    assert entries[-1]["changes"] == {"name": {"from": "Acme", "to": "Acme Support"}}

    members_only = await api.get(
        f"{API}/audit-log", params={"entity_type": "member"}, headers=in_org(admin, org["id"])
    )
    assert [e["action"] for e in members_only.json()["items"]][:2] == [
        "member.removed",
        "member.role_changed",
    ]


async def test_only_admins_read_the_audit_log(api: httpx2.AsyncClient, outbox: Outbox) -> None:
    admin = await create_user(api)
    agent = await create_user(api)
    org = await create_org(api, admin)
    await add_member(api, outbox, admin, org["id"], agent, "agent")
    other_admin = await create_user(api)
    await create_org(api, other_admin)

    assert (await api.get(f"{API}/audit-log", headers=in_org(agent, org["id"]))).status_code == 403
    outsider = await api.get(f"{API}/audit-log", headers=in_org(other_admin, org["id"]))
    assert outsider.status_code in (403, 404)


async def test_the_audit_log_is_append_only(
    api: httpx2.AsyncClient, outbox: Outbox, db_session: AsyncSession
) -> None:
    admin = await create_user(api)
    org = await create_org(api, admin, name="Before")
    await api.patch(f"{ORGS}/{org['id']}", json={"name": "After"}, headers=admin.headers)
    await set_rls_context(
        db_session, organization_id=uuid.UUID(org["id"]), user_id=uuid.UUID(admin.id), bypass=False
    )
    assert (await db_session.execute(text("SELECT count(*) FROM audit_log"))).scalar_one() >= 1

    for statement in (
        "UPDATE audit_log SET summary = 'Nothing happened'",
        "DELETE FROM audit_log",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with db_session.begin_nested():
                await db_session.execute(text(statement))
