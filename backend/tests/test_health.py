import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import ping_database
from app.core.redis import ping_redis
from app.main import app


def test_liveness_returns_service_metadata(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": settings.project_name,
        "version": settings.version,
        "environment": "test",
    }


def test_readiness_ok_when_dependencies_reachable(client: TestClient) -> None:
    app.dependency_overrides[ping_database] = lambda: True
    app.dependency_overrides[ping_redis] = lambda: True

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


@pytest.mark.parametrize(
    ("database_ok", "redis_ok"), [(False, True), (True, False)], ids=["database", "redis"]
)
def test_readiness_503_when_a_dependency_is_down(
    client: TestClient, database_ok: bool, redis_ok: bool
) -> None:
    app.dependency_overrides[ping_database] = lambda: database_ok
    app.dependency_overrides[ping_redis] = lambda: redis_ok

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {
        "database": "ok" if database_ok else "unavailable",
        "redis": "ok" if redis_ok else "unavailable",
    }
