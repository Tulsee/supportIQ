"""The process-wide language-model client, chosen by ``LLM_PROVIDER``."""

from functools import lru_cache

from ai_engine.generation.llm import ClaudeClient, LLMClient, OllamaClient
from app.core.config import Settings, settings


@lru_cache
def get_llm_client() -> LLMClient:
    return build_llm_client(settings)


@lru_cache
def get_local_llm_client() -> LLMClient:
    """The local model (Ollama) whatever ``LLM_PROVIDER`` says: organizations can keep
    their customers' questions on it (``self_service_model: local``)."""
    if settings.llm_provider == "ollama":
        return get_llm_client()
    return build_llm_client(settings.model_copy(update={"llm_provider": "ollama"}))


def build_llm_client(settings: Settings) -> LLMClient:
    """A client for ``settings`` (evaluations pass adjusted copies)."""
    if settings.llm_provider == "anthropic":
        key = settings.anthropic_api_key
        return ClaudeClient(
            settings.anthropic_model,
            api_key=key.get_secret_value() if key else None,
            effort=settings.anthropic_effort,
            max_output_tokens=settings.anthropic_max_output_tokens,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return OllamaClient(
        settings.ollama_url,
        settings.ollama_model,
        temperature=settings.ollama_temperature,
        max_output_tokens=settings.ollama_max_output_tokens,
        context_tokens=settings.ollama_context_tokens,
        think=settings.ollama_think,
        keep_alive=settings.ollama_keep_alive,
        timeout_seconds=settings.llm_timeout_seconds,
        seed=settings.ollama_seed,
    )


async def close_llm_client() -> None:
    if get_llm_client.cache_info().currsize:
        client = get_llm_client()
        if isinstance(client, ClaudeClient):
            await client.aclose()
        get_llm_client.cache_clear()
    get_local_llm_client.cache_clear()
