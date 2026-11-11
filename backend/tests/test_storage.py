"""Object storage backends: local directory and S3-compatible (RustFS in dev/CI)."""

import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.storage import LocalObjectStorage, ObjectStorage, S3ObjectStorage, StorageError

pytestmark = pytest.mark.anyio


async def exercise(storage: ObjectStorage) -> None:
    await storage.put("documents/org/doc/v1/a.md", b"first", "text/markdown")
    await storage.put("documents/org/doc/v2/b.md", b"second", "text/markdown")
    await storage.put("documents/other/c.md", b"other", "text/markdown")

    assert await storage.get("documents/org/doc/v1/a.md") == b"first"
    assert sorted(await storage.list_keys("documents/org/doc/")) == [
        "documents/org/doc/v1/a.md",
        "documents/org/doc/v2/b.md",
    ]

    await storage.delete("documents/org/doc/v1/a.md")
    await storage.delete("documents/org/doc/v1/a.md")  # idempotent
    assert await storage.list_keys("documents/org/doc/") == ["documents/org/doc/v2/b.md"]
    with pytest.raises(StorageError):
        await storage.get("documents/org/doc/v1/a.md")


async def test_local_storage(tmp_path: Path) -> None:
    await exercise(LocalObjectStorage(tmp_path))


async def test_local_storage_rejects_keys_outside_its_root(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "root")

    with pytest.raises(StorageError, match="Invalid storage key"):
        await storage.put("../escape.txt", b"x", "text/plain")


async def test_s3_storage() -> None:
    """Against the S3-compatible service from docker-compose.yml (CI: a service
    container), in a throwaway bucket."""
    storage = S3ObjectStorage(
        bucket=f"supportiq-test-{uuid.uuid4().hex[:12]}",
        endpoint_url=settings.s3_endpoint_url,
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        region=settings.s3_region,
    )
    try:
        await exercise(storage)
    finally:
        for key in await storage.list_keys(""):
            await storage.delete(key)
        storage._client.delete_bucket(Bucket=storage.bucket)
