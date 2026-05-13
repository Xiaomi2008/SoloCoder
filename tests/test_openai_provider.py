"""Tests for OpenAIProvider - converter, chat, stream, retry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openagent.core.types import (
    Message, TextBlock, ToolDef, ToolResultBlock, ToolUseBlock,
)


# ============================================================================
# OpenAIConverterMixin
# ============================================================================


class TestOpenAIConverter:
    """Test message conversion for OpenAI API."""

    def test_convert_messages_with_system_prompt(self):
        from openagent.provider.openai import OpenAIConverterMixin

        conv = OpenAIConverterMixin()
        result = conv.convert_messages(
            messages=[Message(role="user", content="hello")],
            system_prompt="be helpful",
        )
        msgs = result["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "be helpful"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "hello"

    def test_convert_messages_system_msg(self):
        from openagent.provider.openai import OpenAIConverterMixin

        conv = OpenAIConverterMixin()
        result = conv.convert_messages(
            messages=[
                Message(role="system", content="rules"),
                Message(role="user", content="hi"),
            ],
        )
        msgs = result["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_convert_messages_assistant_tool_call(self):
        from openagent.provider.openai import OpenAIConverterMixin

        conv = OpenAIConverterMixin()
        result = conv.convert_messages(
            messages=[
                Message(role="user", content="what's the weather"),
                Message(
                    role="assistant",
                    content=[
                        TextBlock(text="checking..."),
                        ToolUseBlock(id="call_1", name="get_weather", arguments={"city": "Paris"}),
                    ],
                ),
            ],
        )
        assistant_msg = result["messages"][1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == "checking..."
        assert len(assistant_msg["tool_calls"]) == 1
        assert assistant_msg["tool_calls"][0]["id"] == "call_1"
        assert assistant_msg["tool_calls"][0]["function"]["name"] == "get_weather"

    def test_convert_messages_tool_result(self):
        from openagent.provider.openai import OpenAIConverterMixin

        conv = OpenAIConverterMixin()
        result = conv.convert_messages(
            messages=[
                Message(role="user", content="hi"),
                Message(
                    role="tool_result",
                    content=[
                        ToolResultBlock(tool_use_id="call_1", content="22C"),
                    ],
                ),
            ],
        )
        tool_msg = result["messages"][1]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "call_1"
        assert tool_msg["content"] == "22C"

    def test_convert_response_text_only(self):
        from openagent.provider.openai import OpenAIConverterMixin

        conv = OpenAIConverterMixin()
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Hello!"
        mock_resp.choices[0].message.tool_calls = None

        msg = conv.convert_response(mock_resp)
        assert msg.role == "assistant"
        assert msg.text == "Hello!"

    def test_convert_response_with_tool_calls(self):
        from openagent.provider.openai import OpenAIConverterMixin

        conv = OpenAIConverterMixin()
        tool_call = MagicMock()
        tool_call.id = "call_1"
        tool_call.function.name = "get_weather"
        tool_call.function.arguments = '{"city": "Paris"}'

        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Checking..."
        mock_resp.choices[0].message.tool_calls = [tool_call]

        msg = conv.convert_response(mock_resp)
        assert msg.role == "assistant"
        assert isinstance(msg.content, list)
        tool_blocks = [b for b in msg.content if isinstance(b, ToolUseBlock)]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].name == "get_weather"

    def test_convert_tools(self):
        from openagent.provider.openai import OpenAIConverterMixin

        conv = OpenAIConverterMixin()
        tools = [ToolDef(name="get_weather", description="Weather info", parameters={"type": "object"})]
        result = conv.convert_tools(tools)

        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "get_weather"


# ============================================================================
# OpenAIProvider chat
# ============================================================================


class TestOpenAIProviderChat:
    def test_chat_calls_api(self):
        from openagent.provider.openai import OpenAIProvider

        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.model = "gpt-4o"
        provider._max_retries = 0

        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "OK"
        mock_resp.choices[0].message.tool_calls = None

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        provider._client = mock_client

        import asyncio
        result = asyncio.run(provider.chat(
            messages=[Message(role="user", content="hi")],
            system_prompt="",
        ))
        assert result.text == "OK"

    def test_chat_with_tools(self):
        from openagent.provider.openai import OpenAIProvider

        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.model = "gpt-4o"
        provider._max_retries = 0

        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = ""
        mock_resp.choices[0].message.tool_calls = None

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        provider._client = mock_client

        import asyncio
        asyncio.run(provider.chat(
            messages=[Message(role="user", content="hi")],
            tools=[ToolDef(name="test", description="test", parameters={})],
            system_prompt="",
        ))
        mock_client.chat.completions.create.assert_called_once()


# ============================================================================
# OpenAIProvider stream
# ============================================================================


class TestOpenAIProviderStream:
    def test_stream_yields_chunks(self):
        from openagent.provider.openai import OpenAIProvider

        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.model = "gpt-4o"

        chunks = [
            MagicMock(choices=[MagicMock(delta=MagicMock(content="Hello"))]),
            MagicMock(choices=[MagicMock(delta=MagicMock(content=" world"))]),
            MagicMock(choices=[MagicMock(delta=MagicMock(content=None))]),
        ]

        mock_stream = AsyncMock().__aiter__
        iter_chunks = iter(chunks)
        async def async_iter():
            for c in iter_chunks:
                yield c

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=async_iter())
        provider._client = mock_client

        import asyncio
        async def run():
            collected = []
            async for chunk in provider.stream(
                messages=[Message(role="user", content="hi")],
            ):
                collected.append(chunk)
            return collected

        result = asyncio.run(run())
        assert result == ["Hello", " world"]


# ============================================================================
# OpenAIProvider retry
# ============================================================================


class TestOpenAIProviderRetry:
    def test_retry_on_failure_then_success(self):
        from openagent.provider.openai import OpenAIProvider
        from openai import APIConnectionError
        from httpx import Request

        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.model = "gpt-4o"
        provider._max_retries = 3

        call_count = 0
        mock_request = MagicMock(spec=Request)

        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise APIConnectionError(message="fail", request=mock_request)
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = "OK"
            mock_resp.choices[0].message.tool_calls = None
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create
        provider._client = mock_client

        import asyncio
        result = asyncio.run(provider.chat(
            messages=[Message(role="user", content="hi")],
            system_prompt="",
        ))
        assert result.text == "OK"
        assert call_count == 3
