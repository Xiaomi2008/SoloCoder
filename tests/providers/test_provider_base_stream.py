from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, get_type_hints

import pytest

from openagent.core.types import Message, TextBlock, ToolUseBlock
from openagent.provider.base import BaseProvider
from openagent.providers import (
    ProviderError,
    ProviderMessageCompleted,
    ProviderMessageStarted,
    ProviderStreamEvent,
    ProviderTextDelta,
    ProviderToolCall,
)


class DummyProvider(BaseProvider):
    async def chat(
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> Message:
        return Message(role="assistant", content="Hello")


class DummyToolProvider(BaseProvider):
    async def chat(
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> Message:
        return Message(
            role="assistant",
            content=[
                TextBlock(text="Let me check."),
                ToolUseBlock(
                    id="call_weather",
                    name="weather",
                    arguments={"city": "Paris"},
                ),
                TextBlock(text="Done."),
            ],
        )


class ErrorProvider(BaseProvider):
    async def chat(
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> Message:
        raise RuntimeError("provider exploded")


def test_base_provider_stream_is_typed_as_provider_event_iterator():
    hints = get_type_hints(BaseProvider.stream)

    assert hints["return"] == AsyncIterator[ProviderStreamEvent]
    assert "provider stream events" in BaseProvider.stream.__doc__.lower()


@pytest.mark.asyncio
async def test_base_provider_stream_default_fallback_yields_provider_events():
    provider = DummyProvider(model="test")

    events = [event async for event in provider.stream(messages=[])]

    assert [type(event) for event in events] == [
        ProviderMessageStarted,
        ProviderTextDelta,
        ProviderMessageCompleted,
    ]
    assert all(isinstance(event, ProviderStreamEvent) for event in events)
    assert events[0].message_id == events[1].message_id == events[2].message_id
    assert events[1].delta == "Hello"


@pytest.mark.asyncio
async def test_base_provider_stream_fallback_emits_text_and_tool_calls_in_order():
    provider = DummyToolProvider(model="test")

    events = [event async for event in provider.stream(messages=[])]

    assert [type(event) for event in events] == [
        ProviderMessageStarted,
        ProviderTextDelta,
        ProviderToolCall,
        ProviderTextDelta,
        ProviderMessageCompleted,
    ]
    assert events[1].delta == "Let me check."
    assert events[2].id == "call_weather"
    assert events[2].name == "weather"
    assert events[2].arguments == {"city": "Paris"}
    assert events[3].delta == "Done."
    assert all(isinstance(event, ProviderStreamEvent) for event in events)
    assert len({event.message_id for event in events}) == 1


@pytest.mark.asyncio
async def test_base_provider_stream_fallback_yields_terminal_error_event():
    provider = ErrorProvider(model="test")

    events = [event async for event in provider.stream(messages=[])]

    assert [type(event) for event in events] == [
        ProviderMessageStarted,
        ProviderError,
    ]
    assert events[1].error == "provider exploded"
    assert events[0].message_id == events[1].message_id
