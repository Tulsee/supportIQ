import os

# Must be set before the app (and its settings) are imported.
os.environ.setdefault("ENVIRONMENT", "test")

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
