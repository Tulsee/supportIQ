from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import rls_bypass
from app.models import OrganizationInvitation
from tests.factories import API, Outbox, add_member, create_org, create_user, invite

pytestmark = pytest.mark.anyio

LOOKUP = f"{API}/invitations/lookup"
ACCEPT = f"{API}/invitations/accept"


def invitations_url(org_id: str) -> str:
    return f"{API}/organizations/{org_id}/invitations"


async def test_new_user_is_invited_by_email_and_joins(
    api: httpx2.AsyncClient, outbox: Outbox
) -> None:
    admin = await create_user(api, full_name="Ada Admin")
    org = await create_org(api, admin, "Acme Support")

    created = await invite(api, admin, org["id"], "New.Hire@Example.com", "agent")

    assert created["email"] == "new.hire@example.com"
    assert created["status"] == "pending"
    assert created["invited_by_name"] == "Ada Admin"
    assert "token" not in created
    email = outbox.last_to("new.hire@example.com")
    assert email.subject == "Ada Admin invited you to Acme Support on SupportIQ"
    assert "/invitations/accept#token=" in email.text
    token = outbox.invitation_token("new.hire@example.com")

    preview = await api.post(LOOKUP, json={"token": token})
    assert preview.status_code == 200
    assert preview.json() | {"expires_at": None} == {
        "organization_name": "Acme Support",
        "email": "new.hire@example.com",
        "role": "agent",
        "invited_by_name": "Ada Admin",
        "status": "pending",
        "expires_at": None,
        "account_exists": False,
    }

    invitee = await create_user(api, "new.hire@example.com")
    accepted = await api.post(ACCEPT, json={"token": token}, headers=invitee.headers)

    assert accepted.status_code == 200
    assert accepted.json()["id"] == org["id"]
    assert accepted.json()["role"] == "agent"
    my_orgs = (await api.get(f"{API}/organizations", headers=invitee.headers)).json()
    assert [o["id"] for o in my_orgs["items"]] == [org["id"]]
    open_invitations = await api.get(invitations_url(org["id"]), headers=admin.headers)
    assert open_invitations.json()["total"] == 0

    reused = await api.post(ACCEPT, json={"token": token}, headers=invitee.headers)
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "invitation_accepted"


async def test_only_the_invited_email_can_accept(api: httpx2.AsyncClient, outbox: Outbox) -> None:
    admin = await create_user(api)
    invitee = await create_user(api)
    someone_else = await create_user(api)
    org = await create_org(api, admin)
    await invite(api, admin, org["id"], invitee.email, "agent")
    token = outbox.invitation_token(invitee.email)

    wrong_user = await api.post(ACCEPT, json={"token": token}, headers=someone_else.headers)
    anonymous = await api.post(ACCEPT, json={"token": token})

    assert wrong_user.status_code == 403
    assert wrong_user.json()["error"]["code"] == "invitation_email_mismatch"
    assert anonymous.status_code == 401
    assert (await api.post(ACCEPT, json={"token": token}, headers=invitee.headers)).is_success


async def test_reinviting_replaces_the_previous_link(
    api: httpx2.AsyncClient, outbox: Outbox
) -> None:
    admin = await create_user(api)
    invitee = await create_user(api)
    org = await create_org(api, admin)
    await invite(api, admin, org["id"], invitee.email, "agent")
    first_token = outbox.invitation_token(invitee.email)

    await invite(api, admin, org["id"], invitee.email, "admin")
    second_token = outbox.invitation_token(invitee.email)

    old = await api.post(ACCEPT, json={"token": first_token}, headers=invitee.headers)
    assert old.status_code == 410
    assert old.json()["error"]["code"] == "invitation_revoked"
    new = await api.post(ACCEPT, json={"token": second_token}, headers=invitee.headers)
    assert new.status_code == 200
    assert new.json()["role"] == "admin"


async def test_revoked_invitation_cannot_be_accepted(
    api: httpx2.AsyncClient, outbox: Outbox
) -> None:
    admin = await create_user(api)
    invitee = await create_user(api)
    org = await create_org(api, admin)
    created = await invite(api, admin, org["id"], invitee.email, "agent")
    token = outbox.invitation_token(invitee.email)

    revoked = await api.delete(
        f"{invitations_url(org['id'])}/{created['id']}", headers=admin.headers
    )

    assert revoked.status_code == 204
    preview = await api.post(LOOKUP, json={"token": token})
    assert preview.json()["status"] == "revoked"
    accept = await api.post(ACCEPT, json={"token": token}, headers=invitee.headers)
    assert accept.status_code == 410


async def test_expired_invitation_cannot_be_accepted(
    api: httpx2.AsyncClient, outbox: Outbox, db_session: AsyncSession
) -> None:
    admin = await create_user(api)
    invitee = await create_user(api)
    org = await create_org(api, admin)
    await invite(api, admin, org["id"], invitee.email, "agent")
    token = outbox.invitation_token(invitee.email)
    async with rls_bypass(db_session):
        await db_session.execute(
            update(OrganizationInvitation).values(
                expires_at=datetime.now(UTC) - timedelta(minutes=1)
            )
        )

    preview = await api.post(LOOKUP, json={"token": token})
    accept = await api.post(ACCEPT, json={"token": token}, headers=invitee.headers)

    assert preview.json()["status"] == "expired"
    assert accept.status_code == 410
    assert accept.json()["error"]["code"] == "invitation_expired"


async def test_only_admins_manage_invitations(api: httpx2.AsyncClient, outbox: Outbox) -> None:
    admin = await create_user(api)
    agent = await create_user(api)
    org = await create_org(api, admin)
    await add_member(api, outbox, admin, org["id"], agent, "agent")

    create = await api.post(
        invitations_url(org["id"]),
        json={"email": "x@example.com", "role": "agent"},
        headers=agent.headers,
    )
    listing = await api.get(invitations_url(org["id"]), headers=agent.headers)

    assert create.status_code == 403
    assert listing.status_code == 403


async def test_existing_members_cannot_be_invited(api: httpx2.AsyncClient) -> None:
    admin = await create_user(api)
    org = await create_org(api, admin)

    response = await api.post(
        invitations_url(org["id"]),
        json={"email": admin.email.upper(), "role": "agent"},
        headers=admin.headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_member"


async def test_invitations_of_another_organization_are_unreachable(
    api: httpx2.AsyncClient,
) -> None:
    alice = await create_user(api)
    bob = await create_user(api)
    alice_org = await create_org(api, alice, "Alice Co")
    bob_org = await create_org(api, bob, "Bob Co")
    bobs_invitation = await invite(api, bob, bob_org["id"], "guest@example.com", "agent")

    # Alice is admin of her org, but Bob's invitation id doesn't resolve there.
    revoke = await api.delete(
        f"{invitations_url(alice_org['id'])}/{bobs_invitation['id']}", headers=alice.headers
    )

    assert revoke.status_code == 404
    bob_view = await api.get(invitations_url(bob_org["id"]), headers=bob.headers)
    assert bob_view.json()["items"][0]["status"] == "pending"


async def test_invitation_email_escapes_html(api: httpx2.AsyncClient, outbox: Outbox) -> None:
    admin = await create_user(api, full_name="<b>Mallory</b>")
    org = await create_org(api, admin, "<script>alert(1)</script>")

    await invite(api, admin, org["id"], "victim@example.com", "agent")

    html = outbox.last_to("victim@example.com").html or ""
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;Mallory&lt;/b&gt;" in html


async def test_unknown_token_is_not_found(api: httpx2.AsyncClient) -> None:
    response = await api.post(LOOKUP, json={"token": "x" * 64})

    assert response.status_code == 404
