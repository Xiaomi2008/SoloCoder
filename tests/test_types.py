"""Tests for Message types."""

from __future__ import annotations

import pytest

from openagent.core.types import (
    Message,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
    assistant_message,
    text_message,
    tool_result_message,
)
from openagent.model import Message as PublicMessage
from openagent.model import ToolUseBlock as PublicToolUseBlock


def test_text_message():
    """Test text message creation."""
    msg = text_message("user", "Hello!")

    assert msg.role == "user"
    assert msg.content == "Hello!"
    assert msg.text == "Hello!"


def test_assistant_message():
    """Test assistant message with content blocks."""
    msg = assistant_message(
        [
            TextBlock(text="Here's the answer"),
            ToolUseBlock(id="123", name="search", arguments={"q": "test"}),
        ]
    )

    assert msg.role == "assistant"
    assert len(msg.content) == 2


def test_message_text_property():
    """Test text property extraction."""
    # Simple string content
    msg1 = Message(role="user", content="Hello")
    assert msg1.text == "Hello"

    # Block content
    msg2 = Message(
        role="assistant",
        content=[
            TextBlock(text="Line 1"),
            ToolUseBlock(id="1", name="t", arguments={}),
            TextBlock(text="Line 2"),
        ],
    )
    assert msg2.text == "Line 1\nLine 2"


def test_message_tool_calls_property():
    """Test tool_calls extraction."""
    msg = Message(
        role="assistant",
        content=[
            TextBlock(text="Check this"),
            ToolUseBlock(id="1", name="search", arguments={"q": "a"}),
            ToolUseBlock(id="2", name="fetch", arguments={"url": "b"}),
        ],
    )

    calls = msg.tool_calls
    assert len(calls) == 2
    assert calls[0].name == "search"
    assert calls[1].name == "fetch"


def test_message_has_tool_calls():
    """Test has_tool_calls property."""
    msg_with = Message(
        role="assistant",
        content=[ToolUseBlock(id="1", name="t", arguments={})],
    )
    msg_without = Message(role="assistant", content="Just text")

    assert msg_with.has_tool_calls is True
    assert msg_without.has_tool_calls is False


def test_tool_use_block_default_id():
    """Test ToolUseBlock generates ID if not provided."""
    block = ToolUseBlock(name="test", arguments={"x": 1})

    assert block.id.startswith("call_")
    assert len(block.id) > 5


def test_tool_result_block():
    """Test ToolResultBlock creation."""
    result = ToolResultBlock(
        tool_use_id="123",
        content="Success",
        is_error=False,
    )

    assert result.tool_use_id == "123"
    assert result.content == "Success"
    assert result.is_error is False


def test_tool_result_message():
    """Test tool result message creation."""
    msg = tool_result_message(
        [
            ToolResultBlock(tool_use_id="1", content="Result 1"),
            ToolResultBlock(tool_use_id="2", content="Result 2", is_error=True),
        ]
    )

    assert msg.role == "tool_result"
    assert len(msg.tool_results) == 2


def test_tool_def():
    """Test ToolDef creation."""
    tool_def = ToolDef(
        name="search",
        description="Search for information",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )

    assert tool_def.name == "search"
    assert "query" in tool_def.parameters["properties"]


def test_openagent_model_exports_canonical_types():
    """Test openagent.model exposes the canonical type objects."""
    assert PublicMessage is Message
    assert PublicToolUseBlock is ToolUseBlock
