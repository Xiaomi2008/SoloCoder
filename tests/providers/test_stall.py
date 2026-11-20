"""A reply that stops mid-stream fails in seconds, and the run asks again.

The server here is a real socket that answers like an OpenAI-compatible one
and then goes quiet with the connection open: what a TaoPC server did, holding
a run on "Thinking…" for the client's ten-minute read timeout.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openagent.core.types import Message
from openagent.provider import stall
from openagent.provider.openai import OpenAIProvider
from openagent.providers import ProviderError, ProviderReasoningDelta
from openagent.runtime.runner import AgentRunner
from tests.support.streaming_provider import ScriptedStreamProvider, text_turn


def _chunk(reasoning: str) -> bytes:
    body = {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "delta": {"reasoning_content": reasoning}, "finish_reason": None}],
    }
    return f"data: {json.dumps(body)}\n\n".encode()


async def _server(before_first: float, then: str) -> tuple[asyncio.Server, int]:
    """Waits ``before_first`` seconds, sends one chunk, then ``then``:
    "stall" holds the connection silent, "finish" ends the reply."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        writer.write(
            b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n"
            b"transfer-encoding: chunked\r\n\r\n"
        )
        await writer.drain()
        await asyncio.sleep(before_first)

        def send(data: bytes) -> None:
            writer.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")

        send(_chunk("thinking"))
        await writer.drain()
        if then == "stall":
            await asyncio.sleep(30)
        send(b"data: [DONE]\n\n")
        writer.write(b"0\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _drain(provider: OpenAIProvider) -> list[object]:
    events = []
    async for event in provider.stream([Message(role="user", content="hi")]):
        events.append(event)
    return events


async def test_a_reply_that_goes_quiet_fails_as_a_stall(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(stall, "STALL_AFTER", 0.3)
    server, port = await _server(before_first=0, then="stall")
    provider = OpenAIProvider(model="m", base_url=f"http://127.0.0.1:{port}/v1", max_retries=0)
    try:
        async with asyncio.timeout(5):
            events = await _drain(provider)
        errors = [e for e in events if isinstance(e, ProviderError)]
        assert len(errors) == 1 and errors[0].stalled, events
    finally:
        server.close()


async def test_the_wait_for_the_first_byte_is_not_a_stall(monkeypatch: pytest.MonkeyPatch):
    """A long prompt is read before anything is sent; that wait keeps its own limit."""
    monkeypatch.setattr(stall, "STALL_AFTER", 0.2)
    server, port = await _server(before_first=0.5, then="finish")
    provider = OpenAIProvider(model="m", base_url=f"http://127.0.0.1:{port}/v1", max_retries=0)
    try:
        events = await _drain(provider)
        assert any(isinstance(e, ProviderReasoningDelta) for e in events)
    finally:
        server.close()


class _StallsOnce(ScriptedStreamProvider):
    def __init__(self, turns):
        super().__init__(turns)
        self.stalls = 1

    async def stream(self, messages, tools=None, system_prompt="", **kwargs):
        if self.stalls:
            self.stalls -= 1
            self.calls.append({"messages": list(messages)})
            yield ProviderReasoningDelta(message_id="m0", delta="half a thought")
            yield ProviderError(message_id="m0", error="stopped sending for 90s", stalled=True)
            return
        async for event in super().stream(messages, tools, system_prompt, **kwargs):
            yield event


async def test_the_run_asks_again_after_a_stall(tmp_path: Path):
    provider = _StallsOnce([text_turn("answer")])
    runner = AgentRunner(provider)
    events = [event async for event in runner.stream("go")]

    failed = [e for e in events if e.type == "message_failed"]
    assert len(failed) == 1 and "asking again" in failed[0].error
    assert events[-1].type == "run_completed"
    assert events[-1].result.output_text == "answer"
    assert len(provider.calls) == 2


def test_the_guard_is_made_of_the_sdk_clients_own_http_package():
    """openai 3 builds its client on httpx2, which refuses httpx transports."""
    import importlib

    from openagent.provider.openai import _stall_guarded_client

    client = _stall_guarded_client()
    package = importlib.import_module(type(client).__mro__[1].__module__.split(".")[0])
    assert isinstance(client._transport, package.AsyncBaseTransport)
