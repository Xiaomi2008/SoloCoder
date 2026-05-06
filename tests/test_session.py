"""Tests for Session class."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from openagent import Session
from openagent.core.types import Message, TextBlock, ToolResultBlock, ToolUseBlock


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
    session.add_tool_results([
        ToolResultBlock(tool_use_id="abc", content="Found it", is_error=False),
    ])

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


def _make_tool_turn(call_id: str, name: str = "bash") -> tuple:
    """Helper to create a paired assistant+tool_result turn."""
    assistant = Message(
        role="assistant",
        content=[ToolUseBlock(id=call_id, name=name, arguments={"cmd": f"echo {name}"})],
    )
    result = ToolResultBlock(tool_use_id=call_id, content=f"{name} output")
    return assistant, result


def _collect_tool_call_ids(messages: list[Message]) -> set[str]:
    ids = set()
    for msg in messages:
        if isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    ids.add(block.id)
    return ids


def _collect_tool_result_ids(messages: list[Message]) -> set[str]:
    ids = set()
    for msg in messages:
        if isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    ids.add(block.tool_use_id)
    return ids


class TestTurnGrouping:
    """Test that _group_into_turns keeps tool calls paired with results."""

    def test_simple_messages_form_separate_turns(self):
        session = Session()
        session.add("user", "Hello")
        session.add("assistant", "Hi there")

        turns = session._group_into_turns()
        assert len(turns) == 2
        assert all(len(t) == 1 for t in turns)

    def test_tool_call_paired_with_result(self):
        session = Session()
        session.add("user", "Search something")

        msg = Message(
            role="assistant",
            content=[ToolUseBlock(id="call_1", name="search", arguments={"q": "test"})],
        )
        session.add_message(msg)
        session.add_tool_results([
            ToolResultBlock(tool_use_id="call_1", content="Results"),
        ])

        turns = session._group_into_turns()
        # user msg, assistant+tool_result paired
        assert len(turns) == 2
        assert len(turns[0]) == 1  # user message alone
        assert len(turns[1]) == 2  # assistant + tool_result paired

    def test_tool_call_without_result(self):
        session = Session()
        msg = Message(
            role="assistant",
            content=[ToolUseBlock(id="call_1", name="search", arguments={"q": "test"})],
        )
        session.add_message(msg)

        turns = session._group_into_turns()
        assert len(turns) == 1
        assert len(turns[0]) == 1  # tool call alone, no result to pair


class TestCompressionSafety:
    """Test that compression never orphans tool calls from results."""

    def test_no_orphaned_tool_calls_after_compression(self):
        """After compression, every tool call id should have a matching result."""
        session = Session(max_messages=10, summary_threshold=5)

        for i in range(20):
            assistant, result = _make_tool_turn(f"call_{i}", name=f"tool_{i}")
            session.add_message(assistant)
            session.add_tool_results([result])

        call_ids = _collect_tool_call_ids(session.messages)
        result_ids = _collect_tool_result_ids(session.messages)
        assert call_ids == result_ids, "Tool calls and results must be paired after compression"

    def test_no_orphaned_tool_results_after_compression(self):
        """Surviving tool results should reference existing tool calls."""
        session = Session(max_messages=8, summary_threshold=4)

        for i in range(15):
            assistant, result = _make_tool_turn(f"call_{i}")
            session.add_message(assistant)
            session.add_tool_results([result])

        call_ids = _collect_tool_call_ids(session.messages)
        result_ids = _collect_tool_result_ids(session.messages)
        assert result_ids.issubset(call_ids), "No orphaned tool results"

    def test_compression_preserves_summary_message(self):
        """Compressed messages should contain a summary marker."""
        session = Session(max_messages=6, summary_threshold=3)

        for i in range(15):
            assistant, result = _make_tool_turn(f"call_{i}")
            session.add_message(assistant)
            session.add_tool_results([result])

        text_content = "\n".join(
            m.text for m in session.messages if isinstance(m.content, str)
        )
        assert "Conversation Summary" in text_content

    def test_compression_does_not_run_on_small_sessions(self):
        session = Session(max_messages=50, summary_threshold=30)
        session.add("user", "Hello")
        session.add("assistant", "Hi")

        turns_before = session._group_into_turns()
        session._compress_old_messages()
        turns_after = session._group_into_turns()
        assert len(turns_before) == len(turns_after)


class TestTokenEstimation:
    """Test token-aware compression trigger."""

    def test_estimate_tokens_short_string(self):
        session = Session()
        msg = Message(role="user", content="Hello")
        tokens = session._estimate_tokens(msg)
        assert tokens >= 1

    def test_estimate_tokens_long_content(self):
        session = Session()
        long_text = "x" * 4000
        msg = Message(role="user", content=long_text)
        tokens = session._estimate_tokens(msg)
        # ~4 chars per token, so ~1000 tokens
        assert 800 < tokens <= 1200

    def test_estimate_tokens_tool_use(self):
        session = Session()
        msg = Message(
            role="assistant",
            content=[ToolUseBlock(id="c1", name="bash", arguments={"cmd": "ls -la"})],
        )
        tokens = session._estimate_tokens(msg)
        assert tokens > 12  # overhead + argument text

    def test_estimate_tokens_tool_result(self):
        session = Session()
        msg = Message(
            role="tool_result",
            content=[ToolResultBlock(tool_use_id="c1", content="file.txt")],
        )
        tokens = session._estimate_tokens(msg)
        assert tokens > 12

    def test_token_budget_triggers_compression(self):
        """Compression fires when token budget exceeded even if message count is low."""
        session = Session(max_messages=9999, summary_threshold=0, max_tokens=200)

        # Add tool turns with long results to exceed token budget quickly
        for i in range(10):
            assistant = Message(
                role="assistant",
                content=[ToolUseBlock(id=f"call_{i}", name="bash", arguments={"cmd": "echo test"})],
            )
            session.add_message(assistant)
            # Long result content to push token count over budget
            long_result = "x" * 200 + f" output {i}"
            session.add_tool_results([
                ToolResultBlock(tool_use_id=f"call_{i}", content=long_result),
            ])

        text_content = "\n".join(
            m.text for m in session.messages if isinstance(m.content, str)
        )
        assert "Conversation Summary" in text_content

    def test_max_messages_still_works(self):
        """Message count threshold still triggers compression."""
        session = Session(max_messages=6, summary_threshold=3)

        for i in range(15):
            assistant, result = _make_tool_turn(f"call_{i}")
            session.add_message(assistant)
            session.add_tool_results([result])

        assert len(session) < 15  # Should have compressed


class TestCompressionSummary:
    """Test that summaries include useful information."""

    def test_summary_includes_errors(self):
        # Use large thresholds so we can manually trigger compression on a full set
        session = Session(max_messages=999, summary_threshold=0)

        for i in range(20):
            assistant = Message(
                role="assistant",
                content=[ToolUseBlock(id=f"call_{i}", name="bash", arguments={"cmd": "fail"})],
            )
            session.add_message(assistant)
            session.add_tool_results([
                ToolResultBlock(tool_use_id=f"call_{i}", content="Error: command failed", is_error=True),
            ])

        # Manually compress to control what gets summarized
        session._compress_old_messages()

        text_content = "\n".join(
            m.text for m in session.messages if isinstance(m.content, str)
        )
        assert "Conversation Summary" in text_content
        assert "Errors:" in text_content or "Error:" in text_content

    def test_summary_includes_file_paths(self):
        session = Session(max_messages=999, summary_threshold=0)

        for i in range(20):
            assistant = Message(
                role="assistant",
                content=[ToolUseBlock(id=f"call_{i}", name="edit", arguments={"file": f"/path/to/file{i}.py"})],
            )
            session.add_message(assistant)
            session.add_tool_results([
                ToolResultBlock(tool_use_id=f"call_{i}", content=f"Edited file{i}.py"),
            ])

        # Manually compress
        session._compress_old_messages()

        text_content = "\n".join(
            m.text for m in session.messages if isinstance(m.content, str)
        )
        assert "Files modified:" in text_content
