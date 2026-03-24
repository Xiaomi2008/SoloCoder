from __future__ import annotations

from dataclasses import fields, is_dataclass
import pytest

from openagent.provider import ProviderError as CompatProviderError
from openagent.provider import ProviderStreamEvent as CompatProviderStreamEvent
from openagent.provider import ProviderToolCall as CompatProviderToolCall
from openagent.providers import (
    ProviderError,
    ProviderMessageCompleted,
    ProviderMessageStarted,
    ProviderTextDelta,
    ProviderToolCall,
)
from openagent.providers.events import ProviderStreamEvent, ProviderTerminalEvent


def test_provider_stream_events_use_stable_type_discriminators():
    started = ProviderMessageStarted(message_id="msg_123")
    delta = ProviderTextDelta(message_id="msg_123", delta="Hel")
    completed = ProviderMessageCompleted(message_id="msg_123")
    tool_call = ProviderToolCall(
        message_id="msg_123",
        id="tool_456",
        name="search",
        arguments={"query": "weather"},
    )
    error = ProviderError(message_id="msg_123", error="stream interrupted")

    assert started.type == "provider_message_started"
    assert delta.type == "provider_text_delta"
    assert completed.type == "provider_message_completed"
    assert tool_call.type == "provider_tool_call"
    assert error.type == "provider_error"


def test_provider_stream_event_exposes_shared_typed_shape():
    event = ProviderTextDelta(message_id="msg_123", delta="Hel")

    assert is_dataclass(event)
    assert [field.name for field in fields(event)] == ["message_id", "delta"]
    assert isinstance(event, ProviderStreamEvent)
    assert event.message_id == "msg_123"
    assert event.type == "provider_text_delta"


def test_provider_stream_event_base_type_is_abstract():
    with pytest.raises(TypeError):
        ProviderStreamEvent(message_id="msg_123")


def test_provider_terminal_events_cover_completed_and_error_variants():
    terminal_events: list[ProviderTerminalEvent] = [
        ProviderMessageCompleted(message_id="msg_123"),
        ProviderError(message_id="msg_123", error="provider failed"),
    ]

    assert [event.type for event in terminal_events] == [
        "provider_message_completed",
        "provider_error",
    ]
    assert all(isinstance(event, ProviderStreamEvent) for event in terminal_events)


def test_provider_tool_call_carries_canonical_tool_use_inputs():
    event = ProviderToolCall(
        message_id="msg_123",
        id="tool_456",
        name="search",
        arguments={"query": "weather", "units": "metric"},
    )

    assert event.message_id == "msg_123"
    assert event.id == "tool_456"
    assert event.name == "search"
    assert event.arguments == {"query": "weather", "units": "metric"}


def test_singular_provider_package_explicitly_reexports_stream_contract_types():
    assert CompatProviderStreamEvent is ProviderStreamEvent
    assert CompatProviderToolCall is ProviderToolCall
    assert CompatProviderError is ProviderError
