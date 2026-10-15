"""Client for the agent service — the shape the SwiftUI `AgentClient` mirrors.

One reader task demultiplexes the socket: events go to a queue, responses
resolve the future registered by :meth:`call`. That split is the whole reason
``permission.respond`` works: the caller is inside ``start_run``'s event stream
while the answer to a prompt has to travel the other way.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from openagent.runtime.events import RuntimeEvent
from openagent.service import protocol as proto
from openagent.service.protocol import (
    PROTOCOL_VERSION,
    TERMINAL_EVENT_TYPES,
    ErrorCode,
    FrameReader,
    Method,
    ProtocolError,
    RpcError,
    as_request,
    as_response,
    encode,
    event_from_dict,
    event_from_message,
    request,
)

READ_CHUNK = 64 * 1024

logger = logging.getLogger("openagent.client")

_CLOSE = object()

#: Called with the ``permission_requested`` event; returns (granted, scope).
PermissionHandler = Callable[[RuntimeEvent], Awaitable[tuple[bool, str]]]


class ServiceClosed(ConnectionError):
    """The server hung up. Pending calls cannot complete."""


class ServiceClient:
    """An async client for one service connection.

    One connection runs one run at a time; open more connections for parallel
    runs.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._pending: dict[str, asyncio.Future] = {}
        self._events: asyncio.Queue[Any] = asyncio.Queue()
        self._counter = 0
        self._failure: Exception | None = None
        self._reader_task: asyncio.Task | None = None
        self._permission_handler: PermissionHandler | None = None
        self.info: dict[str, Any] = {}

    # ------------------------------------------------------------- connection

    @classmethod
    async def connect(
        cls,
        path: str | Path | None = None,
        *,
        client: str = "openagent-client",
        client_version: str = "0.1.0",
        protocol_version: int = PROTOCOL_VERSION,
        permission_handler: PermissionHandler | None = None,
    ) -> "ServiceClient":
        from openagent.service.server import ServiceSettings

        target = Path(path) if path else ServiceSettings().socket_path
        reader, writer = await asyncio.open_unix_connection(path=str(target))
        instance = cls(reader, writer)
        instance._permission_handler = permission_handler
        instance._reader_task = asyncio.create_task(instance._read_loop())
        instance._reader_task.add_done_callback(instance._reader_finished)
        instance.info = await instance.call(
            Method.HELLO,
            {
                "protocol_version": protocol_version,
                "client": client,
                "client_version": client_version,
            },
        )
        return instance

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    async def __aenter__(self) -> "ServiceClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ----------------------------------------------------------------- calls

    async def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> dict[str, Any]:
        if self._failure is not None:
            raise self._failure
        self._counter += 1
        request_id = f"c{self._counter}"
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._writer.write(encode(request(method, params, id=request_id).to_dict()))
        try:
            await self._writer.drain()
        except (ConnectionError, OSError) as exc:
            self._pending.pop(request_id, None)
            raise ServiceClosed(str(exc)) from exc
        try:
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)

    async def ping(self) -> dict[str, Any]:
        return await self.call(Method.PING)

    async def start_run(
        self,
        prompt: str,
        working_dir: str | Path,
        *,
        model: str | None = None,
        session_id: str | None = None,
        max_turns: int | None = None,
        permission_mode: str = "ask",
        images: list[str] | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        """Start a run and return ``{run_id, session_id, model, working_dir}``.

        Events arrive on :meth:`events`; this returns as soon as the server has
        accepted the run, so the caller can render a spinner before the first
        token.
        """
        params: dict[str, Any] = {
            "prompt": prompt,
            "working_dir": str(working_dir),
            "permission_mode": permission_mode,
        }
        if model:
            params["model"] = model
        if session_id:
            params["session_id"] = session_id
        if max_turns is not None:
            params["max_turns"] = max_turns
        if images:
            params["images"] = images
        if reasoning_effort:
            params["reasoning_effort"] = reasoning_effort
        return await self.call(Method.RUN_START, params)

    async def cancel(self, run_id: str) -> dict[str, Any]:
        return await self.call(Method.RUN_CANCEL, {"run_id": run_id})

    async def respond_permission(
        self, request_id: str, granted: bool, scope: str = "once"
    ) -> dict[str, Any]:
        return await self.call(
            Method.PERMISSION_RESPOND,
            {"request_id": request_id, "granted": granted, "scope": scope},
        )

    async def models(self, *, discover: bool = False) -> dict[str, Any]:
        return await self.call(Method.MODEL_LIST, {"discover": discover})

    async def probe(self, model: str) -> dict[str, Any]:
        return await self.call(Method.MODEL_PROBE, {"model": model})

    async def select_model(self, model: str) -> dict[str, Any]:
        return await self.call(Method.MODEL_SELECT, {"model": model})

    async def auth_status(self) -> dict[str, Any]:
        return await self.call(Method.AUTH_STATUS)

    async def tools(self) -> list[dict[str, Any]]:
        return (await self.call(Method.TOOL_LIST))["tools"]

    async def sessions(self) -> list[dict[str, Any]]:
        return (await self.call(Method.SESSION_LIST))["sessions"]

    async def load_session(self, session_id: str) -> list[dict[str, Any]]:
        """Messages of a stored session, in the form a provider consumes."""
        result = await self.call(Method.SESSION_LOAD, {"session_id": session_id})
        return result["messages"]

    async def delete_session(self, session_id: str) -> bool:
        """Remove a stored session, and report whether there was a file to remove.

        A session with a run in flight is refused (`session_busy`), not queued:
        the run's post-run save would write the file straight back.
        """
        result = await self.call(Method.SESSION_DELETE, {"session_id": session_id})
        return bool(result["deleted"])

    async def config(self, keys: list[str] | None = None) -> dict[str, Any]:
        return (await self.call(Method.CONFIG_GET, {"keys": keys or []}))["config"]

    # ---------------------------------------------------------------- events

    async def events(self) -> AsyncIterator[RuntimeEvent]:
        """Yield events until the run's terminal event, then finish.

        Ends on ``run_completed`` / ``run_failed`` / ``run_cancelled`` so a
        caller's ``async for`` is a complete run, with no sentinel to remember.
        """
        while True:
            item = await self._events.get()
            if item is _CLOSE:
                if self._failure is not None:
                    raise self._failure
                return
            yield item  # type: ignore[misc]
            if getattr(item, "type", None) in TERMINAL_EVENT_TYPES:
                return

    # ------------------------------------------------------------ demultiplexing

    def _reader_finished(self, task: asyncio.Task) -> None:
        """A reader that dies on an unexpected exception must not hang callers."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._fail(ServiceClosed(f"reader stopped: {type(exc).__name__}: {exc}"))

    def _dispatch(self, frame: Any) -> None:
        req = as_request(frame)
        if req is not None:
            if req.method == proto.EVENT_METHOD:
                try:
                    event = event_from_dict(req.params)
                except ProtocolError as exc:
                    self._fail(exc)
                    return
                self._events.put_nowait(event)
                if event.type == "permission_requested" and self._permission_handler is not None:
                    asyncio.create_task(self._answer_permission(event))
            return

        try:
            response = as_response(frame)
        except ProtocolError as exc:
            self._fail(exc)
            return
        if response is None:
            return
        future = self._pending.pop(response.id, None)
        if future is None or future.done():
            return
        if response.error is not None:
            future.set_exception(response.error)
        else:
            future.set_result(response.result or {})

    def _fail(self, exc: Exception) -> None:
        self._failure = exc
        self._events.put_nowait(_CLOSE)
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    async def _answer_permission(self, event: RuntimeEvent) -> None:
        handler = self._permission_handler
        if handler is None:
            return
        try:
            granted, scope = await handler(event)
        except Exception:
            # Fail closed, and say so: a UI whose prompt is broken must not
            # silently approve the tool the prompt was guarding.
            logger.exception("permission handler failed; refusing the tool call")
            granted, scope = False, "once"
        try:
            await self.respond_permission(event.request_id, granted, scope)  # type: ignore[attr-defined]
        except (RpcError, ServiceClosed):
            pass

    async def _read_loop(self) -> None:
        frames = FrameReader()
        try:
            while True:
                chunk = await self._reader.read(READ_CHUNK)
                if not chunk:
                    break
                for frame in frames.feed(chunk):
                    self._dispatch(frame)
            self._fail(ServiceClosed("the service closed the connection"))
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError, ProtocolError) as exc:
            self._fail(ServiceClosed(str(exc)))


__all__ = ["PermissionHandler", "ServiceClient", "ServiceClosed"]
