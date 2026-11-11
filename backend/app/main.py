"""SupportIQ API: FastAPI application entry point.

Run from the ``backend`` directory (uv creates/syncs the venv automatically)::

    uv run uvicorn app.main:app --reload

Interactive API docs (disabled in production): http://127.0.0.1:8000/docs
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.exceptions import register_exception_handlers
from app.core.knowledge import close_qdrant_client
from app.core.llm import close_llm_client
from app.core.logging import configure_logging
from app.core.redis import close_redis
from app.services.maintenance import maintenance_loop

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "Starting %s API v%s (%s)", settings.project_name, settings.version, settings.environment
    )
    maintenance: asyncio.Task[None] | None = None
    if settings.maintenance_interval_minutes > 0 and settings.environment != "test":
        maintenance = asyncio.create_task(
            maintenance_loop(SessionLocal, settings.maintenance_interval_minutes)
        )
    yield
    if maintenance is not None:
        maintenance.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await maintenance
    await close_redis()
    await close_qdrant_client()
    await close_llm_client()
    await engine.dispose()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    configure_logging(settings.log_level)
    docs_enabled = not settings.is_production

    app = FastAPI(
        title=f"{settings.project_name} API",
        version=settings.version,
        lifespan=lifespan,
        openapi_url=f"{settings.api_v1_prefix}/openapi.json" if docs_enabled else None,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
