"""Tests for Ollama provider."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openagent.core.types import (
    Message,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
)
from openagent.provider import (
    OllamaProvider as CompatOllamaProvider,
    ProviderError,
    ProviderMessageCompleted,
    ProviderMessageStarted,
    ProviderTextDelta,
    ProviderToolCall,
)
import openagent.provider.ollama as ollama_provider_module
from openagent.provider.ollama import OllamaConverterMixin, OllamaProvider


# ---------------------------------------------------------------------------
# Converter mixin tests (pure logic, no network)
# ---------------------------------------------------------------------------


class TestOllamaConverterMixin:
    def setup_method(self):
        self.converter = OllamaConverterMixin()

    def test_convert_simple_messages(self):
        messages = [
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi there!"),
        ]
        result = self.converter.convert_messages(messages)
        assert result["messages"] == [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

    def test_convert_messages_with_system_prompt(self):
        messages = [Message(role="user", content="Hello")]
        result = self.converter.convert_messages(messages, system_prompt="Be helpful")
        assert result["messages"][0] == {"role": "system", "content": "Be helpful"}
        assert result["messages"][1] == {"role": "user", "content": "Hello"}

    def test_convert_system_message(self):
        messages = [Message(role="system", content="You are helpful")]
        result = self.converter.convert_messages(messages)
        assert result["messages"] == [{"role": "system", "content": "You are helpful"}]

    def test_convert_assistant_with_tool_calls(self):
        messages = [
            Message(
                role="assistant",
                content=[
                    TextBlock(text="Let me check"),
                    ToolUseBlock(
                        id="call_1", name="get_weather", arguments={"city": "Tokyo"}
                    ),
                ],
            ),
        ]
        result = self.converter.convert_messages(messages)
        msg = result["messages"][0]
        assert msg["role"] == "assistant"
        assert msg["content"] == "Let me check"
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0]["function"]["name"] == "get_weather"
        # Ollama uses dict arguments, not JSON strings
        assert msg["tool_calls"][0]["function"]["arguments"] == {"city": "Tokyo"}

    def test_convert_assistant_tool_calls_only(self):
        """Assistant message with only tool calls and no text."""
        messages = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="call_1", name="search", arguments={"q": "test"}),
                ],
            ),
        ]
        result = self.converter.convert_messages(messages)
        msg = result["messages"][0]
        assert msg["content"] == ""
        assert len(msg["tool_calls"]) == 1

    def test_convert_tool_results(self):
        messages = [
            Message(
                role="tool_result",
                content=[
                    ToolResultBlock(tool_use_id="call_1", content="22C and sunny"),
                ],
            ),
        ]
        result = self.converter.convert_messages(messages)
        # Ollama tool results have no tool_call_id
        assert result["messages"] == [{"role": "tool", "content": "22C and sunny"}]

    def test_convert_multiple_tool_results(self):
        messages = [
            Message(
                role="tool_result",
                content=[
                    ToolResultBlock(tool_use_id="call_1", content="Result 1"),
                    ToolResultBlock(tool_use_id="call_2", content="Result 2"),
                ],
            ),
        ]
        result = self.converter.convert_messages(messages)
        assert len(result["messages"]) == 2
        assert result["messages"][0] == {"role": "tool", "content": "Result 1"}
        assert result["messages"][1] == {"role": "tool", "content": "Result 2"}

    # -- convert_response --

    def test_convert_response_text_only(self):
        mock_response = MagicMock()
        mock_response.message.content = "Hello!"
        mock_response.message.tool_calls = None
        result = self.converter.convert_response(mock_response)
        assert result.role == "assistant"
        assert result.content == "Hello!"

    def test_convert_response_with_tool_calls(self):
        mock_tc = MagicMock()
        mock_tc.id = "tool_123"
        mock_tc.function.name = "get_weather"
        mock_tc.function.arguments = {"city": "Tokyo"}

        mock_response = MagicMock()
        mock_response.message.content = ""
        mock_response.message.tool_calls = [mock_tc]

        result = self.converter.convert_response(mock_response)
        assert result.role == "assistant"
        assert isinstance(result.content, list)
        tool_blocks = [b for b in result.content if isinstance(b, ToolUseBlock)]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].id == "tool_123"
        assert tool_blocks[0].name == "get_weather"
        assert tool_blocks[0].arguments == {"city": "Tokyo"}

    def test_convert_response_with_text_and_tool_calls(self):
        mock_tc = MagicMock()
        mock_tc.function.name = "search"
        mock_tc.function.arguments = {"q": "test"}

        mock_response = MagicMock()
        mock_response.message.content = "Let me search"
        mock_response.message.tool_calls = [mock_tc]

        result = self.converter.convert_response(mock_response)
        assert result.role == "assistant"
        assert isinstance(result.content, list)
        assert len(result.content) == 2
        assert isinstance(result.content[0], TextBlock)
        assert isinstance(result.content[1], ToolUseBlock)

    def test_convert_response_arguments_as_string(self):
        """Defensive: handle arguments as JSON string."""
        mock_tc = MagicMock()
        mock_tc.id = "tool_calc"
        mock_tc.function.name = "calc"
        mock_tc.function.arguments = '{"expr": "1+1"}'

        mock_response = MagicMock()
        mock_response.message.content = ""
        mock_response.message.tool_calls = [mock_tc]

        result = self.converter.convert_response(mock_response)
        tool_blocks = [b for b in result.content if isinstance(b, ToolUseBlock)]
        assert tool_blocks[0].id == "tool_calc"
        assert tool_blocks[0].arguments == {"expr": "1+1"}

    def test_convert_response_invalid_json_arguments_fall_back_safely(self):
        mock_tc = MagicMock()
        mock_tc.id = "tool_broken"
        mock_tc.function.name = "calc"
        mock_tc.function.arguments = '{"expr": "1+1"'

        mock_response = MagicMock()
        mock_response.message.content = ""
        mock_response.message.tool_calls = [mock_tc]

        result = self.converter.convert_response(mock_response)

        tool_blocks = [b for b in result.content if isinstance(b, ToolUseBlock)]
        assert tool_blocks[0].id == "tool_broken"
        assert tool_blocks[0].arguments == {}

    # -- convert_tools --

    def test_convert_tools(self):
        tools = [
            ToolDef(
                name="search",
                description="Search the web",
                parameters={"type": "object", "properties": {"q": {"type": "string"}}},
            ),
        ]
        result = self.converter.convert_tools(tools)
        assert len(result) == 1
        assert result[0] == {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search the web",
                "parameters": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                },
            },
        }

    def test_convert_tools_empty(self):
        result = self.converter.convert_tools([])
        assert result == []


# ---------------------------------------------------------------------------
# Provider tests (mocked SDK)
# ---------------------------------------------------------------------------


class TestOllamaProvider:
    @staticmethod
    def _make_provider(mock_client: AsyncMock) -> OllamaProvider:
        """Create an OllamaProvider with a pre-set mock client."""
        provider = OllamaProvider.__new__(OllamaProvider)
        provider.model = "llama3"
        provider.api_key = None
        provider._max_retries = 0
        provider._client = mock_client
        return provider

    @staticmethod
    def _text_response(text: str) -> MagicMock:
        resp = MagicMock()
        resp.message.content = text
        resp.message.tool_calls = None
        return resp

    @staticmethod
    async def _collect(stream):
        return [event async for event in stream]

    @staticmethod
    def _stream_chunk(content: str = "", tool_calls=None) -> MagicMock:
        chunk = MagicMock()
        chunk.id = None
        chunk.message.content = content
        chunk.message.tool_calls = tool_calls
        return chunk

    async def test_chat_simple(self):
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=self._text_response("Hello!"))
        provider = self._make_provider(mock_client)

        result = await provider.chat([Message(role="user", content="Hi")])
        assert result == Message(role="assistant", content="Hello!")
        mock_client.chat.assert_called_once()

    async def test_stream_yields_provider_events_for_text_response(self):
        mock_client = AsyncMock()

        async def fake_stream():
            yield self._stream_chunk(content="Hello")
            yield self._stream_chunk(content=" world")

        mock_client.chat = AsyncMock(return_value=fake_stream())
        provider = self._make_provider(mock_client)

        events = await self._collect(
            provider.stream([Message(role="user", content="Hi")])
        )

        assert CompatOllamaProvider is OllamaProvider
        assert [type(event) for event in events] == [
            ProviderMessageStarted,
            ProviderTextDelta,
            ProviderTextDelta,
            ProviderMessageCompleted,
        ]
        assert events[0].message_id.startswith("msg_")
        assert events[1].message_id == events[0].message_id
        assert events[1].delta == "Hello"
        assert events[2].delta == " world"
        assert events[3].message_id == events[0].message_id

    async def test_stream_emits_provider_tool_call_events_when_ollama_streams_tool_calls(
        self,
    ):
        mock_tool_call = MagicMock()
        mock_tool_call.id = "tool_stream_123"
        mock_tool_call.function.name = "lookup_weather"
        mock_tool_call.function.arguments = {"city": "Paris", "units": "metric"}

        mock_client = AsyncMock()

        async def fake_stream():
            yield self._stream_chunk(tool_calls=[mock_tool_call])

        mock_client.chat = AsyncMock(return_value=fake_stream())
        provider = self._make_provider(mock_client)

        events = await self._collect(
            provider.stream([Message(role="user", content="Hi")])
        )

        assert [type(event) for event in events] == [
            ProviderMessageStarted,
            ProviderToolCall,
            ProviderMessageCompleted,
        ]
        assert events[1].message_id == events[0].message_id
        assert events[1].id == "tool_stream_123"
        assert events[1].name == "lookup_weather"
        assert events[1].arguments == {"city": "Paris", "units": "metric"}

    async def test_stream_uses_safe_argument_fallback_for_malformed_tool_call_json(
        self,
    ):
        mock_tool_call = MagicMock()
        mock_tool_call.id = "tool_stream_broken"
        mock_tool_call.function.name = "lookup_weather"
        mock_tool_call.function.arguments = '{"city": "Paris"'

        mock_client = AsyncMock()

        async def fake_stream():
            yield self._stream_chunk(tool_calls=[mock_tool_call])

        mock_client.chat = AsyncMock(return_value=fake_stream())
        provider = self._make_provider(mock_client)

        events = await self._collect(
            provider.stream([Message(role="user", content="Hi")])
        )

        assert [type(event) for event in events] == [
            ProviderMessageStarted,
            ProviderToolCall,
            ProviderMessageCompleted,
        ]
        assert events[1].id == "tool_stream_broken"
        assert events[1].arguments == {}

    async def test_stream_emits_terminal_provider_error_instead_of_raising(self):
        mock_client = AsyncMock()

        async def fake_stream():
            raise RuntimeError("stream broke")
            yield

        mock_client.chat = AsyncMock(return_value=fake_stream())
        provider = self._make_provider(mock_client)

        events = await self._collect(
            provider.stream([Message(role="user", content="Hi")])
        )

        assert [type(event) for event in events] == [
            ProviderMessageStarted,
            ProviderError,
        ]
        assert events[1].message_id == events[0].message_id
        assert events[1].error == "stream broke"

    async def test_chat_with_tools(self):
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=self._text_response("Sure"))
        provider = self._make_provider(mock_client)

        tools = [
            ToolDef(
                name="test",
                description="A test",
                parameters={"type": "object", "properties": {}},
            )
        ]
        await provider.chat([Message(role="user", content="Hi")], tools=tools)

        call_kwargs = mock_client.chat.call_args
        assert "tools" in call_kwargs.kwargs

    async def test_chat_no_tools_omits_tools_key(self):
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=self._text_response("Hi"))
        provider = self._make_provider(mock_client)

        await provider.chat([Message(role="user", content="Hi")])
        call_kwargs = mock_client.chat.call_args
        assert "tools" not in call_kwargs.kwargs

    async def test_custom_host(self):
        with patch.object(ollama_provider_module, "_OllamaAsyncClient") as MockClient:
            MockClient.return_value = AsyncMock()
            OllamaProvider(model="llama3", host="http://remote:11434")
            MockClient.assert_called_once_with(host="http://remote:11434")

    async def test_default_host(self):
        with patch.object(ollama_provider_module, "_OllamaAsyncClient") as MockClient:
            MockClient.return_value = AsyncMock()
            OllamaProvider(model="llama3")
            # No host kwarg passed when host is None
            MockClient.assert_called_once_with()

    async def test_import_error(self):
        with patch.object(ollama_provider_module, "_OllamaAsyncClient", None):
            with pytest.raises(ImportError, match="Install ollama"):
                provider = OllamaProvider.__new__(OllamaProvider)
                OllamaProvider.__init__(provider, model="llama3")
