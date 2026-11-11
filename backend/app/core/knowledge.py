"""Process-wide knowledge-base clients: the embedding model, OCR, and Qdrant."""

from functools import lru_cache

from qdrant_client import AsyncQdrantClient

from ai_engine.embeddings.embedder import Embedder, FastEmbedEmbedder
from ai_engine.ingestion.ocr import OcrEngine, RapidOcrEngine
from ai_engine.reranking.cross_encoder import FastEmbedReranker, Reranker
from ai_engine.retrieval.vector_store import QdrantVectorStore, collection_name
from app.core.config import settings


@lru_cache
def get_embedder() -> Embedder:
    """Loaded lazily on first use (the first call may download the model)."""
    return FastEmbedEmbedder(
        settings.embedding_model,
        cache_dir=settings.model_cache_dir,
        query_prefix=settings.embedding_query_prefix,
    )


@lru_cache
def get_reranker() -> Reranker:
    """Loaded lazily on first rerank (the first call may download the model)."""
    return FastEmbedReranker(settings.reranker_model, cache_dir=settings.model_cache_dir)


@lru_cache
def get_ocr_engine() -> OcrEngine:
    """Loaded lazily on first scanned page (models ship with the package)."""
    return RapidOcrEngine()


def new_qdrant_client() -> AsyncQdrantClient:
    api_key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
    return AsyncQdrantClient(url=settings.qdrant_url, api_key=api_key, timeout=30)


@lru_cache
def get_qdrant_client() -> AsyncQdrantClient:
    """The API process's client (bound to its event loop; closed on shutdown)."""
    return new_qdrant_client()


async def close_qdrant_client() -> None:
    if get_qdrant_client.cache_info().currsize:
        await get_qdrant_client().close()
        get_qdrant_client.cache_clear()


def vector_store_for(client: AsyncQdrantClient, embedder: Embedder) -> QdrantVectorStore:
    collection = collection_name(settings.qdrant_collection_prefix, embedder.model_name)
    return QdrantVectorStore(client, collection, embedder.dimensions)
