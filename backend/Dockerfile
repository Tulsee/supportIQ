# syntax=docker/dockerfile:1
# The API, the ingestion worker and one-off commands (migrations, evaluations) share
# this image. Build from the repository root, so the evaluation datasets are included:
#   docker build -f backend/Dockerfile -t supportiq-backend .

FROM ghcr.io/astral-sh/uv:0.12.5 AS uv

FROM python:3.12-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app/backend
# Dependencies only (exact versions from uv.lock), so code changes don't reinstall them.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=backend/uv.lock,target=uv.lock \
    --mount=type=bind,source=backend/pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

FROM python:3.12-slim-bookworm
RUN useradd --create-home --uid 10001 supportiq \
    && mkdir /models && chown supportiq:supportiq /models
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Embedding, reranking, claim-check and OCR models download here on first use;
    # mount a volume to keep them between containers.
    MODEL_CACHE_DIR=/models \
    HF_HUB_DISABLE_TELEMETRY=1
WORKDIR /app
COPY --chown=supportiq:supportiq datasets ./datasets
COPY --chown=supportiq:supportiq backend ./backend
WORKDIR /app/backend
USER supportiq
EXPOSE 8000
# Behind the reverse proxy: trust its X-Forwarded-* headers (the API port itself is
# not published), so rate limits see the real client address.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
