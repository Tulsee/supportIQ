import uuid
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import rls_bypass
from app.core.security import hash_token
from app.models import OrganizationInvitation, OrgRole, RefreshToken
from app.services.maintenance import _MAINTENANCE_LOCK_ID, purge_expired, run_maintenance_once
from tests.factories import create_org, create_user

pytestmark = pytest.mark.anyio

NOW = datetime.now(UTC)


async def test_purge_removes_only_expired_tokens_and_stale_invitations(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    user = await create_user(api)
    org = await create_org(api, user)
    user_id, org_id = uuid.UUID(user.id), uuid.UUID(org["id"])

    def token(expires_at: datetime, revoked_at: datetime | None = None) -> RefreshToken:
        return RefreshToken(
            user_id=user_id,
            family_id=uuid.uuid4(),
            token_hash=hash_token(uuid.uuid4().hex),
            expires_at=expires_at,
            revoked_at=revoked_at,
        )

    def invitation(**fields: datetime | None) -> OrganizationInvitation:
        return OrganizationInvitation(
            organization_id=org_id,
            email=f"{uuid.uuid4().hex[:8]}@example.com",
            role=OrgRole.AGENT,
            token_hash=hash_token(uuid.uuid4().hex),
            **{"expires_at": NOW + timedelta(days=7), **fields},
        )

    async with rls_bypass(db_session):
        await db_session.execute(text("DELETE FROM refresh_tokens"))  # drop login tokens
        db_session.add_all(
            [
                token(NOW - timedelta(seconds=1)),  # expired -> purged
                token(NOW + timedelta(days=1)),  # valid -> kept
                # Rotated but unexpired: kept so a replay is still detected.
                token(NOW + timedelta(days=1), revoked_at=NOW - timedelta(days=1)),
                invitation(expires_at=NOW - timedelta(days=31)),  # long expired -> purged
                invitation(revoked_at=NOW - timedelta(days=31)),  # long revoked -> purged
                invitation(expires_at=NOW - timedelta(days=1)),  # recently expired -> kept
                invitation(),  # pending -> kept
                invitation(  # accepted long ago -> kept as a record
                    expires_at=NOW - timedelta(days=60), accepted_at=NOW - timedelta(days=59)
                ),
            ]
        )
        await db_session.flush()

        result = await purge_expired(db_session)

        assert (result.refresh_tokens, result.invitations) == (1, 2)
        assert await db_session.scalar(select(func.count()).select_from(RefreshToken)) == 2
        remaining = select(func.count()).select_from(OrganizationInvitation)
        assert await db_session.scalar(remaining) == 3


async def test_concurrent_maintenance_runs_are_skipped(
    db_session: AsyncSession, session_factory: async_sessionmaker
) -> None:
    # Another "worker" holds the maintenance lock in its transaction...
    await db_session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": _MAINTENANCE_LOCK_ID}
    )

    # ...so this run (separate connection) skips instead of duplicating the work.
    assert await run_maintenance_once(session_factory) is None


async def test_maintenance_runs_when_lock_is_free(session_factory: async_sessionmaker) -> None:
    result = await run_maintenance_once(session_factory)

    assert result is not None
