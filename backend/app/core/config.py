"""Application settings.

Values resolve in increasing priority from: the defaults below, the repository-root
``.env``, ``backend/.env``, then real environment variables. Field names map to
upper-case variables, e.g. ``database_url`` <- ``DATABASE_URL``.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

# Development-only signing key. Rejected at startup outside development/test.
_DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-in-env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ----------------------------------------------------------
    project_name: str = "SupportIQ"
    version: str = "0.1.0"
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"

    # --- HTTP -----------------------------------------------------------------
    # Comma-separated in env: CORS_ORIGINS=http://localhost:3000,https://app.example.com
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    # --- Database -------------------------------------------------------------
    # Defaults match docker-compose.yml (host port 5433). Use 127.0.0.1, not
    # "localhost": on Windows, localhost tries IPv6 first and each new connection
    # stalls ~2s before falling back to the IPv4-only Docker port binding.
    #
    # Runtime role: least privilege, owns nothing, subject to row-level security.
    database_url: str = "postgresql+asyncpg://supportiq_app:supportiq_app@127.0.0.1:5433/supportiq"
    # Owner role, used only by Alembic (DDL, grants, policies). Never by the API.
    migration_database_url: str = (
        "postgresql+asyncpg://supportiq:supportiq@127.0.0.1:5433/supportiq"
    )
    # The role migrations grant table privileges to: the user in DATABASE_URL.
    database_app_role: Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_]*$")] = "supportiq_app"
    database_echo: bool = False
    health_check_timeout_seconds: float = 3.0  # readiness probe, per dependency

    # --- Redis ----------------------------------------------------------------
    redis_url: str = "redis://127.0.0.1:6379/0"

    # --- Rate limits (max attempts per window) ----------------------------------
    login_ip_limit: int = 20
    login_ip_window_seconds: int = 5 * 60
    login_account_failure_limit: int = 5  # then the account is locked for the window
    login_account_window_seconds: int = 15 * 60
    register_ip_limit: int = 10
    register_ip_window_seconds: int = 60 * 60

    # --- Email ------------------------------------------------------------------
    # "smtp" (dev: Mailpit, UI at http://127.0.0.1:8025) or "console" (log only).
    email_backend: Literal["smtp", "console"] = "smtp"
    email_from: str = "SupportIQ <no-reply@supportiq.local>"
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = False

    # --- Invitations ------------------------------------------------------------
    frontend_url: str = "http://localhost:3000"  # base for links in emails
    invitation_expire_days: int = 7

    # --- Maintenance --------------------------------------------------------------
    # Purge expired refresh tokens and stale invitations. 0 disables the loop.
    maintenance_interval_minutes: int = 60

    # --- Background jobs (Celery) ---------------------------------------------------
    # A separate Redis database from rate limits and real-time events.
    celery_broker_url: str = "redis://127.0.0.1:6379/1"

    # --- Object storage (original documents) ------------------------------------------
    # "s3": any S3-compatible service (dev: RustFS from docker-compose.yml).
    # "local": a directory on disk, for setups without object storage.
    storage_backend: Literal["s3", "local"] = "s3"
    s3_endpoint_url: str | None = "http://127.0.0.1:9000"  # None for AWS S3
    s3_access_key_id: str = "supportiq"
    s3_secret_access_key: SecretStr = SecretStr("supportiq-dev-secret")
    s3_region: str = "us-east-1"
    s3_bucket: str = "supportiq-documents"
    local_storage_dir: Path = REPO_ROOT / ".cache" / "storage"

    # --- Knowledge base -----------------------------------------------------------------
    qdrant_url: str = "http://127.0.0.1:6433"
    qdrant_api_key: SecretStr | None = None
    # One collection per embedding model (the model is appended), so switching
    # models never mixes vectors of different spaces.
    qdrant_collection_prefix: str = "supportiq_chunks"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # BGE v1.5 recommends this instruction for short queries against passages.
    embedding_query_prefix: str = "Represent this sentence for searching relevant passages: "
    model_cache_dir: Path = REPO_ROOT / ".cache" / "models"
    # BGE-small reads at most 512 tokens (510 plus [CLS]/[SEP]); longer chunks would be
    # silently truncated when embedded. Overlap keeps the plan's 15% ratio.
    chunk_size_tokens: Annotated[int, Field(ge=32, le=510)] = 480
    chunk_overlap_tokens: Annotated[int, Field(ge=0)] = 72
    # Retrieval: candidates from each retriever, Reciprocal Rank Fusion constant,
    # how many fused candidates the cross-encoder reorders, and passages returned.
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    retrieval_sparse_top_k: int = 20
    retrieval_dense_top_k: int = 20
    retrieval_rrf_k: int = 60
    retrieval_rerank_candidates: int = 20
    retrieval_top_k: int = 5
    bm25_k1: float = 1.2  # term-frequency saturation
    bm25_b: float = 0.75  # document-length normalization
    # OCR for scanned PDF pages: pages with fewer extractable characters than this
    # are rendered and read with OCR (about 1-2 s per page), up to a page limit.
    ocr_enabled: bool = True
    ocr_min_text_chars: int = 20
    ocr_max_pages: int = 200
    ocr_dpi: Annotated[int, Field(ge=72, le=400)] = 200
    # Upload and processing limits.
    max_upload_bytes: int = 20 * 1024 * 1024
    max_document_pages: int = 2_000
    max_extracted_characters: int = 5_000_000
    document_upload_limit: int = 30  # per user, per hour
    knowledge_search_limit: int = 60  # per user, per minute
    # Queued/processing longer than this means the worker died or the job was lost.
    ingestion_stale_minutes: int = 30

    # --- Answer generation ------------------------------------------------------------
    # "ollama": a local model (documents never leave the machine). "anthropic": Claude.
    llm_provider: Literal["ollama", "anthropic"] = "ollama"
    # Per question; the Next.js dev proxy allows 300 s, so keep this below that.
    llm_timeout_seconds: Annotated[float, Field(gt=0, le=290)] = 240
    ai_chat_limit: int = 20  # questions per user, per minute
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:4b"
    ollama_temperature: Annotated[float, Field(ge=0, le=2)] = 0.1
    ollama_max_output_tokens: Annotated[int, Field(ge=64)] = 1024
    # The model's context window (num_ctx): the prompt and the answer must fit in it.
    # Larger windows use more memory.
    ollama_context_tokens: Annotated[int, Field(ge=2048)] = 8192
    ollama_think: bool = False  # let reasoning models think first (much slower on CPU)
    ollama_keep_alive: str = "10m"  # how long Ollama keeps the model in memory when idle
    ollama_seed: int | None = None  # fixed sampling seed (evaluations set one)
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    anthropic_max_output_tokens: Annotated[int, Field(ge=1024)] = 16_000

    # --- Auth -----------------------------------------------------------------
    jwt_secret_key: SecretStr = SecretStr(_DEV_JWT_SECRET)
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    refresh_cookie_name: str = "supportiq_refresh"
    # Must be true whenever the API is served over HTTPS (enforced outside dev/test).
    refresh_cookie_secure: bool = False
    refresh_cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("frontend_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _check_chunking(self) -> Self:
        if self.chunk_overlap_tokens >= self.chunk_size_tokens // 2:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be less than half of CHUNK_SIZE_TOKENS")
        return self

    @model_validator(mode="after")
    def _check_llm_context(self) -> Self:
        if self.ollama_context_tokens < self.ollama_max_output_tokens + 1024:
            raise ValueError(
                "OLLAMA_CONTEXT_TOKENS must leave at least 1024 tokens for the prompt "
                "after OLLAMA_MAX_OUTPUT_TOKENS"
            )
        return self

    @model_validator(mode="after")
    def _enforce_auth_safety(self) -> Self:
        secret = self.jwt_secret_key.get_secret_value()
        if len(secret) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        if self.environment in ("development", "test"):
            return self
        if secret == _DEV_JWT_SECRET:
            raise ValueError(
                "JWT_SECRET_KEY must be set to a random value outside development/test"
            )
        if not self.refresh_cookie_secure:
            raise ValueError("REFRESH_COOKIE_SECURE must be true outside development/test")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def refresh_cookie_path(self) -> str:
        """The refresh cookie is only ever sent to the auth endpoints."""
        return f"{self.api_v1_prefix}/auth"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
