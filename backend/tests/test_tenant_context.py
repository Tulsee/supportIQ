"""The ``X-Organization-Id`` tenant context that Module 2+ resources will use."""

import httpx2
import pytest
from fastapi import FastAPI

from app.api.deps import TenantMembership
from app.main import create_app
from tests.conftest import ClientFactory
from tests.factories import create_org, create_user

pytestmark = pytest.mark.anyio

ROUTE = "/_test/tenant"


@pytest.fixture
def tenant_app() -> FastAPI:
    """The real app plus a probe route that depends on the tenant context."""
    test_app = create_app()

    @test_app.get(ROUTE)
    async def tenant_probe(membership: TenantMembership) -> dict[str, str]:
        return {"organization_id": str(membership.organization_id), "role": membership.role}

    return test_app


async def test_tenant_header_resolves_the_callers_membership(
    api: httpx2.AsyncClient, make_client: ClientFactory, tenant_app: FastAPI
) -> None:
    owner = await create_user(api)
    outsider = await create_user(api)
    org = await create_org(api, owner)
    tenant = {"X-Organization-Id": org["id"]}

    async with make_client(tenant_app) as client:
        member = await client.get(ROUTE, headers={**owner.headers, **tenant})
        missing_header = await client.get(ROUTE, headers=owner.headers)
        non_member = await client.get(ROUTE, headers={**outsider.headers, **tenant})
        anonymous = await client.get(ROUTE, headers=tenant)

    assert member.status_code == 200
    assert member.json() == {"organization_id": org["id"], "role": "admin"}
    assert missing_header.status_code == 422
    assert non_member.status_code == 404
    assert anonymous.status_code == 401
