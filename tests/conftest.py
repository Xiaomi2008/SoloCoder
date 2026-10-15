"""Pytest fixtures for OpenAgent tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from openagent.core.types import Message, TextBlock, ToolDef, ToolUseBlock


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point the config file at a temp dir for every test.

    ``ProviderRegistry`` reads the user's config (custom model servers live
    there), so without this the suite sees the developer's own servers and can
    write to their config file. Same reason the Keychain is never touched.
    """
    from openagent.core import config as cfg

    monkeypatch.setattr(cfg.ConfigManager, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "_config", None)
    yield
    monkeypatch.setattr(cfg, "_config", None)


class MockProvider:
    """Mock provider for testing without real API calls."""

    def __init__(self, responses: list[Message] | None = None) -> None:
        self.responses = responses or []
        self._call_count = 0
        self.model = "mock-model"
        self.api_key = None

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> Message:
        if self._call_count < len(self.responses):
            response = self.responses[self._call_count]
            self._call_count += 1
            return response
        return Message(role="assistant", content="Default response")


@pytest.fixture
def scripted_provider():
    """A provider whose ``stream`` replays a script — see tests/support."""
    from tests.support.streaming_provider import ScriptedStreamProvider

    def _create(turns: list[list[Any]] | None = None) -> ScriptedStreamProvider:
        return ScriptedStreamProvider(turns)

    return _create


@pytest.fixture
def mock_provider():
    """Create a mock provider with configurable responses."""

    def _create(responses: list[Message] | None = None) -> MockProvider:
        return MockProvider(responses)

    return _create


@pytest.fixture
def simple_response():
    """Create a simple text response."""
    return Message(role="assistant", content="Hello!")


@pytest.fixture
def tool_call_response():
    """Create a response with a tool call."""
    return Message(
        role="assistant",
        content=[
            TextBlock(text="Let me check that."),
            ToolUseBlock(
                id="call_123",
                name="get_weather",
                arguments={"city": "Tokyo"},
            ),
        ],
    )
