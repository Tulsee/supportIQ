import httpx2
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import main as cli_main
from app.core.config import settings
from app.models import User
from tests.factories import API, DEFAULT_PASSWORD, AuthedUser, create_org, create_user

pytestmark = pytest.mark.anyio

ADMIN = f"{API}/admin"
COOKIE = settings.refresh_cookie_name


async def make_superadmin(db_session: AsyncSession, user: AuthedUser) -> None:
    await db_session.execute(
        update(User).where(User.email == user.email).values(is_superadmin=True)
    )


async def test_regular_users_cannot_use_admin_endpoints(api: httpx2.AsyncClient) -> None:
    user = await create_user(api)

    responses = [
        await api.get(f"{ADMIN}/users", headers=user.headers),
        await api.get(f"{ADMIN}/organizations", headers=user.headers),
        await api.patch(
            f"{ADMIN}/users/{user.id}", json={"is_superadmin": True}, headers=user.headers
        ),
    ]

    assert [r.status_code for r in responses] == [403, 403, 403]


async def test_superadmin_lists_users_and_all_organizations(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    root = await create_user(api, "root@example.com")
    alice = await create_user(api, "alice@example.com")
    bob = await create_user(api, "bob@example.com")
    await create_org(api, alice, "Alice Co")
    await create_org(api, bob, "Bob Co")
    await make_superadmin(db_session, root)

    users = (await api.get(f"{ADMIN}/users", headers=root.headers)).json()
    searched = (
        await api.get(f"{ADMIN}/users", params={"search": "ALI"}, headers=root.headers)
    ).json()
    orgs = (await api.get(f"{ADMIN}/organizations", headers=root.headers)).json()

    assert users["total"] == 3
    assert [u["email"] for u in searched["items"]] == ["alice@example.com"]
    # Sees every tenant's organization (RLS bypass for this platform listing).
    assert {(o["name"], o["member_count"]) for o in orgs["items"]} == {
        ("Alice Co", 1),
        ("Bob Co", 1),
    }
    me = (await api.get(f"{API}/users/me", headers=root.headers)).json()
    assert me["is_superadmin"] is True


async def test_user_search_matches_name_or_email(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    root = await create_user(api, "root@example.com")
    await create_user(api, "olive@example.com", full_name="Olive Other")
    await create_user(api, "pat@example.com", full_name="Pat 100% Real")
    await make_superadmin(db_session, root)

    async def search(term: str) -> list[str]:
        response = await api.get(f"{ADMIN}/users", params={"search": term}, headers=root.headers)
        return [u["email"] for u in response.json()["items"]]

    assert await search("other") == ["olive@example.com"]  # by name, case-insensitive
    assert await search("PAT@") == ["pat@example.com"]  # by email
    assert await search("100%") == ["pat@example.com"]  # LIKE wildcards are literal
    assert await search("%") == ["pat@example.com"]


async def test_superadmin_has_no_implicit_access_to_tenant_data(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    root = await create_user(api)
    alice = await create_user(api)
    alice_org = await create_org(api, alice, "Alice Co")
    await make_superadmin(db_session, root)

    response = await api.get(f"{API}/organizations/{alice_org['id']}", headers=root.headers)

    assert response.status_code == 404


async def test_deactivating_a_user_ends_all_their_sessions(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    root = await create_user(api)
    target = await create_user(api)
    await make_superadmin(db_session, root)
    await api.post(f"{API}/auth/login", json={"email": target.email, "password": DEFAULT_PASSWORD})
    refresh_cookie = {"Cookie": f"{COOKIE}={api.cookies[COOKIE]}"}
    api.cookies.clear()

    deactivated = await api.patch(
        f"{ADMIN}/users/{target.id}", json={"is_active": False}, headers=root.headers
    )

    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    assert (await api.get(f"{API}/users/me", headers=target.headers)).status_code == 401
    refresh = await api.post(f"{API}/auth/refresh", headers=refresh_cookie)
    assert refresh.status_code == 401
    login = await api.post(
        f"{API}/auth/login", json={"email": target.email, "password": DEFAULT_PASSWORD}
    )
    assert login.status_code == 401

    await api.patch(f"{ADMIN}/users/{target.id}", json={"is_active": True}, headers=root.headers)
    relogin = await api.post(
        f"{API}/auth/login", json={"email": target.email, "password": DEFAULT_PASSWORD}
    )
    assert relogin.status_code == 200


async def test_superadmin_cannot_change_own_status(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    root = await create_user(api)
    await make_superadmin(db_session, root)

    response = await api.patch(
        f"{ADMIN}/users/{root.id}", json={"is_superadmin": False}, headers=root.headers
    )

    assert response.status_code == 403


async def test_admin_update_requires_a_field(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    root = await create_user(api)
    target = await create_user(api)
    await make_superadmin(db_session, root)

    response = await api.patch(f"{ADMIN}/users/{target.id}", json={}, headers=root.headers)

    assert response.status_code == 422


def test_cli_rejects_unknown_email(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli_main(["grant-superadmin", "nobody@example.com"])

    assert exit_code == 1
    assert "No user with email nobody@example.com" in capsys.readouterr().err
