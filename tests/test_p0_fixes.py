"""Tests for P0 fixes: git tools in CoderAgent, task() removal, AnthropicProvider stream+retry."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openagent.core.types import Message, TextBlock, ToolUseBlock


# ============================================================================
# 1. Git tools registered in CoderAgent
# ============================================================================


class TestCoderAgentGitTools:
    """Verify core tools are present in CoderAgent's tool registry."""

    def _get_tool_names(self):
        """Extract registered tool names from a CoderAgent instance."""
        from openagent.provider.openai import OpenAIProvider
        from openagent.coder import CoderAgent

        mock_provider = MagicMock(spec=OpenAIProvider)
        mock_provider.model = "gpt-4o"
        mock_provider.api_key = None
        mock_provider.chat = AsyncMock(return_value=Message(role="assistant", content="ok"))

        agent = CoderAgent(provider=mock_provider)
        return list(agent.tool_registry._tools.keys())

    def test_awk_registered(self):
        names = self._get_tool_names()
        assert "awk" in names

    def test_bash_registered(self):
        names = self._get_tool_names()
        assert "bash" in names

    def test_read_registered(self):
        names = self._get_tool_names()
        assert "read" in names

    def test_write_registered(self):
        names = self._get_tool_names()
        assert "write" in names

    def test_grep_registered(self):
        names = self._get_tool_names()
        assert "grep" in names


# ============================================================================
# 2. task() tool removed
# ============================================================================


class TestTaskToolRemoved:
    """Verify the broken task() stub is no longer available."""

    def test_task_not_in_tools_module(self):
        import openagent.tools as tools
        assert not hasattr(tools, "task")

    def test_task_not_in_builtin_module(self):
        from openagent.tools import builtin
        assert not hasattr(builtin, "task")

    def test_task_not_in_tools_all(self):
        from openagent.tools import __all__
        assert "task" not in __all__

    def test_task_not_in_builtin_all(self):
        from openagent.tools.builtin import __all__
        assert "task" not in __all__

    def test_task_not_in_coder_agent(self):
        """Verify task tool is not registered in CoderAgent."""
        from openagent.provider.openai import OpenAIProvider
        from openagent.coder import CoderAgent

        mock_provider = MagicMock(spec=OpenAIProvider)
        mock_provider.model = "gpt-4o"
        mock_provider.api_key = None
        mock_provider.chat = AsyncMock(return_value=Message(role="assistant", content="ok"))

        agent = CoderAgent(provider=mock_provider)
        names = list(agent.tool_registry._tools.keys())
        assert "task" not in names


# ============================================================================
# 3. AnthropicProvider stream() method
# ============================================================================


class TestAnthropicProviderStream:
    """Verify AnthropicProvider has streaming and retry support."""

    def test_stream_method_exists(self):
        from openagent.provider.anthropic import AnthropicProvider
        assert hasattr(AnthropicProvider, "stream")

    def test_retry_method_exists(self):
        from openagent.provider.anthropic import AnthropicProvider
        assert hasattr(AnthropicProvider, "_chat_with_retry")
        assert hasattr(AnthropicProvider, "chat")

    def test_max_retries_parameter(self):
        import inspect
        from openagent.provider.anthropic import AnthropicProvider
        sig = inspect.signature(AnthropicProvider.__init__)
        assert "max_retries" in sig.parameters
        assert sig.parameters["max_retries"].default == 3

    def test_chat_delegates_to_retry(self):
        """Verify chat() calls _chat_with_retry, not the API directly."""
        import inspect
        from openagent.provider.anthropic import AnthropicProvider
        source = inspect.getsource(AnthropicProvider.chat)
        assert "_chat_with_retry" in source

    def test_stream_yields_text_chunks(self):
        """Verify stream() yields text chunks from the response stream."""
        from openagent.provider.anthropic import AnthropicProvider
        from openagent.core.types import Message

        provider = AnthropicProvider.__new__(AnthropicProvider)
        provider.model = "claude-sonnet-4-20250514"

        chunks = ["Hello", ", ", "world!"]
        chunk_iter = iter(chunks)

        class _AsyncIter:
            async def __aiter__(self):
                for c in chunk_iter:
                    yield c

        mock_response = MagicMock()
        mock_response.text_stream = _AsyncIter()

        class _Ctx:
            async def __aenter__(self):
                return mock_response
            async def __aexit__(self, *args):
                pass

        mock_stream_obj = MagicMock()
        mock_stream_obj.return_value = _Ctx()

        mock_client = MagicMock()
        mock_client.messages.stream = mock_stream_obj
        provider._client = mock_client

        async def run():
            collected = []
            async for chunk in provider.stream(
                messages=[Message(role="user", content="hi")],
                system_prompt="",
            ):
                collected.append(chunk)
            return collected

        result = asyncio.run(run())
        assert result == ["Hello", ", ", "world!"]

    def test_stream_includes_system_prompt(self):
        """Verify stream() passes system prompt to API."""
        from openagent.provider.anthropic import AnthropicProvider
        from openagent.core.types import Message

        provider = AnthropicProvider.__new__(AnthropicProvider)
        provider.model = "claude-sonnet-4-20250514"

        captured_kwargs = {}
        ok_iter = iter(["ok"])

        class _AsyncIterOk:
            async def __aiter__(self):
                for c in ok_iter:
                    yield c

        mock_response = MagicMock()
        mock_response.text_stream = _AsyncIterOk()

        class _Ctx:
            async def __aenter__(self):
                return mock_response
            async def __aexit__(self, *args):
                pass

        def mock_stream_obj(**kwargs):
            captured_kwargs.update(kwargs)
            return _Ctx()

        mock_client = MagicMock()
        mock_client.messages.stream = mock_stream_obj
        provider._client = mock_client

        async def run():
            async for _ in provider.stream(
                messages=[Message(role="user", content="hi")],
                system_prompt="be nice",
            ):
                pass

        asyncio.run(run())
        assert captured_kwargs.get("system") == "be nice"


# ============================================================================
# 3b. AnthropicProvider retry logic
# ============================================================================


class TestAnthropicProviderRetry:
    """Verify retry logic wraps chat calls."""

    def _make_mock_error(self, cls, message="error"):
        """Create a mock provider error that retry.py will catch."""
        err = MagicMock()
        err.__class__ = cls
        err.__str__ = lambda self: message
        err.args = (message,)
        return err

    def test_retry_on_transient_failure(self):
        """chat() should retry on transient errors then succeed."""
        from openagent.provider.anthropic import AnthropicProvider
        from openagent.core.types import Message
        from anthropic import APIConnectionError

        provider = AnthropicProvider.__new__(AnthropicProvider)
        provider.model = "claude-sonnet-4-20250514"
        provider._max_retries = 3

        call_count = 0

        from httpx import Request
        mock_request = MagicMock(spec=Request)

        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise APIConnectionError(message="timeout", request=mock_request)
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(type="text", text="ok")]
            return mock_resp

        from httpx import Request
        mock_request = MagicMock(spec=Request)

        mock_client = MagicMock()
        mock_client.messages.create = mock_create
        provider._client = mock_client

        async def run():
            msg = await provider.chat(
                messages=[Message(role="user", content="hi")],
                system_prompt="",
            )
            return msg

        result = asyncio.run(run())
        assert result.text == "ok"
        assert call_count == 3  # 2 failures + 1 success

    def test_gives_up_after_max_retries(self):
        """chat() should raise after max_retries exhausted."""
        from openagent.provider.anthropic import AnthropicProvider
        from openagent.core.types import Message
        from anthropic import RateLimitError

        provider = AnthropicProvider.__new__(AnthropicProvider)
        provider.model = "claude-sonnet-4-20250514"
        provider._max_retries = 2

        mock_response = MagicMock()

        async def mock_create(**kwargs):
            raise RateLimitError(message="too many requests", response=mock_response, body="")

        mock_client = MagicMock()
        mock_client.messages.create = mock_create
        provider._client = mock_client

        async def run():
            with pytest.raises(RateLimitError):
                await provider.chat(
                    messages=[Message(role="user", content="hi")],
                    system_prompt="",
                )

        asyncio.run(run())

    def test_no_retry_on_success_first_try(self):
        """chat() should not retry when it succeeds immediately."""
        from openagent.provider.anthropic import AnthropicProvider
        from openagent.core.types import Message

        provider = AnthropicProvider.__new__(AnthropicProvider)
        provider.model = "claude-sonnet-4-20250514"
        provider._max_retries = 3

        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(type="text", text="ok")]
            return mock_resp

        mock_client = MagicMock()
        mock_client.messages.create = mock_create
        provider._client = mock_client

        async def run():
            return await provider.chat(
                messages=[Message(role="user", content="hi")],
                system_prompt="",
            )

        asyncio.run(run())
        assert call_count == 1
