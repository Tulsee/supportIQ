"""Every error, expected or not, must use the ``{"error": {...}}`` envelope."""

from collections.abc import Iterator

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.exceptions import NotFoundError
from app.main import create_app


@pytest.fixture
def error_client() -> Iterator[TestClient]:
    """A fresh app with extra routes that raise each kind of error."""
    app = create_app()
    router = APIRouter(prefix="/_test")

    @router.get("/app-error")
    async def raise_app_error() -> None:
        raise NotFoundError("Ticket not found")

    @router.get("/crash")
    async def raise_unexpected() -> None:
        raise RuntimeError("secret internal detail")

    @router.get("/validated")
    async def validated(limit: int) -> dict[str, int]:
        return {"limit": limit}

    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_unknown_route_returns_404_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


def test_wrong_method_returns_405_envelope(client: TestClient) -> None:
    response = client.post("/api/v1/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_app_error_maps_to_its_status_and_code(error_client: TestClient) -> None:
    response = error_client.get("/_test/app-error")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Ticket not found"}}


def test_validation_error_lists_fields_without_echoing_input(error_client: TestClient) -> None:
    response = error_client.get("/_test/validated", params={"limit": "not-a-number"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["query", "limit"]
    assert "not-a-number" not in response.text


def test_unhandled_exception_returns_generic_500(error_client: TestClient) -> None:
    response = error_client.get("/_test/crash")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"}
    }
    assert "secret internal detail" not in response.text
