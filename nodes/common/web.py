"""HTTP plumbing shared by every node and service (FastAPI + uvicorn + httpx)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


class NodeError(Exception):
    """An error a node wants to return to its caller with a status code."""

    def __init__(self, status: int, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


def create_app(title: str, description: str, lifespan=None) -> FastAPI:
    app = FastAPI(title=title, description=description, lifespan=lifespan,
                  docs_url="/docs", redoc_url=None)

    @app.exception_handler(NodeError)
    async def _node_error(_: Request, exc: NodeError):
        return JSONResponse(status_code=exc.status, content={"error": exc.message, **exc.extra})

    return app


def run(app: FastAPI, host: str, port: int) -> None:
    """Serve until SIGTERM/SIGINT; uvicorn finishes in-flight requests first."""
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False,
                timeout_graceful_shutdown=5)


def client(timeout: float = 10.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=3.0))


def error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            return str(body.get("error") or body.get("detail") or body)
        return str(body)
    except (ValueError, json.JSONDecodeError):
        return resp.text[:500] or f"HTTP {resp.status_code}"


def sse_response(gen: AsyncIterator[dict[str, Any]]) -> StreamingResponse:
    """Server-Sent Events: one `data: <json>` frame per item, plus keep-alives."""

    async def frames() -> AsyncIterator[bytes]:
        queue: asyncio.Queue = asyncio.Queue()
        done = object()

        async def pump():
            try:
                async for item in gen:
                    await queue.put(item)
            finally:
                await queue.put(done)

        task = asyncio.create_task(pump())
        try:
            yield b": connected\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield b": keep-alive\n\n"
                    continue
                if item is done:
                    break
                event = item.get("event") if isinstance(item, dict) else None
                payload = json.dumps(item.get("data", item) if event else item, ensure_ascii=False)
                prefix = f"event: {event}\n" if event else ""
                yield f"{prefix}data: {payload}\n\n".encode()
        finally:
            task.cancel()

    return StreamingResponse(frames(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def read_sse(resp: httpx.Response) -> AsyncIterator[tuple[str | None, str]]:
    """Parse an SSE stream into (event, data) tuples."""
    event: str | None = None
    data_lines: list[str] = []
    async for line in resp.aiter_lines():
        if line == "":
            if data_lines:
                yield event, "\n".join(data_lines)
            event, data_lines = None, []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
