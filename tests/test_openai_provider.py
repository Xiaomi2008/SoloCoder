"""Tests for OpenAI provider behavior and diagnostics."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from openagent.core.types import Message
from openagent.provider.openai import OpenAIProvider
from openagent.providers import (
    ProviderError,
    ProviderMessageCompleted,
    ProviderMessageStarted,
    ProviderTextDelta,
    ProviderToolCall,
)


class TestOpenAIProviderDiagnostics:
    @staticmethod
    def _make_provider() -> OpenAIProvider:
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.model = "qwen-local"
        provider.api_key = None
        provider.base_url = "http://localhost:1234/v1"
        provider._max_retries = 0
        provider._client = MagicMock()
        return provider

    @staticmethod
    def _response(
        content: str | None, tool_calls=None, finish_reason: str | None = None
    ):
        response = MagicMock()
        choice = MagicMock()
        choice.finish_reason = finish_reason
        choice.message.content = content
        choice.message.tool_calls = tool_calls
        response.choices = [choice]
        response.model_dump.return_value = {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {"content": content, "tool_calls": tool_calls},
                }
            ]
        }
        return response

    def test_logs_empty_assistant_message(self, caplog):
        provider = self._make_provider()
        response = self._response(content="", tool_calls=None, finish_reason="stop")

        with caplog.at_level(logging.DEBUG, logger="openagent.provider.openai"):
            message = provider.convert_response(response)

        assert message.role == "assistant"
        assert message.content == []
        assert "empty_assistant_message" in caplog.text
        assert "Raw response payload" in caplog.text

    def test_logs_invalid_tool_arguments(self, caplog):
        provider = self._make_provider()
        tool_call = MagicMock()
        tool_call.id = "call_1"
        tool_call.function.name = "glob"
        tool_call.function.arguments = "not-json"
        response = self._response(
            content="", tool_calls=[tool_call], finish_reason="tool_calls"
        )

        with caplog.at_level(logging.DEBUG, logger="openagent.provider.openai"):
            with pytest.raises(Exception):
                provider.convert_response(response)

        assert "invalid_tool_arguments" in caplog.text
        assert "not-json" in caplog.text

    def test_ignores_reasoning_content_when_tool_call_fields_are_absent(self, caplog):
        provider = self._make_provider()
        response = self._response(content="", tool_calls=[], finish_reason="stop")
        response.choices[0].message.reasoning_content = """Now let me inspect the file.

<tool_call>
<function=read>
<parameter=path>
/Users/taozeng/Projects/SoloCoder/cli_coder.py
</parameter>
<parameter=line_start>
180
</parameter>
<parameter=line_end>
230
</parameter>
</function>
</tool_call>"""

        with caplog.at_level(logging.DEBUG, logger="openagent.provider.openai"):
            message = provider.convert_response(response)

        assert message.role == "assistant"
        assert message.content == []
        assert "recovered_tool_call_from_reasoning" not in caplog.text
        assert "empty_assistant_message" in caplog.text


class TestOpenAIProviderStreaming:
    @staticmethod
    def _make_provider() -> OpenAIProvider:
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.model = "gpt-4o"
        provider.api_key = "test-key"
        provider.base_url = None
        provider._max_retries = 0
        provider._client = MagicMock()
        return provider

    @staticmethod
    def _response(content: str):
        response = MagicMock()
        choice = MagicMock()
        choice.message.content = content
        choice.message.tool_calls = []
        response.choices = [choice]
        return response

    @staticmethod
    def _chunk(
        message_id: str,
        content: str | None = None,
        finish_reason: str | None = None,
        tool_calls=None,
    ):
        chunk = MagicMock()
        chunk.id = message_id
        choice = MagicMock()
        choice.delta.content = content
        choice.delta.tool_calls = tool_calls
        choice.finish_reason = finish_reason
        chunk.choices = [choice]
        return chunk

    @staticmethod
    async def _collect(stream):
        return [event async for event in stream]

    @pytest.mark.asyncio
    async def test_chat_returns_canonical_assistant_message(self):
        provider = self._make_provider()
        provider._client.chat.completions.create = AsyncMock(
            return_value=self._response("Hello from OpenAI")
        )

        message = await provider.chat(messages=[Message(role="user", content="Hi")])

        assert message == Message(role="assistant", content="Hello from OpenAI")

    @pytest.mark.asyncio
    async def test_stream_yields_provider_events_for_text_chunks(self):
        provider = self._make_provider()

        async def fake_stream():
            yield self._chunk("chatcmpl_123", content="Hello")
            yield self._chunk("chatcmpl_123", content=" world")

        provider._client.chat.completions.create = AsyncMock(return_value=fake_stream())

        events = await self._collect(
            provider.stream(messages=[Message(role="user", content="Hi")])
        )

        assert [type(event) for event in events] == [
            ProviderMessageStarted,
            ProviderTextDelta,
            ProviderTextDelta,
            ProviderMessageCompleted,
        ]
        assert events[0].message_id == "chatcmpl_123"
        assert events[1].message_id == "chatcmpl_123"
        assert events[1].delta == "Hello"
        assert events[2].delta == " world"
        assert events[3].message_id == "chatcmpl_123"

    @pytest.mark.asyncio
    async def test_stream_emits_single_terminal_completed_event_for_simple_text_response(
        self,
    ):
        provider = self._make_provider()

        async def fake_stream():
            yield self._chunk("chatcmpl_terminal", content="Hello")
            yield self._chunk("chatcmpl_terminal", finish_reason="stop")

        provider._client.chat.completions.create = AsyncMock(return_value=fake_stream())

        events = await self._collect(
            provider.stream(messages=[Message(role="user", content="Hi")])
        )

        assert [event.type for event in events] == [
            "provider_message_started",
            "provider_text_delta",
            "provider_message_completed",
        ]
        assert isinstance(events[-1], ProviderMessageCompleted)
        assert sum(isinstance(event, ProviderMessageCompleted) for event in events) == 1

    @pytest.mark.asyncio
    async def test_stream_emits_provider_tool_call_event_from_openai_delta_chunks(self):
        provider = self._make_provider()

        def tool_call_delta(
            index: int, id: str = "", name: str = "", arguments: str = ""
        ):
            tool_call = MagicMock()
            tool_call.index = index
            tool_call.id = id
            tool_call.function.name = name
            tool_call.function.arguments = arguments
            return tool_call

        async def fake_stream():
            yield self._chunk(
                "chatcmpl_tool",
                tool_calls=[
                    tool_call_delta(
                        0,
                        id="call_123",
                        name="lookup_weather",
                        arguments='{"city"',
                    )
                ],
            )
            yield self._chunk(
                "chatcmpl_tool",
                tool_calls=[
                    tool_call_delta(0, arguments=': "Paris", "units": "metric"}')
                ],
                finish_reason="tool_calls",
            )

        provider._client.chat.completions.create = AsyncMock(return_value=fake_stream())

        events = await self._collect(
            provider.stream(messages=[Message(role="user", content="Hi")])
        )

        assert [type(event) for event in events] == [
            ProviderMessageStarted,
            ProviderToolCall,
            ProviderMessageCompleted,
        ]
        assert events[1].message_id == "chatcmpl_tool"
        assert events[1].id == "call_123"
        assert events[1].name == "lookup_weather"
        assert events[1].arguments == {"city": "Paris", "units": "metric"}

    @pytest.mark.asyncio
    async def test_stream_emits_terminal_provider_error_instead_of_raising(self):
        provider = self._make_provider()

        async def fake_stream():
            raise RuntimeError("stream broke")
            yield

        provider._client.chat.completions.create = AsyncMock(return_value=fake_stream())

        events = await self._collect(
            provider.stream(messages=[Message(role="user", content="Hi")])
        )

        assert [type(event) for event in events] == [
            ProviderMessageStarted,
            ProviderError,
        ]
        assert events[1].message_id == events[0].message_id
        assert events[1].error == "stream broke"
