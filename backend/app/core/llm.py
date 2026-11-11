"""The process-wide language-model client, chosen by ``LLM_PROVIDER``."""

from functools import lru_cache

from ai_engine.generation.llm import ClaudeClient, LLMClient, OllamaClient
from app.core.config import settings


@lru_cache
def get_llm_client() -> LLMClient:
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
    )


async def close_llm_client() -> None:
    if get_llm_client.cache_info().currsize:
        client = get_llm_client()
        if isinstance(client, ClaudeClient):
            await client.aclose()
        get_llm_client.cache_clear()
