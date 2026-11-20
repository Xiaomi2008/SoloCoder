"""Notice a streamed reply that stopped coming.

A model server can stop sending in the middle of a reply and leave the
connection open. The HTTP client's read timeout is ten minutes, because a long
prompt can take that long to read before the first token; so a reply that
stalled after it began held the run on "Thinking…" for those ten minutes.

Once a reply has begun, bytes keep coming: tokens, or the keep-alive comments
servers send while they work (strata sends one at least every 10 s, through
prefill too). Silence for ``STALL_AFTER`` seconds after that is a stall. It is
counted in bytes, below the SDK, because the SDK drops the keep-alives. The wait
before the first byte keeps the client's own timeout: llama.cpp sends nothing
while it reads a prompt.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from functools import cache
from types import ModuleType
from typing import Any

STALL_AFTER = 90.0


class StreamStalled(Exception):
    """No data for ``STALL_AFTER`` seconds in a reply that had begun."""


def is_stall(error: BaseException | None) -> bool:
    """Is this, or what caused it, a stalled reply? The SDK may wrap it."""
    seen = 0
    while error is not None and seen < 8:
        if isinstance(error, StreamStalled):
            return True
        error = error.__cause__ or error.__context__
        seen += 1
    return False


@cache
def _classes(http: ModuleType) -> tuple[type, type]:
    """The guard, built on ``http``: the HTTP package the SDK's client uses.

    Not ``httpx`` by import: openai 3 moved to ``httpx2``, whose client asserts
    its own stream and transport types, and a guard made of ``httpx`` parts
    failed every request there with a bare ``AssertionError``.
    """

    class GuardedStream(http.AsyncByteStream):
        def __init__(self, inner: Any, stall_after: float) -> None:
            self._inner = inner
            self._stall_after = stall_after

        async def __aiter__(self) -> AsyncIterator[bytes]:
            chunks = self._inner.__aiter__()
            begun = False
            while True:
                try:
                    if begun:
                        chunk = await asyncio.wait_for(anext(chunks), self._stall_after)
                    else:
                        chunk = await anext(chunks)
                except StopAsyncIteration:
                    return
                except TimeoutError:
                    raise StreamStalled(
                        f"the model server stopped sending for {self._stall_after:.0f}s mid-reply"
                    ) from None
                begun = True
                yield chunk

        async def aclose(self) -> None:
            await self._inner.aclose()

    class StallGuardTransport(http.AsyncBaseTransport):
        """Wraps a transport so event-stream replies fail on a stall."""

        def __init__(self, inner: Any, stall_after: float | None = None) -> None:
            self._inner = inner
            self.stall_after = stall_after

        async def handle_async_request(self, request: Any) -> Any:
            response = await self._inner.handle_async_request(request)
            if "text/event-stream" in response.headers.get("content-type", ""):
                limit = self.stall_after if self.stall_after is not None else STALL_AFTER
                response.stream = GuardedStream(response.stream, limit)
            return response

        async def aclose(self) -> None:
            await self._inner.aclose()

    return GuardedStream, StallGuardTransport


def stall_guard_transport(http: ModuleType, inner: Any, stall_after: float | None = None) -> Any:
    """``inner`` (a transport of ``http``), failing a reply that stalls."""
    return _classes(http)[1](inner, stall_after)


__all__ = ["STALL_AFTER", "StreamStalled", "is_stall", "stall_guard_transport"]
