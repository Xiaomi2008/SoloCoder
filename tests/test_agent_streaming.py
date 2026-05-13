"""Tests for streaming support in Agent._loop()."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from openagent import Agent
from openagent.core.types import Message, ToolDef
from tests.conftest import MockProvider


class StreamingMockProvider(MockProvider):
    """Mock provider that supports streaming with configurable latency."""

    def __init__(
        self,
        responses: list[Message] | None = None,
        chunks: list[str] | None = None,
        chat_delay: float = 0.1,
        stream_delay: float = 0.01,
    ):
        super().__init__(responses)
        self.chunks = chunks or ["Hello", " world"]
        self._stream_called = False
        self._chat_delay = chat_delay
        self._stream_delay = stream_delay

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
        **kwargs,
    ) -> Message:
        await asyncio.sleep(self._chat_delay)
        return await super().chat(messages, tools, system_prompt, **kwargs)

    async def stream(
        self,
        messages: list[Message],
        tools=None,
        system_prompt: str = "",
        **kwargs,
    ) -> AsyncIterator[str]:
        self._stream_called = True
        for chunk in self.chunks:
            await asyncio.sleep(self._stream_delay)
            yield chunk


class TestAgentStreaming:
    async def test_on_chunk_receives_streamed_text(self):
        """When on_chunk is provided, streamed chunks are delivered to callback."""
        chunks_received = []
        provider = StreamingMockProvider(
            responses=[Message(role="assistant", content="Hello world")],
            chunks=["Hello", " ", "world"],
        )

        agent = Agent(provider=provider)
        result = await agent.run("hi", on_chunk=lambda c: chunks_received.append(c))

        assert result == "Hello world"
        assert chunks_received == ["Hello", " ", "world"]
        assert provider._stream_called

    async def test_no_streaming_without_on_chunk(self):
        """When on_chunk is not provided, stream() is not called."""
        provider = StreamingMockProvider(
            responses=[Message(role="assistant", content="OK")],
        )

        agent = Agent(provider=provider)
        result = await agent.run("hi")

        assert result == "OK"
        assert not provider._stream_called

    async def test_streaming_with_tool_calls(self):
        """Streaming works when the response includes tool calls."""
        from openagent import tool
        from openagent.core.types import TextBlock, ToolUseBlock

        chunks_received = []

        @tool
        def dummy(x: str) -> str:
            return f"echo: {x}"

        tool_response = Message(
            role="assistant",
            content=[
                TextBlock(text="calling tool"),
                ToolUseBlock(id="call_1", name="dummy", arguments={"x": "test"}),
            ],
        )
        final_response = Message(role="assistant", content="Done!")

        provider = StreamingMockProvider(
            responses=[tool_response, final_response],
            chunks=["calling", " tool"],
        )

        agent = Agent(provider=provider, tools=[dummy])
        result = await agent.run("run tool", on_chunk=lambda c: chunks_received.append(c))

        assert result == "Done!"
        assert len(chunks_received) >= 1

    async def test_streaming_empty_chunks_ignored(self):
        """Empty chunks from the stream don't trigger on_chunk."""
        chunks_received = []
        provider = StreamingMockProvider(
            responses=[Message(role="assistant", content="OK")],
            chunks=["", "Hello", "", "world", ""],
        )

        agent = Agent(provider=provider)
        await agent.run("hi", on_chunk=lambda c: chunks_received.append(c))

        assert chunks_received == ["Hello", "world"]

    async def test_streaming_delivered_for_final_turn(self):
        """Streaming is delivered when the final turn has no tool calls."""
        chunks_received = []
        provider = StreamingMockProvider(
            responses=[Message(role="assistant", content="Final answer!")],
            chunks=["Final", " ", "answer!"],
        )

        agent = Agent(provider=provider)
        result = await agent.run("question?", on_chunk=lambda c: chunks_received.append(c))

        assert result == "Final answer!"
        assert "Final" in chunks_received
        assert "answer!" in chunks_received
