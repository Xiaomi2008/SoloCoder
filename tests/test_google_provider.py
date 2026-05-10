"""Tests for GoogleProvider - converter, chat, stream."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from openagent.core.types import (
    Message, TextBlock, ToolDef, ToolResultBlock, ToolUseBlock,
)


def _mock_google_module():
    """Inject a fake google.genai.types module so tests work without the SDK installed."""
    mock_types = MagicMock()
    mock_genai = MagicMock()
    mock_genai.types = mock_types
    mock_google = MagicMock()
    mock_google.genai = mock_genai

    sys.modules.setdefault("google", mock_google)
    sys.modules.setdefault("google.genai", mock_genai)
    return mock_types


# ============================================================================
# GoogleConverterMixin
# ============================================================================


class TestGoogleConverter:
    """Test message conversion for Google Gemini API."""

    def test_convert_messages_system_instruction(self):
        _mock_google_module()
        from openagent.provider.google import GoogleConverterMixin

        conv = GoogleConverterMixin()
        result = conv.convert_messages(
            messages=[Message(role="user", content="hello")],
            system_prompt="be helpful",
        )
        assert "system_instruction" in result
        assert "be helpful" in result["system_instruction"]
        assert len(result["contents"]) == 1

    def test_convert_messages_user_and_assistant(self):
        _mock_google_module()
        from openagent.provider.google import GoogleConverterMixin

        conv = GoogleConverterMixin()
        result = conv.convert_messages(
            messages=[
                Message(role="user", content="hi"),
                Message(role="assistant", content="hello back"),
            ],
        )
        contents = result["contents"]
        assert len(contents) == 2

    def test_convert_messages_tool_result(self):
        _mock_google_module()
        from openagent.provider.google import GoogleConverterMixin

        conv = GoogleConverterMixin()
        result = conv.convert_messages(
            messages=[
                Message(
                    role="tool_result",
                    content=[ToolResultBlock(tool_use_id="fc_1", content="22C")],
                ),
            ],
        )
        contents = result["contents"]
        assert len(contents) == 1

    def test_convert_response_text(self):
        from openagent.provider.google import GoogleConverterMixin

        conv = GoogleConverterMixin()
        mock_part = MagicMock()
        mock_part.text = "Hello!"
        mock_part.function_call = None

        mock_resp = MagicMock()
        mock_resp.candidates[0].content.parts = [mock_part]

        msg = conv.convert_response(mock_resp)
        assert msg.text == "Hello!"

    def test_convert_response_with_function_call(self):
        from openagent.provider.google import GoogleConverterMixin

        conv = GoogleConverterMixin()
        mock_fc = MagicMock()
        mock_fc.name = "get_weather"
        mock_fc.args = MagicMock()
        mock_fc.args.to_dict.return_value = {"city": "Paris"}

        mock_part = MagicMock()
        mock_part.text = None
        mock_part.function_call = mock_fc

        mock_resp = MagicMock()
        mock_resp.candidates[0].content.parts = [mock_part]

        msg = conv.convert_response(mock_resp)
        tool_blocks = [b for b in msg.content if isinstance(b, ToolUseBlock)]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].name == "get_weather"

    def test_convert_tools(self):
        from openagent.provider.google import GoogleConverterMixin

        conv = GoogleConverterMixin()
        tools = [ToolDef(name="get_weather", description="Weather info", parameters={"type": "object"})]
        result = conv.convert_tools(tools)

        assert len(result) == 1
        assert result[0]["name"] == "get_weather"


# ============================================================================
# GoogleProvider chat
# ============================================================================


class TestGoogleProviderChat:
    def test_chat_calls_api(self):
        _mock_google_module()
        from openagent.provider.google import GoogleProvider

        provider = GoogleProvider.__new__(GoogleProvider)
        provider.model = "gemini-2.0-flash"
        provider._max_retries = 0

        mock_part = MagicMock()
        mock_part.text = "OK"
        mock_part.function_call = None

        mock_resp = MagicMock()
        mock_resp.candidates[0].content.parts = [mock_part]

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)
        provider._client = mock_client

        import asyncio
        result = asyncio.run(provider.chat(
            messages=[Message(role="user", content="hi")],
            system_prompt="",
        ))
        assert result.text == "OK"
