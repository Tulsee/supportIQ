"""Language-model clients for answer generation.

One small interface, ``LLMClient``, with two implementations:

- ``OllamaClient``: a model served locally by Ollama (the default; documents never
  leave the machine).
- ``ClaudeClient``: Anthropic's hosted Claude models.

Each client describes the settings that shape its output, and each completion
reports the model that answered, token counts and timings, so every run records
exactly what produced it.
"""

import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import anthropic
import httpx2


class LLMError(Exception):
    """The model produced no answer: unreachable, timed out, or the request was
    rejected. The message is safe to show to staff (it never contains secrets)."""


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str


# Normalized across providers: "stop" (finished), "length" (hit the output limit),
# "refusal" (declined by the provider's safety systems), or the provider's own value.
StopReason = str


@dataclass(frozen=True)
class Completion:
    text: str
    model: str  # as reported by the provider (it can differ from the one requested)
    stop_reason: StopReason | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model_version: str | None = None  # e.g. the Ollama model digest
    timings_ms: dict[str, float] = field(default_factory=dict)


ModelState = Literal["ready", "unreachable", "not_installed", "not_configured"]


@dataclass(frozen=True)
class ModelStatus:
    state: ModelState
    detail: str | None = None

    @property
    def available(self) -> bool:
        return self.state == "ready"


class LLMClient(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def config(self) -> dict[str, Any]:
        """Every setting that affects the output (recorded with each run)."""
        ...

    @property
    def prompt_token_budget(self) -> int | None:
        """Tokens the prompt may use, or None when the context window is effectively
        unlimited for a few passages."""
        ...

    async def complete(self, prompt: Prompt) -> Completion: ...

    async def status(self) -> ModelStatus:
        """Whether the model can answer now (cheap; never runs the model)."""
        ...


def _ms(nanoseconds: int | None) -> float | None:
    return round(nanoseconds / 1_000_000, 2) if nanoseconds is not None else None


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


class OllamaClient:
    """A local model through Ollama's ``/api/chat`` (https://ollama.com)."""

    provider = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        temperature: float,
        max_output_tokens: int,
        context_tokens: int,
        think: bool,
        keep_alive: str,
        timeout_seconds: float,
        seed: int | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,  # tests
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.context_tokens = context_tokens
        self.think = think
        self.keep_alive = keep_alive
        self.timeout_seconds = timeout_seconds
        self.seed = seed  # fixed sampling seed, for reproducible evaluations
        self._transport = transport

    @property
    def model(self) -> str:
        return self._model

    @property
    def config(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "context_tokens": self.context_tokens,
            "think": self.think,
            "keep_alive": self.keep_alive,
            "seed": self.seed,
        }

    @property
    def prompt_token_budget(self) -> int:
        return self.context_tokens - self.max_output_tokens

    def _http(self, timeout: float) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            base_url=self.base_url,
            timeout=httpx2.Timeout(timeout, connect=5.0),
            transport=self._transport,
        )

    def _installed(self, tags: dict[str, Any]) -> dict[str, Any] | None:
        """This model's entry in ``/api/tags`` (a bare name means its ``:latest`` tag)."""
        wanted = {self._model, f"{self._model}:latest"}
        return next(
            (entry for entry in tags.get("models", []) if entry.get("name") in wanted), None
        )

    async def complete(self, prompt: Prompt) -> Completion:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            "stream": False,
            # Reasoning models (Qwen 3.x, ...) otherwise think at length before answering.
            "think": self.think,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_output_tokens,
                "num_ctx": self.context_tokens,
                **({"seed": self.seed} if self.seed is not None else {}),
            },
        }
        started = time.perf_counter()
        try:
            async with self._http(self.timeout_seconds) as http:
                response = await http.post("/api/chat", json=body)
                version = None
                if response.is_success:
                    version = await self._digest(http)
        except (httpx2.ConnectError, httpx2.ConnectTimeout) as exc:
            raise LLMError(f"Couldn't reach Ollama at {self.base_url}. Is it running?") from exc
        except httpx2.TimeoutException as exc:
            raise LLMError(
                f"The model took longer than {self.timeout_seconds:g} s to answer."
            ) from exc
        except httpx2.HTTPError as exc:
            raise LLMError(f"The request to Ollama failed ({type(exc).__name__}).") from exc

        if response.status_code == 404:
            raise LLMError(f"The model {self._model} isn't installed in Ollama.")
        if not response.is_success:
            raise LLMError(f"Ollama returned an error ({response.status_code}): {_error(response)}")

        data = response.json()
        timings = {
            name: value
            for name, value in (
                ("load", _ms(data.get("load_duration"))),
                ("prompt", _ms(data.get("prompt_eval_duration"))),
                ("output", _ms(data.get("eval_duration"))),
            )
            if value is not None
        }
        timings["total"] = _elapsed_ms(started)
        return Completion(
            text=data.get("message", {}).get("content", ""),
            model=data.get("model", self._model),
            stop_reason=data.get("done_reason"),  # "stop" or "length"
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            model_version=version,
            timings_ms=timings,
        )

    async def _digest(self, http: httpx2.AsyncClient) -> str | None:
        """The installed model's digest: the exact weights a tag pointed to (best effort)."""
        try:
            response = await http.get("/api/tags", timeout=3.0)
            entry = self._installed(response.json()) if response.is_success else None
        except (httpx2.HTTPError, ValueError):
            return None
        return entry.get("digest") if entry else None

    async def status(self) -> ModelStatus:
        try:
            async with self._http(3.0) as http:
                response = await http.get("/api/tags")
                response.raise_for_status()
                tags = response.json()
        except (httpx2.HTTPError, ValueError):
            return ModelStatus("unreachable", f"Ollama isn't responding at {self.base_url}.")
        if self._installed(tags) is None:
            return ModelStatus("not_installed", f"{self._model} isn't installed in Ollama.")
        return ModelStatus("ready")


def _error(response: httpx2.Response) -> str:
    try:
        return str(response.json().get("error", ""))[:300]
    except ValueError:
        return response.text[:300]


# Claude stop reasons -> the normalized values above.
_CLAUDE_STOP = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length"}


class ClaudeClient:
    """Claude through the Anthropic API.

    Claude Opus 5.5 takes no sampling parameters (temperature, top_p); ``effort``
    is the quality/latency control. Server-side refusal fallbacks are enabled, so a
    request a safety classifier declines is retried on a fallback model in the same
    call (the completion reports the model that actually answered).
    """

    provider = "anthropic"
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None,
        effort: str,
        max_output_tokens: int,
        timeout_seconds: float,
        http_client: httpx2.AsyncClient | None = None,  # tests
    ) -> None:
        self._model = model
        self._api_key = api_key
        self.effort = effort
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def model(self) -> str:
        return self._model

    @property
    def config(self) -> dict[str, Any]:
        return {
            "effort": self.effort,
            "max_output_tokens": self.max_output_tokens,
            "fallbacks": "default",
        }

    @property
    def prompt_token_budget(self) -> None:
        return None  # a 1M-token context window

    def _anthropic(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            # Without an explicit key the SDK resolves ANTHROPIC_API_KEY, an auth
            # token, or an `ant auth login` profile.
            options: dict[str, Any] = {"timeout": self.timeout_seconds, "max_retries": 2}
            if self._http_client is not None:
                options["http_client"] = self._http_client
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key, **options)
        return self._client

    async def complete(self, prompt: Prompt) -> Completion:
        started = time.perf_counter()
        try:
            response = await self._anthropic().beta.messages.create(
                model=self._model,
                max_tokens=self.max_output_tokens,
                system=prompt.system,
                messages=[{"role": "user", "content": prompt.user}],
                output_config={"effort": self.effort},
                betas=[self.FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as exc:
            raise LLMError("The Anthropic API key was rejected.") from exc
        except anthropic.NotFoundError as exc:
            raise LLMError(f"The Anthropic API doesn't know the model {self._model}.") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("The Anthropic API is rate limiting requests. Try again soon.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"The Anthropic API returned an error ({exc.status_code}).") from exc
        except anthropic.APITimeoutError as exc:
            raise LLMError(
                f"Claude took longer than {self.timeout_seconds:g} s to answer."
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Couldn't reach the Anthropic API.") from exc
        except TypeError as exc:
            # The SDK raises TypeError when it finds no credentials at all.
            if "authentication" in str(exc).lower():
                raise LLMError("No Anthropic API key is configured.") from exc
            raise

        text = "".join(block.text for block in response.content if block.type == "text")
        stop = response.stop_reason
        return Completion(
            text=text,
            model=response.model,
            stop_reason=_CLAUDE_STOP.get(stop, stop) if stop else None,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            timings_ms={"total": _elapsed_ms(started)},
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def status(self) -> ModelStatus:
        # Checking the key would cost a request; it is verified on the first question.
        if (
            self._api_key
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        ):
            return ModelStatus("ready")
        return ModelStatus("not_configured", "No Anthropic API key is configured.")
