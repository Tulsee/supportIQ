"""Row-level security, tested at the database layer.

These queries deliberately omit every ``WHERE organization_id = ...`` filter: they
simulate an application bug and check that Postgres still enforces the boundary.
"""

import uuid

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import reset_rls_context, rls_bypass, set_rls_context
from tests.factories import AuthedUser, create_org, create_user, invite

pytestmark = pytest.mark.anyio


async def org_ids(session: AsyncSession, table: str) -> set[str]:
    column = "id" if table == "organizations" else "organization_id"
    rows = await session.execute(text(f"SELECT {column} FROM {table}"))  # no filter on purpose
    return {str(value) for (value,) in rows}


@pytest.fixture
async def two_tenants(api: httpx2.AsyncClient) -> tuple[AuthedUser, dict, AuthedUser, dict]:
    alice = await create_user(api)
    bob = await create_user(api)
    alice_org = await create_org(api, alice, "Alice Co")
    bob_org = await create_org(api, bob, "Bob Co")
    await invite(api, alice, alice_org["id"], "guest-a@example.com", "agent")
    await invite(api, bob, bob_org["id"], "guest-b@example.com", "agent")
    return alice, alice_org, bob, bob_org


async def test_unfiltered_queries_only_see_the_current_tenant(
    db_session: AsyncSession, two_tenants: tuple
) -> None:
    alice, alice_org, _, _ = two_tenants
    await set_rls_context(
        db_session, organization_id=uuid.UUID(alice_org["id"]), user_id=uuid.UUID(alice.id)
    )

    for table in ("organizations", "organization_members", "organization_invitations"):
        assert await org_ids(db_session, table) == {alice_org["id"]}, table


async def test_without_context_no_tenant_rows_are_visible(
    db_session: AsyncSession, two_tenants: tuple
) -> None:
    await reset_rls_context(db_session)

    for table in ("organizations", "organization_members", "organization_invitations"):
        assert await org_ids(db_session, table) == set(), table


async def test_bypass_sees_every_tenant(db_session: AsyncSession, two_tenants: tuple) -> None:
    _, alice_org, _, bob_org = two_tenants

    async with rls_bypass(db_session):
        visible = await org_ids(db_session, "organization_invitations")

    assert visible == {alice_org["id"], bob_org["id"]}


async def test_writes_to_another_tenant_are_blocked(
    db_session: AsyncSession, two_tenants: tuple
) -> None:
    alice, alice_org, _, bob_org = two_tenants
    await reset_rls_context(db_session)
    await set_rls_context(db_session, organization_id=uuid.UUID(alice_org["id"]))

    renamed = await db_session.execute(
        text("UPDATE organizations SET name = 'pwned' WHERE id = :id"), {"id": bob_org["id"]}
    )
    assert renamed.rowcount == 0  # Bob's org is invisible, so nothing matches

    with pytest.raises(DBAPIError, match="row-level security"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO organization_members (id, organization_id, user_id, role) "
                    "VALUES (:id, :org, :user, 'admin')"
                ),
                {"id": uuid.uuid4(), "org": bob_org["id"], "user": alice.id},
            )


async def test_app_role_cannot_bypass_rls(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT rolsuper, rolbypassrls, "
                "(SELECT tableowner FROM pg_tables WHERE tablename = 'organizations') "
                "FROM pg_roles WHERE rolname = current_user"
            )
        )
    ).one()

    assert row.rolsuper is False
    assert row.rolbypassrls is False
    assert row.tableowner != (await db_session.scalar(text("SELECT current_user")))


async def test_context_is_reapplied_to_every_new_transaction(
    session_factory: async_sessionmaker,
) -> None:
    """Production sessions commit for real, ending the transaction (and its
    transaction-local settings); the session must re-apply its context each time."""
    org_id = uuid.uuid4()
    current = text("SELECT app_current_organization_id()")
    async with session_factory() as session:
        await set_rls_context(session, organization_id=org_id)  # before any transaction
        assert await session.scalar(current) == org_id
        await session.commit()

        assert await session.scalar(current) == org_id  # a brand-new transaction
        await session.rollback()

        async with rls_bypass(session):
            assert await session.scalar(text("SELECT app_rls_bypass()")) is True
        assert await session.scalar(text("SELECT app_rls_bypass()")) is False


async def test_context_is_transaction_local(migrated_database: str) -> None:
    """Settings vanish at commit, so a pooled connection can't leak them."""
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    text("SELECT set_config('app.current_organization_id', :org, true)"),
                    {"org": str(uuid.uuid4())},
                )
            after = await connection.scalar(text("SELECT app_current_organization_id()"))
            await connection.rollback()
    finally:
        await engine.dispose()

    assert after is None
