from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import ping_database
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


def test_readiness_ok_when_database_reachable(client: TestClient) -> None:
    app.dependency_overrides[ping_database] = lambda: True

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok"}}


def test_readiness_503_when_database_unreachable(client: TestClient) -> None:
    app.dependency_overrides[ping_database] = lambda: False

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"database": "unavailable"}}
