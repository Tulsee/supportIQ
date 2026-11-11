import httpx2
import pytest

from tests.factories import (
    API,
    Workspace,
    create_customer,
    create_org,
    create_ticket,
    create_user,
    in_org,
)

pytestmark = pytest.mark.anyio

CUSTOMERS = f"{API}/customers"


async def test_staff_create_list_get_and_update_customers(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    created = await create_customer(api, ws.agent, ws.org_id, "Zed Zulu", "Zed@Example.com")
    await create_customer(api, ws.agent, ws.org_id, "Amy Alpha")

    listed = (await api.get(CUSTOMERS, headers=in_org(ws.agent, ws.org_id))).json()
    searched = (
        await api.get(CUSTOMERS, params={"search": "zulu"}, headers=in_org(ws.admin, ws.org_id))
    ).json()
    updated = await api.patch(
        f"{CUSTOMERS}/{created['id']}",
        json={"email": None, "metadata": {"plan": "pro"}},
        headers=in_org(ws.agent, ws.org_id),
    )

    assert created["email"] == "zed@example.com"
    assert created["has_account"] is False
    assert [c["name"] for c in listed["items"]] == ["Amy Alpha", "Zed Zulu"]  # by name
    assert listed["items"][0]["ticket_count"] == 0
    assert [c["name"] for c in searched["items"]] == ["Zed Zulu"]
    assert updated.status_code == 200
    assert updated.json()["email"] is None
    assert updated.json()["metadata"] == {"plan": "pro"}
    assert updated.json()["name"] == "Zed Zulu"  # untouched


async def test_customer_email_is_unique_per_organization(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    await create_customer(api, ws.agent, ws.org_id, "One", "same@example.com")

    duplicate = await api.post(
        CUSTOMERS,
        json={"name": "Two", "email": "SAME@example.com"},
        headers=in_org(ws.agent, ws.org_id),
    )
    other_admin = await create_user(api)
    other_org = await create_org(api, other_admin, "Other Co")

    assert duplicate.status_code == 409
    # Another organization may have a customer with the same email.
    await create_customer(api, other_admin, other_org["id"], "Two", "same@example.com")


async def test_customers_are_staff_only(api: httpx2.AsyncClient, ws: Workspace) -> None:
    headers = in_org(ws.customer, ws.org_id)

    assert (await api.get(CUSTOMERS, headers=headers)).status_code == 403
    assert (await api.post(CUSTOMERS, json={"name": "X"}, headers=headers)).status_code == 403


async def test_only_admins_delete_and_only_customers_without_tickets(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    empty = await create_customer(api, ws.agent, ws.org_id, "No Tickets")
    busy = await create_customer(api, ws.agent, ws.org_id, "Has Tickets")
    await create_ticket(api, ws.agent, ws.org_id, customer_id=busy["id"])

    by_agent = await api.delete(f"{CUSTOMERS}/{empty['id']}", headers=in_org(ws.agent, ws.org_id))
    with_tickets = await api.delete(
        f"{CUSTOMERS}/{busy['id']}", headers=in_org(ws.admin, ws.org_id)
    )
    deleted = await api.delete(f"{CUSTOMERS}/{empty['id']}", headers=in_org(ws.admin, ws.org_id))

    assert by_agent.status_code == 403
    assert with_tickets.status_code == 409
    assert deleted.status_code == 204


async def test_customers_of_another_organization_are_not_found(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    customer = await create_customer(api, ws.agent, ws.org_id)
    intruder = await create_user(api)
    intruder_org = await create_org(api, intruder, "Intruder Co")

    response = await api.get(
        f"{CUSTOMERS}/{customer['id']}", headers=in_org(intruder, intruder_org["id"])
    )
    listed = (await api.get(CUSTOMERS, headers=in_org(intruder, intruder_org["id"]))).json()

    assert response.status_code == 404
    assert listed["total"] == 0
