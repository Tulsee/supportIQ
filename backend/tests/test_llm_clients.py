"""Model clients against mocked HTTP: what they send, and how replies and failures
are read. No model runs and nothing leaves the machine."""

import json
from collections.abc import Callable

import httpx2
import pytest

from ai_engine.generation.llm import ClaudeClient, LLMError, OllamaClient, Prompt

pytestmark = pytest.mark.anyio

PROMPT = Prompt(system="Rules.", user="<passages>...</passages>\n\nQuestion: q?")
Handler = Callable[[httpx2.Request], httpx2.Response]

TAGS = {"models": [{"name": "qwen3.5:4b", "digest": "abc123"}]}


def ollama(handler: Handler, **overrides: object) -> OllamaClient:
    options: dict = {
        "temperature": 0.1,
        "max_output_tokens": 512,
        "context_tokens": 4096,
        "think": False,
        "keep_alive": "5m",
        "timeout_seconds": 30,
        "transport": httpx2.MockTransport(handler),
    }
    return OllamaClient("http://ollama.test", "qwen3.5:4b", **{**options, **overrides})


# --- Ollama --------------------------------------------------------------------------------------


async def test_ollama_sends_the_prompt_and_settings_and_reads_the_reply() -> None:
    sent: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/tags":
            return httpx2.Response(200, json=TAGS)
        sent.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "model": "qwen3.5:4b",
                "message": {"role": "assistant", "content": "Use WPA3 [1]."},
                "done": True,
                "done_reason": "stop",
                "load_duration": 2_000_000_000,
                "prompt_eval_count": 812,
                "prompt_eval_duration": 4_500_000_000,
                "eval_count": 9,
                "eval_duration": 1_250_000_000,
            },
        )

    client = ollama(handler)
    completion = await client.complete(PROMPT)

    body = sent[0]
    assert body["model"] == "qwen3.5:4b" and body["stream"] is False
    assert body["messages"] == [
        {"role": "system", "content": "Rules."},
        {"role": "user", "content": PROMPT.user},
    ]
    assert body["think"] is False and body["keep_alive"] == "5m"
    assert body["options"] == {"temperature": 0.1, "num_predict": 512, "num_ctx": 4096}
    assert completion.text == "Use WPA3 [1]."
    assert completion.stop_reason == "stop"
    assert (completion.input_tokens, completion.output_tokens) == (812, 9)
    assert completion.model_version == "abc123"
    assert completion.timings_ms["load"] == 2000.0
    assert completion.timings_ms["prompt"] == 4500.0
    assert completion.timings_ms["output"] == 1250.0
    assert client.prompt_token_budget == 4096 - 512
    assert client.config["temperature"] == 0.1


async def test_ollama_not_running() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    client = ollama(handler)

    with pytest.raises(LLMError, match="Couldn't reach Ollama"):
        await client.complete(PROMPT)
    status = await client.status()
    assert status.state == "unreachable" and not status.available


async def test_ollama_model_not_installed() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/tags":
            return httpx2.Response(200, json={"models": [{"name": "llama3:8b"}]})
        return httpx2.Response(404, json={"error": "model 'qwen3.5:4b' not found"})

    client = ollama(handler)

    with pytest.raises(LLMError, match=r"qwen3\.5:4b isn't installed"):
        await client.complete(PROMPT)
    assert (await client.status()).state == "not_installed"


async def test_ollama_too_slow() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    with pytest.raises(LLMError, match="longer than 30 s"):
        await ollama(handler).complete(PROMPT)


async def test_ollama_ready_with_a_bare_model_name() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"models": [{"name": "mistral:latest"}]})

    client = OllamaClient(
        "http://ollama.test",
        "mistral",
        temperature=0,
        max_output_tokens=256,
        context_tokens=2048,
        think=False,
        keep_alive="1m",
        timeout_seconds=5,
        transport=httpx2.MockTransport(handler),
    )

    assert (await client.status()).available


# --- Claude --------------------------------------------------------------------------------------


def claude(handler: Handler, api_key: str | None = "sk-ant-test") -> ClaudeClient:
    return ClaudeClient(
        "claude-opus-5-5",
        api_key=api_key,
        effort="medium",
        max_output_tokens=16_000,
        timeout_seconds=30,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def message(text: str, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 900, "output_tokens": 40},
    }


async def test_claude_request_and_reply() -> None:
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, json=message("Use WPA3 [1]."))

    completion = await claude(handler).complete(PROMPT)

    request = sent[0]
    body = json.loads(request.content)
    assert request.url.path == "/v1/messages"
    assert request.headers["authorization"] == "Bearer sk-ant-test"
    assert ClaudeClient.FALLBACK_BETA in request.headers["anthropic-beta"]
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 16_000
    assert body["system"] == "Rules."
    assert body["messages"] == [{"role": "user", "content": PROMPT.user}]
    assert body["output_config"] == {"effort": "medium"}
    assert body["fallbacks"] == "default"
    assert "temperature" not in body  # Claude Opus 5.5 takes no sampling parameters
    assert completion.text == "Use WPA3 [1]."
    assert completion.stop_reason == "stop"
    assert (completion.input_tokens, completion.output_tokens) == (900, 40)


async def test_claude_hitting_the_output_limit_or_refusing() -> None:
    replies = iter([message("Partial", "max_tokens"), message("", "refusal")])

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=next(replies))

    client = claude(handler)

    assert (await client.complete(PROMPT)).stop_reason == "length"
    assert (await client.complete(PROMPT)).stop_reason == "refusal"


async def test_claude_rejected_key() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            401,
            json={
                "type": "error",
                "error": {"type": "authentication_error", "message": "invalid x-api-key"},
            },
        )

    with pytest.raises(LLMError, match="key was rejected") as raised:
        await claude(handler).complete(PROMPT)
    assert "sk-ant-test" not in str(raised.value)


async def test_claude_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("no request without credentials")

    status = await claude(handler, api_key=None).status()

    assert status.state == "not_configured"
