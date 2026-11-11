"""Telling users whether an indexing worker is running."""

import uuid

import httpx2
import pytest
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from app.workers.heartbeat import Heartbeat, RedisWorkerRegistry
from tests.factories import API, StaticWorkerRegistry, Workspace, in_org

pytestmark = pytest.mark.anyio

WORKERS = f"{API}/documents/workers"


@pytest.mark.parametrize(
    ("count", "expected"),
    [(2, {"status": "online", "workers": 2}), (0, {"status": "offline", "workers": 0})],
    ids=["online", "offline"],
)
async def test_worker_status(
    api: httpx2.AsyncClient,
    ws: Workspace,
    worker_registry: StaticWorkerRegistry,
    count: int,
    expected: dict,
) -> None:
    worker_registry.count = count

    response = await api.get(WORKERS, headers=in_org(ws.agent, ws.org_id))

    assert response.status_code == 200
    assert response.json() == expected


async def test_unknown_when_redis_is_unreachable(
    api: httpx2.AsyncClient, ws: Workspace, worker_registry: StaticWorkerRegistry
) -> None:
    worker_registry.count = None

    response = await api.get(WORKERS, headers=in_org(ws.admin, ws.org_id))

    assert response.json() == {"status": "unknown", "workers": None}


async def test_customers_cannot_ask(api: httpx2.AsyncClient, ws: Workspace) -> None:
    response = await api.get(WORKERS, headers=in_org(ws.customer, ws.org_id))

    assert response.status_code == 403


async def test_heartbeat_registers_and_withdraws_a_worker() -> None:
    prefix = f"test:workers:{uuid.uuid4().hex}:"
    sync_client = Redis.from_url("redis://127.0.0.1:6379/15")
    async_client = AsyncRedis.from_url("redis://127.0.0.1:6379/15")
    registry = RedisWorkerRegistry(async_client, prefix=prefix)
    heartbeat = Heartbeat(sync_client, "worker-a", prefix=prefix)
    try:
        assert await registry.live_workers() == 0
        heartbeat.start()
        assert await registry.live_workers() == 1
        assert 0 < sync_client.ttl(heartbeat.key) <= 60  # disappears if the worker dies
        heartbeat.stop()
        assert await registry.live_workers() == 0
    finally:
        heartbeat.stop()
        sync_client.close()
        await async_client.aclose()


async def test_registry_reports_unknown_when_redis_is_down() -> None:
    client = AsyncRedis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.5)
    try:
        assert await RedisWorkerRegistry(client).live_workers() is None
    finally:
        await client.aclose()
