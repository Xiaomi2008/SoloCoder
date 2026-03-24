"""Tests for Session class."""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
from pathlib import Path

import pytest

from openagent import Session
from openagent.core.types import Message, TextBlock, ToolResultBlock, ToolUseBlock
from openagent.provider.base import BaseProvider


class DummyProvider(BaseProvider):
    async def chat(self, messages, tools=None, system_prompt="", **kwargs):
        assert all(isinstance(message, Message) for message in messages)
        return Message(role="assistant", content="Compacted summary")


def test_session_init():
    """Test session initialization."""
    session = Session(system_prompt="Test prompt")

    assert session.system_prompt == "Test prompt"
    assert len(session) == 0
    assert session.messages == []


def test_session_add_message():
    """Test adding messages."""
    session = Session()

    msg = session.add("user", "Hello!")
    assert len(session) == 1
    assert msg.role == "user"
    assert msg.content == "Hello!"


def test_session_add_tool_results():
    """Test adding tool results."""
    session = Session()

    results = [
        ToolResultBlock(tool_use_id="123", content="Result"),
    ]
    msg = session.add_tool_results(results)

    assert len(session) == 1
    assert msg.role == "tool_result"


def test_session_clear():
    """Test clearing session."""
    session = Session()
    session.add("user", "Hello!")
    session.add("assistant", "Hi!")

    assert len(session) == 2
    session.clear()
    assert len(session) == 0


def test_session_replace_history_replaces_messages_with_summary():
    """Replacing history should swap the authoritative session store."""
    session = Session(system_prompt="Test prompt")
    session.add("user", "Old request")
    session.add("assistant", "Old response")

    replacement = [
        Message(role="system", content="Conversation summary:\n\nCompacted history"),
        Message(role="user", content="Latest request"),
    ]

    session.replace_history(replacement)
    replacement.append(Message(role="assistant", content="Should not leak in"))

    assert [message.role for message in session.messages] == ["system", "user"]
    assert session.messages[0].content == "Conversation summary:\n\nCompacted history"
    assert session.messages[1].content == "Latest request"


def test_session_to_list():
    """Test session serialization to list."""
    session = Session()
    session.add("user", "Hello!")
    session.add("assistant", "Hi!")

    data = session.to_list()

    assert len(data) == 2
    assert data[0]["role"] == "user"
    assert data[0]["content"] == "Hello!"


def test_session_to_list_complex():
    """Test serialization with complex content blocks."""
    session = Session()
    session.add("user", "Help me")

    # Add a message with multiple content blocks
    msg = Message(
        role="assistant",
        content=[
            TextBlock(text="Sure!"),
            ToolUseBlock(id="123", name="search", arguments={"q": "test"}),
        ],
    )
    session.add_message(msg)

    data = session.to_list()

    assert len(data) == 2
    assert len(data[1]["content"]) == 2
    assert data[1]["content"][0]["type"] == "text"
    assert data[1]["content"][1]["type"] == "tool_use"


def test_session_save_load():
    """Test session persistence."""
    session = Session(system_prompt="Test system")
    session.add("user", "Hello!")
    session.add("assistant", "Hi there!")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        path = f.name

    try:
        session.save(path)

        # Verify file was created
        assert Path(path).exists()

        # Load and verify
        loaded = Session.load(path)
        assert loaded.system_prompt == "Test system"
        assert len(loaded) == 2
        assert loaded.messages[0].content == "Hello!"
        assert loaded.messages[1].content == "Hi there!"
    finally:
        Path(path).unlink()


def test_session_save_load_complex():
    """Test persistence with complex content."""
    session = Session(system_prompt="Complex test")

    # Add message with tool use
    msg = Message(
        role="assistant",
        content=[
            TextBlock(text="Running tool"),
            ToolUseBlock(id="abc", name="search", arguments={"query": "test"}),
        ],
    )
    session.add_message(msg)

    # Add tool result
    session.add_tool_results(
        [
            ToolResultBlock(tool_use_id="abc", content="Found it", is_error=False),
        ]
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        path = f.name

    try:
        session.save(path)
        loaded = Session.load(path)

        assert len(loaded) == 2

        # Check tool use was preserved
        first_msg = loaded.messages[0]
        assert len(first_msg.content) == 2
        assert isinstance(first_msg.content[0], TextBlock)
        assert isinstance(first_msg.content[1], ToolUseBlock)
        assert first_msg.content[1].name == "search"

        # Check tool result was preserved
        second_msg = loaded.messages[1]
        assert isinstance(second_msg.content[0], ToolResultBlock)
        assert second_msg.content[0].content == "Found it"
    finally:
        Path(path).unlink()


def test_session_save_load_preserves_tool_result_tool_name():
    """Tool result tool names should survive session persistence."""
    session = Session()
    session.add_tool_results(
        [
            ToolResultBlock(
                tool_use_id="abc",
                tool_name="search",
                content="Found it",
            )
        ]
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        path = f.name

    try:
        session.save(path)
        loaded = Session.load(path)

        tool_result = loaded.messages[0].content[0]
        assert isinstance(tool_result, ToolResultBlock)
        assert tool_result.tool_name == "search"
    finally:
        Path(path).unlink()


@pytest.mark.asyncio
async def test_compact_context_uses_canonical_messages_and_summary_boundary():
    """Compaction should call providers with Message objects and keep a valid tail."""
    session = Session(system_prompt="Test system")
    session.add("user", "First request")
    session.add_message(
        Message(
            role="assistant",
            content=[
                TextBlock(text="Running tool"),
                ToolUseBlock(id="tool-1", name="search", arguments={"q": "x"}),
            ],
        )
    )
    session.add_tool_results([ToolResultBlock(tool_use_id="tool-1", content="done")])
    session.add("assistant", "Tool finished")
    session.add("user", "Second request")
    session.add("assistant", "Second response")

    summary = await session.compact_context(DummyProvider(model="dummy"), keep_recent=3)

    assert summary == "Compacted summary"
    assert session.messages[0].role == "system"
    assert session.messages[0].content == "Conversation summary:\n\nCompacted summary"
    assert [message.role for message in session.messages[1:]] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_compact_context_fallback_keeps_messages_from_user_boundary():
    """Fallback truncation should not start with an assistant-only fragment."""
    session = Session()
    session.add("user", "First request")
    session.add("assistant", "First response")
    session.add_message(
        Message(
            role="assistant",
            content=[
                TextBlock(text="Running tool"),
                ToolUseBlock(id="tool-2", name="search", arguments={"q": "y"}),
            ],
        )
    )
    session.add_tool_results([ToolResultBlock(tool_use_id="tool-2", content="done")])
    session.add("assistant", "Tool finished")
    session.add("user", "Latest request")
    session.add("assistant", "Latest response")

    class FailingProvider(BaseProvider):
        async def chat(self, messages, tools=None, system_prompt="", **kwargs):
            raise RuntimeError("boom")

    summary = await session.compact_context(
        FailingProvider(model="dummy"), keep_recent=2
    )

    assert "Compaction failed" in summary
    assert session.messages[0].role == "user"
    assert [message.role for message in session.messages] == ["user", "assistant"]


def test_session_token_count_uses_fallback_when_tiktoken_unavailable(monkeypatch):
    """Token counting should not crash if tiktoken cannot be imported."""
    session = Session()
    session.add("user", "alpha beta")
    session.add_message(
        Message(role="assistant", content=[TextBlock(text="gamma delta")])
    )

    monkeypatch.setitem(sys.modules, "tiktoken", None)
    monkeypatch.delitem(sys.modules, "openagent.core.utils", raising=False)

    token_count = session.token_count

    assert token_count == 12

    monkeypatch.delitem(sys.modules, "openagent.core.utils", raising=False)
    importlib.import_module("openagent.core.utils")


def test_session_check_compaction_needed_uses_fallback_when_tiktoken_unavailable(
    monkeypatch,
):
    """Compaction checks should keep working without tiktoken."""
    session = Session()
    session.add("user", "alpha beta")
    session.add("assistant", "gamma delta")

    monkeypatch.setitem(sys.modules, "tiktoken", None)
    monkeypatch.delitem(sys.modules, "openagent.core.utils", raising=False)

    assert session.check_compaction_needed(max_tokens=11, threshold=1.0) is True

    monkeypatch.delitem(sys.modules, "openagent.core.utils", raising=False)
    importlib.import_module("openagent.core.utils")
