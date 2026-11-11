"""A minimal in-process WebSocket client for ASGI apps.

Starlette's TestClient runs the app in another thread and event loop, which can't
share the test's async database session. This drives the ASGI websocket protocol
directly, in the test's own loop, so dependency overrides (database session,
event broker) apply exactly as they do for the HTTP client.
"""

import asyncio
import json
from types import TracebackType
from typing import Any

from fastapi import FastAPI


class Closed(Exception):
    def __init__(self, code: int, reason: str = "") -> None:
        super().__init__(f"closed with {code}: {reason}")
        self.code = code
        self.reason = reason


class WebSocketSession:
    def __init__(self, app: FastAPI, path: str) -> None:
        self.app = app
        self.path = path
        self._inbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._outbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "WebSocketSession":
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": self.path,
            "raw_path": self.path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "subprotocols": [],
            "state": {},
        }
        await self._inbound.put({"type": "websocket.connect"})
        self._task = asyncio.create_task(self.app(scope, self._inbound.get, self._outbound.put))
        message = await self._next()
        if message["type"] != "websocket.accept":
            raise Closed(message.get("code", 1000), message.get("reason", ""))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._inbound.put({"type": "websocket.disconnect", "code": 1000})
        assert self._task is not None
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except TimeoutError:
            self._task.cancel()
            raise

    async def _next(self, wait_seconds: float = 5) -> dict[str, Any]:
        return await asyncio.wait_for(self._outbound.get(), wait_seconds)

    async def send_json(self, data: Any) -> None:
        await self._inbound.put({"type": "websocket.receive", "text": json.dumps(data)})

    async def receive_json(self, wait_seconds: float = 5) -> Any:
        message = await self._next(wait_seconds)
        if message["type"] == "websocket.close":
            raise Closed(message.get("code", 1000), message.get("reason", ""))
        assert message["type"] == "websocket.send", message
        return json.loads(message["text"])

    async def expect_close(self, wait_seconds: float = 5) -> Closed:
        message = await self._next(wait_seconds)
        assert message["type"] == "websocket.close", message
        return Closed(message.get("code", 1000), message.get("reason", ""))

    async def nothing_received(self, within: float = 0.2) -> bool:
        try:
            message = await asyncio.wait_for(self._outbound.get(), within)
        except TimeoutError:
            return True
        await self._outbound.put(message)  # keep it for whoever asserts next
        return False
