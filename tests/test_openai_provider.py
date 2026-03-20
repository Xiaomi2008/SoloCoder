"""Tests for OpenAI provider response diagnostics."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from openagent.core.types import ToolUseBlock
from openagent.provider.openai import OpenAIProvider


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

    def test_recovers_tool_call_from_reasoning_content(self, caplog):
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

        assert isinstance(message.content, list)
        assert isinstance(message.content[0], ToolUseBlock)
        assert message.content[0].name == "read"
        assert message.content[0].arguments == {
            "path": "/Users/taozeng/Projects/SoloCoder/cli_coder.py",
            "line_start": "180",
            "line_end": "230",
        }
        assert "recovered_tool_call_from_reasoning" in caplog.text
