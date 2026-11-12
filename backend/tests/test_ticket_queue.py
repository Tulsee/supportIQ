"""The ticket queue for staff (Module 10, phase 7): each ticket's SLA summary, the
"due soon" / "breached" and "my teams" filters, and the customer fields routing
rules can use. Kathmandu, Monday-Friday 09:00-17:00; tickets open Monday 09:00."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx2
import pytest

from tests.factories import API, Workspace, create_ticket, in_org
from tests.test_teams import add_team

pytestmark = pytest.mark.anyio

KTM = ZoneInfo("Asia/Kathmandu")


def at(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=KTM).astimezone(UTC)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    """Clocks start Monday 09:00; the queue is read at a time the test sets."""
    monkeypatch.setattr("app.services.sla_clocks.utcnow", lambda: at(2026, 11, 16, 9, 0))

    def read_at(*parts: int) -> None:
        moment = at(*parts)
        monkeypatch.setattr("app.api.v1.endpoints.tickets.utcnow", lambda: moment)
        monkeypatch.setattr("app.repositories.tickets.utcnow", lambda: moment)

    return read_at


@pytest.fixture
async def sla(api: httpx2.AsyncClient, ws: Workspace) -> None:
    response = await api.post(
        f"{API}/sla/setup", json={"timezone": "Asia/Kathmandu"}, headers=in_org(ws.admin, ws.org_id)
    )
    assert response.status_code == 201, response.text


async def queue(api: httpx2.AsyncClient, ws: Workspace, user=None, **params) -> dict[str, dict]:
    response = await api.get(
        f"{API}/tickets", params=params, headers=in_org(user or ws.agent, ws.org_id)
    )
    assert response.status_code == 200, response.text
    return {t["subject"]: t for t in response.json()["items"]}


async def test_each_ticket_shows_the_clock_that_matters_most(
    api: httpx2.AsyncClient, ws: Workspace, sla: None, clock
) -> None:
    customer_id = (await create_ticket(api, ws.customer, ws.org_id, subject="Normal"))["customer"][
        "id"
    ]
    for subject, priority in (("Urgent", "urgent"), ("Low", "low")):
        await create_ticket(
            api, ws.agent, ws.org_id, subject=subject, customer_id=customer_id, priority=priority
        )
    answered = await create_ticket(api, ws.customer, ws.org_id, subject="Answered")
    await api.post(
        f"{API}/tickets/{answered['id']}/messages",
        json={"body": "On it"},
        headers=in_org(ws.agent, ws.org_id),
    )

    clock(2026, 11, 16, 12, 30)
    tickets = await queue(api, ws)
    summary = {s: (t["sla"]["status"], t["sla"]["kind"]) for s, t in tickets.items()}
    assert summary == {
        "Normal": ("at_risk", "first_response"),  # due 13:00, warned at 12:00
        "Urgent": ("breached", "first_response"),  # due 10:00
        "Low": ("on_track", "first_response"),  # due 17:00, warned at 16:00
        "Answered": ("on_track", "resolution"),  # first response met: resolution next
    }
    assert tickets["Normal"]["sla"]["remaining_minutes"] == 30
    assert tickets["Urgent"]["sla"]["remaining_minutes"] == -150

    assert set(await queue(api, ws, sla="at_risk")) == {"Normal"}
    assert set(await queue(api, ws, sla="breached")) == {"Urgent"}
    # Customers see neither the SLA nor the filter's effect.
    mine = await queue(api, ws, ws.customer, sla="breached")
    assert set(mine) == {"Normal", "Urgent", "Low", "Answered"}
    assert {t["sla"] for t in mine.values()} == {None}

    # The single-ticket read has it too.
    one = await api.get(
        f"{API}/tickets/{tickets['Urgent']['id']}", headers=in_org(ws.agent, ws.org_id)
    )
    assert one.json()["sla"]["status"] == "breached"


async def test_without_slas_there_is_no_summary(api: httpx2.AsyncClient, ws: Workspace) -> None:
    await create_ticket(api, ws.customer, ws.org_id, subject="Plain")
    assert (await queue(api, ws))["Plain"]["sla"] is None
    assert await queue(api, ws, sla="breached") == {}


async def test_my_teams(api: httpx2.AsyncClient, ws: Workspace) -> None:
    team = await add_team(api, ws, "Support", [{"user_id": ws.agent.id}])
    ours = await create_ticket(api, ws.customer, ws.org_id, subject="Ours")
    await create_ticket(api, ws.customer, ws.org_id, subject="Nobody's")
    await api.patch(
        f"{API}/tickets/{ours['id']}",
        json={"team_id": team["id"]},
        headers=in_org(ws.agent, ws.org_id),
    )
    assert set(await queue(api, ws, my_teams=True)) == {"Ours"}
    assert await queue(api, ws, ws.admin, my_teams=True) == {}  # not in the team
    assert set(await queue(api, ws, ws.customer, my_teams=True)) == {"Ours", "Nobody's"}


async def test_customer_fields_for_routing_rules(api: httpx2.AsyncClient, ws: Workspace) -> None:
    staff = in_org(ws.agent, ws.org_id)
    for name, metadata in (
        ("Gold Co", {"plan": "gold", "tier": 1, "Region": "EU"}),
        ("Silver Co", {"plan": "silver"}),
        ("Plain Co", {}),
    ):
        response = await api.post(
            f"{API}/customers", json={"name": name, "metadata": metadata}, headers=staff
        )
        assert response.status_code == 201, response.text

    fields = await api.get(f"{API}/routing/customer-fields", headers=staff)
    # Most common first; keys rules can't name (upper case) are left out.
    assert fields.json() == ["plan", "tier"]
    customer = await api.get(
        f"{API}/routing/customer-fields", headers=in_org(ws.customer, ws.org_id)
    )
    assert customer.status_code == 403
