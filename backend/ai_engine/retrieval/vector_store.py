"""Chunk vectors in Qdrant.

Points carry identifiers only (organization, document, version, ingestion run,
chunk position), never chunk text: PostgreSQL stays the single source of truth for
content, under row-level security. Callers resolve hits back to chunk rows, which
also drops any vector whose chunk was replaced or deleted.

Multi-tenancy: one collection per embedding model, ``organization_id`` indexed as
the tenant key, and every search filtered by it.
"""

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient, models

VECTOR_NAME = "dense"
UPSERT_BATCH = 256


def collection_name(prefix: str, model_name: str) -> str:
    """E.g. ``supportiq_chunks`` + ``BAAI/bge-small-en-v1.5``
    -> ``supportiq_chunks__baai_bge_small_en_v1_5``."""
    return f"{prefix}__{re.sub(r'[^a-z0-9]+', '_', model_name.lower()).strip('_')}"


@dataclass(frozen=True)
class ChunkPoint:
    chunk_id: uuid.UUID
    vector: Sequence[float]
    organization_id: uuid.UUID
    document_id: uuid.UUID
    document_version: int
    ingestion_id: uuid.UUID
    chunk_index: int
    page: int | None

    def payload(self) -> dict[str, Any]:
        return {
            "organization_id": str(self.organization_id),
            "document_id": str(self.document_id),
            "document_version": self.document_version,
            "ingestion_id": str(self.ingestion_id),
            "chunk_index": self.chunk_index,
            "page": self.page,
        }


@dataclass(frozen=True)
class VectorHit:
    chunk_id: uuid.UUID
    score: float


def _match(key: str, value: object) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=str(value)))


class QdrantVectorStore:
    def __init__(self, client: AsyncQdrantClient, collection: str, dimensions: int) -> None:
        self.client = client
        self.collection = collection
        self.dimensions = dimensions

    async def ensure_collection(self) -> None:
        if await self.client.collection_exists(self.collection):
            return
        await self.client.create_collection(
            self.collection,
            vectors_config={
                VECTOR_NAME: models.VectorParams(
                    size=self.dimensions, distance=models.Distance.COSINE
                )
            },
        )
        await self.client.create_payload_index(
            self.collection,
            "organization_id",
            field_schema=models.KeywordIndexParams(
                type=models.KeywordIndexType.KEYWORD, is_tenant=True
            ),
        )
        for field in ("document_id", "ingestion_id"):
            await self.client.create_payload_index(
                self.collection, field, field_schema=models.PayloadSchemaType.KEYWORD
            )

    async def upsert(self, points: Sequence[ChunkPoint]) -> None:
        for i in range(0, len(points), UPSERT_BATCH):
            batch = points[i : i + UPSERT_BATCH]
            await self.client.upsert(
                self.collection,
                points=[
                    models.PointStruct(
                        id=str(point.chunk_id),
                        vector={VECTOR_NAME: list(point.vector)},
                        payload=point.payload(),
                    )
                    for point in batch
                ],
                wait=True,
            )

    async def delete_document(
        self, document_id: uuid.UUID, *, except_ingestion: uuid.UUID | None = None
    ) -> None:
        """Remove a document's vectors (optionally keeping one ingestion run's)."""
        if not await self.client.collection_exists(self.collection):
            return
        must_not = [_match("ingestion_id", except_ingestion)] if except_ingestion else None
        await self.client.delete(
            self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(must=[_match("document_id", document_id)], must_not=must_not)
            ),
            wait=True,
        )

    async def delete_ingestion(self, ingestion_id: uuid.UUID) -> None:
        """Remove what one (failed or superseded) ingestion run wrote."""
        if not await self.client.collection_exists(self.collection):
            return
        await self.client.delete(
            self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(must=[_match("ingestion_id", ingestion_id)])
            ),
            wait=True,
        )

    async def search(
        self, organization_id: uuid.UUID, vector: Sequence[float], *, limit: int
    ) -> list[VectorHit]:
        """Nearest chunks within one organization, best first."""
        if not await self.client.collection_exists(self.collection):
            return []
        response = await self.client.query_points(
            self.collection,
            query=list(vector),
            using=VECTOR_NAME,
            query_filter=models.Filter(must=[_match("organization_id", organization_id)]),
            limit=limit,
            with_payload=False,
        )
        return [VectorHit(uuid.UUID(str(point.id)), point.score) for point in response.points]
