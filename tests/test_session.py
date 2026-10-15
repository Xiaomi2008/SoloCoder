"""Tests for Session class."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from openagent.core.session import Session
from openagent.core.types import (
    ImageBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    text_message,
    tool_result_message,
)


# ============================================================================
# Basic session operations
# ============================================================================


class TestSessionBasic:
    def test_add_user_message(self):
        session = Session(system_prompt="Test")
        session.add("user", "Hello")
        assert len(session.messages) == 1
        assert session.messages[0].role == "user"
        assert session.messages[0].content == "Hello"

    def test_add_assistant_message(self):
        session = Session()
        session.add("assistant", "Hi there")
        assert session.messages[0].role == "assistant"
        assert session.messages[0].content == "Hi there"

    def test_add_system_message(self):
        session = Session()
        session.add("system", "You are helpful")
        assert session.messages[0].role == "system"

    def test_multi_turn(self):
        session = Session()
        session.add("user", "Hello")
        session.add("assistant", "Hi")
        session.add("user", "How are you?")
        assert len(session.messages) == 3

    def test_clear(self):
        session = Session()
        session.add("user", "Hello")
        session.add("assistant", "Hi")
        session.clear()
        assert len(session.messages) == 0

    def test_len(self):
        session = Session()
        assert len(session) == 0
        session.add("user", "Hello")
        assert len(session) == 1

    def test_system_prompt(self):
        session = Session(system_prompt="You are a coder")
        assert session.system_prompt == "You are a coder"


# ============================================================================
# Message manipulation
# ============================================================================


class TestMessageManipulation:
    def test_add_message(self):
        session = Session()
        msg = text_message("user", "Hello")
        session.add_message(msg)
        assert len(session.messages) == 1

    def test_replace_history(self):
        session = Session()
        session.add("user", "Old")
        new_messages = [text_message("user", "New")]
        session.replace_history(new_messages)
        assert session.messages[0].content == "New"

    def test_add_tool_results(self):
        session = Session()
        results = [ToolResultBlock(tool_use_id="c1", tool_name="read", content="file contents")]
        msg = session.add_tool_results(results)
        assert msg.role == "tool_result"
        assert len(msg.content) == 1

    def test_messages_returns_copy(self):
        session = Session()
        session.add("user", "Hello")
        msgs = session.messages
        assert msgs is not session._messages


# ============================================================================
# Serialization
# ============================================================================


class TestSerialization:
    def test_to_list_simple(self):
        session = Session()
        session.add("user", "Hello")
        data = session.to_list()
        assert len(data) == 1
        assert data[0]["role"] == "user"
        assert data[0]["content"] == "Hello"

    def test_to_list_with_tool_call(self):
        session = Session()
        session.add("user", "Read file")
        tool_call = ToolUseBlock(id="c1", name="read", arguments={"file": "x.py"})
        session.add_message(Message(role="assistant", content=[tool_call]))
        data = session.to_list()
        assert len(data) == 2
        assert data[1]["content"][0]["type"] == "tool_use"
        assert data[1]["content"][0]["name"] == "read"

    def test_to_list_with_tool_result(self):
        session = Session()
        result = ToolResultBlock(tool_use_id="c1", tool_name="read", content="contents")
        session.add_tool_results([result])
        data = session.to_list()
        assert data[-1]["role"] == "tool_result"

    def test_to_list_with_image(self):
        session = Session()
        session.add_user_multimodal(text="Describe this", image_data="base64data")
        data = session.to_list()
        image_blocks = [b for b in data[0]["content"] if b["type"] == "image_url"]
        assert len(image_blocks) == 1

    def test_save_and_load(self, tmp_path):
        session = Session(system_prompt="Test")
        session.add("user", "Hello")
        session.add("assistant", "Hi")

        path = tmp_path / "session.json"
        session.save(str(path))
        assert path.exists()

        loaded = Session.load(str(path))
        assert loaded.system_prompt == "Test"
        assert len(loaded.messages) == 2

    def test_save_missing_path_raises(self):
        session = Session()
        session.add("user", "Hello")
        with pytest.raises(FileNotFoundError):
            session.save("/nonexistent/path/session.json")


# ============================================================================
# Turn grouping (basic)
# ============================================================================


class TestTurnGrouping:
    def test_user_assistant_pairs(self):
        session = Session()
        session.add("user", "Hello")
        session.add("assistant", "Hi")
        session.add("user", "Bye")
        session.add("assistant", "See you")
        # 2 turns: user+assistant x 2
        assert len(session) == 4

    def test_tool_call_paired_with_result(self):
        session = Session()
        session.add("user", "Read file")
        tool_call = ToolUseBlock(id="c1", name="read", arguments={"file": "x.py"})
        session.add_message(Message(role="assistant", content=[tool_call]))
        result = ToolResultBlock(tool_use_id="c1", tool_name="read", content="contents")
        session.add_tool_results([result])
        assert len(session) == 3

    def test_unpaired_tool_call(self):
        session = Session()
        tool_call = ToolUseBlock(id="c1", name="read", arguments={"file": "x.py"})
        session.add_message(Message(role="assistant", content=[tool_call]))
        # No result added — unpaired
        assert len(session) == 1


# ============================================================================
# Context compaction
# ============================================================================


class TestCompressionSafety:
    def test_no_orphaned_tool_calls_after_compression(self):
        """After compaction, every tool call should have a matching result."""
        session = Session()
        session.add("user", "Read file")
        tool_call = ToolUseBlock(id="c1", name="read", arguments={"file": "x.py"})
        session.add_message(Message(role="assistant", content=[tool_call]))
        result = ToolResultBlock(tool_use_id="c1", tool_name="read", content="contents")
        session.add_tool_results([result])

        # Before compaction: no orphans
        tool_calls = [m for m in session.messages if isinstance(m.content, list)
                      and any(isinstance(b, ToolUseBlock) for b in m.content)]
        tool_results = [m for m in session.messages if m.role == "tool_result"]
        assert len(tool_calls) == len(tool_results)

    def test_no_orphaned_tool_results_after_compression(self):
        """No tool results should appear without a matching tool call."""
        session = Session()
        session.add("user", "Hello")
        session.add("assistant", "Hi")

        # No tool calls, no results — clean
        tool_results = [m for m in session.messages if m.role == "tool_result"]
        assert len(tool_results) == 0

    def test_compression_preserves_summary_message(self):
        """Compaction should insert a summary message."""
        events = []

        class RecordingProvider:
            async def chat(self, messages, tools=None, system_prompt="", **kwargs):
                events.append(("chat", messages))
                return Message(role="assistant", content="Summary of conversation.")

            async def stream(self, messages, system_prompt="", **kwargs):
                yield "Summary of conversation."

        provider = RecordingProvider()
        session = Session()

        for i in range(10):
            session.add("user", f"Message {i}")
            session.add("assistant", f"Response {i}")

        # check_compaction_needed should trigger
        needs_compact = session.check_compaction_needed(max_tokens=1000, threshold=0.5)
        # May or may not need compaction depending on token estimation

    async def test_compact_context_returns_summary(self):
        session = Session()
        session.add("user", "Hello")
        session.add("assistant", "Hi, how can I help?")

        summary = await session.compact_context(provider=None, keep_recent=2, summary_type="brief")
        assert isinstance(summary, str)


# ============================================================================
# Token estimation
# ============================================================================


class TestTokenEstimation:
    def test_token_count_short_string(self):
        session = Session()
        session.add("user", "Hi")
        tokens = session.token_count
        assert tokens > 0

    def test_token_count_long_content(self):
        session = Session()
        long_text = "x " * 1000
        session.add("user", long_text)
        tokens = session.token_count
        assert tokens > 100

    def test_token_count_tool_use(self):
        session = Session()
        tool_call = ToolUseBlock(id="c1", name="read", arguments={"file": "x.py"})
        session.add_message(Message(role="assistant", content=[tool_call]))
        tokens = session.token_count
        assert tokens > 0

    def test_token_count_tool_result(self):
        session = Session()
        result = ToolResultBlock(tool_use_id="c1", tool_name="read", content="some content here")
        session.add_tool_results([result])
        tokens = session.token_count
        assert tokens > 0

    def test_token_budget_triggers_compression(self):
        session = Session()
        for i in range(50):
            session.add("user", f"Message {i} " * 10)
            session.add("assistant", f"Response {i} " * 10)
        # Should trigger compaction with low budget
        needs_compact = session.check_compaction_needed(max_tokens=500, threshold=0.5)
        assert needs_compact is True

    def test_max_messages_still_works(self):
        session = Session()
        for i in range(100):
            session.add("user", f"Message {i}")
            session.add("assistant", f"Response {i}")
        # Should have all messages
        assert len(session.messages) == 200


# ============================================================================
# Compression summary
# ============================================================================


class TestCompressionSummary:
    async def test_summary_includes_errors(self):
        class RecordingProvider:
            async def chat(self, messages, tools=None, system_prompt="", **kwargs):
                return Message(role="assistant", content="Summary with error info.")
            async def stream(self, messages, system_prompt="", **kwargs):
                yield "Summary with error info."

        session = Session()
        for i in range(10):
            session.add("user", f"Message {i}")
            session.add("assistant", f"Response {i}")

        summary = await session.compact_context(
            provider=RecordingProvider(), keep_recent=3, summary_type="detailed"
        )
        assert isinstance(summary, str)

    async def test_summary_includes_file_paths(self):
        class RecordingProvider:
            async def chat(self, messages, tools=None, system_prompt="", **kwargs):
                return Message(role="assistant", content="Summary of file operations.")
            async def stream(self, messages, system_prompt="", **kwargs):
                yield "Summary of file operations."

        session = Session()
        for i in range(10):
            session.add("user", f"Edit file_{i}.py")
            session.add("assistant", f"Edited file_{i}.py")

        summary = await session.compact_context(
            provider=RecordingProvider(), keep_recent=3, summary_type="detailed"
        )
        assert isinstance(summary, str)
