import uuid

import httpx2
import pytest

from tests.factories import API, Outbox, add_member, create_org, create_user

pytestmark = pytest.mark.anyio

ORGS = f"{API}/organizations"


async def test_creator_becomes_admin_and_sees_the_organization(api: httpx2.AsyncClient) -> None:
    owner = await create_user(api)

    org = await create_org(api, owner, "Acme Support")

    assert org["slug"] == "acme-support"
    assert org["role"] == "admin"
    listing = (await api.get(ORGS, headers=owner.headers)).json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == org["id"]


async def test_slugs_are_unique_across_tenants(api: httpx2.AsyncClient) -> None:
    owner = await create_user(api)
    other = await create_user(api)
    first = await create_org(api, owner, "Acme Support")

    # Another user's org with the same name: the existing slug is invisible to them
    # under RLS, yet the generated slug is still deduplicated.
    second = await create_org(api, other, "Acme Support")
    explicit_duplicate = await api.post(
        ORGS, json={"name": "Other", "slug": first["slug"]}, headers=other.headers
    )

    assert second["slug"].startswith("acme-support-")
    assert explicit_duplicate.status_code == 409


async def test_invalid_slug_is_rejected(api: httpx2.AsyncClient) -> None:
    owner = await create_user(api)

    response = await api.post(
        ORGS, json={"name": "Acme", "slug": "Not Valid!"}, headers=owner.headers
    )

    assert response.status_code == 422


# --- Tenant isolation -------------------------------------------------------------------


async def test_users_cannot_see_or_touch_other_organizations(api: httpx2.AsyncClient) -> None:
    alice = await create_user(api)
    bob = await create_user(api)
    alice_org = await create_org(api, alice, "Alice Co")
    bob_org = await create_org(api, bob, "Bob Co")

    listing = (await api.get(ORGS, headers=alice.headers)).json()
    assert [org["id"] for org in listing["items"]] == [alice_org["id"]]

    bob_url = f"{ORGS}/{bob_org['id']}"
    attempts = [
        ("GET", bob_url, None),
        ("PATCH", bob_url, {"name": "Hijacked"}),
        ("GET", f"{bob_url}/members", None),
        ("GET", f"{bob_url}/invitations", None),
        ("POST", f"{bob_url}/invitations", {"email": alice.email, "role": "admin"}),
    ]
    for method, url, body in attempts:
        response = await api.request(method, url, json=body, headers=alice.headers)
        assert response.status_code == 404, (method, url)

    # A foreign organization is indistinguishable from one that doesn't exist.
    foreign = await api.get(bob_url, headers=alice.headers)
    missing = await api.get(f"{ORGS}/{uuid.uuid4()}", headers=alice.headers)
    assert foreign.json() == missing.json()

    bob_view = await api.get(bob_url, headers=bob.headers)
    assert bob_view.json()["name"] == "Bob Co"
    assert (await api.get(f"{bob_url}/members", headers=bob.headers)).json()["total"] == 1


async def test_member_ids_from_another_organization_are_unreachable(
    api: httpx2.AsyncClient, outbox: Outbox
) -> None:
    alice = await create_user(api)
    bob = await create_user(api)
    carol = await create_user(api)
    alice_org = await create_org(api, alice, "Alice Co")
    bob_org = await create_org(api, bob, "Bob Co")
    carol_in_bob_org = await add_member(api, outbox, bob, bob_org["id"], carol, "agent")

    # Alice is an admin, but of her own org: using it in the path doesn't reach Bob's member.
    foreign_member_url = f"{ORGS}/{alice_org['id']}/members/{carol_in_bob_org['id']}"
    promote = await api.patch(foreign_member_url, json={"role": "admin"}, headers=alice.headers)
    remove = await api.delete(foreign_member_url, headers=alice.headers)

    assert promote.status_code == 404
    assert remove.status_code == 404
    bob_members = (await api.get(f"{ORGS}/{bob_org['id']}/members", headers=bob.headers)).json()
    carol_row = next(m for m in bob_members["items"] if m["id"] == carol_in_bob_org["id"])
    assert carol_row["role"] == "agent"


# --- Roles within an organization -------------------------------------------------------


async def test_roles_are_enforced_within_an_organization(
    api: httpx2.AsyncClient, outbox: Outbox
) -> None:
    admin = await create_user(api)
    agent = await create_user(api)
    customer = await create_user(api)
    org = await create_org(api, admin)
    await add_member(api, outbox, admin, org["id"], agent, "agent")
    await add_member(api, outbox, admin, org["id"], customer, "customer")
    org_url = f"{ORGS}/{org['id']}"

    agent_view = await api.get(f"{org_url}/members", headers=agent.headers)
    assert agent_view.status_code == 200
    assert agent_view.json()["total"] == 3
    assert (await api.get(f"{org_url}/members", headers=customer.headers)).status_code == 403

    agent_renames = await api.patch(org_url, json={"name": "Renamed"}, headers=agent.headers)
    assert agent_renames.status_code == 403
    assert agent_renames.json()["error"]["code"] == "forbidden"

    admin_renames = await api.patch(org_url, json={"name": "Renamed"}, headers=admin.headers)
    assert admin_renames.status_code == 200
    assert admin_renames.json()["name"] == "Renamed"
    customer_view = await api.get(org_url, headers=customer.headers)
    assert customer_view.json()["role"] == "customer"


async def test_last_admin_cannot_be_demoted_or_removed(
    api: httpx2.AsyncClient, outbox: Outbox
) -> None:
    admin = await create_user(api)
    org = await create_org(api, admin)
    members_url = f"{ORGS}/{org['id']}/members"
    own_id = (await api.get(members_url, headers=admin.headers)).json()["items"][0]["id"]

    demote = await api.patch(
        f"{members_url}/{own_id}", json={"role": "agent"}, headers=admin.headers
    )
    remove = await api.delete(f"{members_url}/{own_id}", headers=admin.headers)

    assert demote.status_code == 409
    assert remove.status_code == 409

    second_admin = await create_user(api)
    await add_member(api, outbox, admin, org["id"], second_admin, "admin")
    demote_again = await api.patch(
        f"{members_url}/{own_id}", json={"role": "agent"}, headers=admin.headers
    )
    assert demote_again.status_code == 200
    assert demote_again.json()["role"] == "agent"


async def test_removed_member_loses_access(api: httpx2.AsyncClient, outbox: Outbox) -> None:
    admin = await create_user(api)
    agent = await create_user(api)
    org = await create_org(api, admin)
    membership = await add_member(api, outbox, admin, org["id"], agent, "agent")

    removed = await api.delete(
        f"{ORGS}/{org['id']}/members/{membership['id']}", headers=admin.headers
    )

    assert removed.status_code == 204
    assert (await api.get(f"{ORGS}/{org['id']}", headers=agent.headers)).status_code == 404
