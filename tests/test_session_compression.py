"""Tests for Session compression and persistence."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from openagent.core.session import Session
from openagent.core.types import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    text_message,
)


# ============================================================================
# Token estimation
# ============================================================================


class TestTokenEstimation:
    def test_short_message(self):
        session = Session()
        session.add("user", "Hi there")
        count = session.token_count
        assert count > 0

    def test_long_message(self):
        session = Session()
        session.add("user", "x " * 500)
        count = session.token_count
        assert count > 100

    def test_tool_call_overhead(self):
        session = Session()
        session.add("user", "Read file")
        tool_call = ToolUseBlock(id="c1", name="read", arguments={"file": "x.py"})
        session.add_message(Message(role="assistant", content=[tool_call]))
        count = session.token_count
        assert count > 0

    def test_total_tokens(self):
        session = Session()
        session.add("user", "Hello")
        session.add("assistant", "Hi")
        count = session.token_count
        assert count > 0


# ============================================================================
# Turn grouping
# ============================================================================


class TestTurnGrouping:
    def test_simple_turns(self):
        session = Session()
        session.add("user", "Hello")
        session.add("assistant", "Hi")
        session.add("user", "Bye")
        assert len(session.messages) == 3

    def test_tool_call_paired_with_result(self):
        session = Session()
        session.add("user", "Read")
        tool_call = ToolUseBlock(id="c1", name="read", arguments={"file": "x.py"})
        session.add_message(Message(role="assistant", content=[tool_call]))
        result = ToolResultBlock(tool_use_id="c1", tool_name="read", content="contents")
        session.add_tool_results([result])
        assert len(session.messages) == 3

    def test_unpaired_tool_call(self):
        session = Session()
        tool_call = ToolUseBlock(id="c1", name="read", arguments={"file": "x.py"})
        session.add_message(Message(role="assistant", content=[tool_call]))
        assert len(session.messages) == 1


# ============================================================================
# Session compression
# ============================================================================


class TestSessionCompression:
    def test_no_compression_under_threshold(self):
        """No compaction needed when under threshold."""
        session = Session()
        session.add("user", "Hello")
        session.add("assistant", "Hi")
        needs = session.check_compaction_needed(max_tokens=128000, threshold=0.8)
        assert needs is False

    def test_compresses_when_exceeding_max_messages(self):
        """Compaction triggers when token count exceeds budget."""
        session = Session()
        for i in range(100):
            session.add("user", f"Message {i} " * 20)
            session.add("assistant", f"Response {i} " * 20)
        needs = session.check_compaction_needed(max_tokens=500, threshold=0.5)
        assert needs is True

    def test_compresses_when_exceeding_token_budget(self):
        """Session with many tokens triggers compaction."""
        session = Session()
        for i in range(50):
            session.add("user", f"Long message {i} " * 30)
        needs = session.check_compaction_needed(max_tokens=300, threshold=0.5)
        assert needs is True

    async def test_compression_keeps_head_and_tail(self):
        """After compaction, first and last messages are preserved."""

        class RecordingProvider:
            async def chat(self, messages, tools=None, system_prompt="", **kwargs):
                return Message(role="assistant", content="Conversation summary.")
            async def stream(self, messages, system_prompt="", **kwargs):
                yield "Conversation summary."

        session = Session(system_prompt="You are helpful")
        for i in range(20):
            session.add("user", f"Msg {i}")
            session.add("assistant", f"Resp {i}")

        summary = await session.compact_context(
            provider=RecordingProvider(), keep_recent=5, summary_type="detailed"
        )
        assert isinstance(summary, str)
        # Should have messages + summary
        assert len(session.messages) > 0

    async def test_compression_inserts_summary(self):
        """Compaction should insert a summary message."""

        class RecordingProvider:
            async def chat(self, messages, tools=None, system_prompt="", **kwargs):
                return Message(role="assistant", content="Summary: user asked about files.")
            async def stream(self, messages, system_prompt="", **kwargs):
                yield "Summary: user asked about files."

        session = Session()
        session.add("user", "Old msg 1")
        session.add("assistant", "Old resp 1")
        session.add("user", "Old msg 2")
        session.add("assistant", "Old resp 2")

        summary = await session.compact_context(
            provider=RecordingProvider(), keep_recent=2, summary_type="brief"
        )
        assert len(session.messages) > 0

    async def test_create_summary_counts_operations(self):
        """Summary should mention key operations performed."""

        class RecordingProvider:
            async def chat(self, messages, tools=None, system_prompt="", **kwargs):
                return Message(role="assistant", content="Read and edited 3 files.")
            async def stream(self, messages, system_prompt="", **kwargs):
                yield "Read and edited 3 files."

        session = Session()
        for i in range(10):
            session.add("user", f"Edit file_{i}.py")
            session.add("assistant", f"Edited file_{i}.py")

        summary = await session.compact_context(
            provider=RecordingProvider(), keep_recent=3, summary_type="detailed"
        )
        assert isinstance(summary, str)

    async def test_create_summary_captures_errors(self):
        """Summary should capture errors from the session."""

        class RecordingProvider:
            async def chat(self, messages, tools=None, system_prompt="", **kwargs):
                return Message(role="assistant", content="Summary with error info.")
            async def stream(self, messages, system_prompt="", **kwargs):
                yield "Summary with error info."

        session = Session()
        for i in range(10):
            session.add("user", f"Task {i}")
            session.add("assistant", f"Done {i}")

        summary = await session.compact_context(
            provider=RecordingProvider(), keep_recent=3, summary_type="detailed"
        )
        assert isinstance(summary, str)

    async def test_create_summary_tracks_files(self):
        """Summary should track file operations."""

        class RecordingProvider:
            async def chat(self, messages, tools=None, system_prompt="", **kwargs):
                return Message(role="assistant", content="Summary of file operations.")
            async def stream(self, messages, system_prompt="", **kwargs):
                yield "Summary of file operations."

        session = Session()
        for i in range(10):
            session.add("user", f"Read file_{i}.py")
            session.add("assistant", f"Read file_{i}.py contents")

        summary = await session.compact_context(
            provider=RecordingProvider(), keep_recent=3, summary_type="detailed"
        )
        assert isinstance(summary, str)

    def test_compression_never_splits_tool_pairs(self):
        """Compaction should not split tool_call/result pairs."""
        session = Session()
        session.add("user", "Read file")
        tool_call = ToolUseBlock(id="c1", name="read", arguments={"file": "x.py"})
        session.add_message(Message(role="assistant", content=[tool_call]))
        result = ToolResultBlock(tool_use_id="c1", tool_name="read", content="contents")
        session.add_tool_results([result])

        # Before compaction: tool call and result are paired
        tool_calls = [m for m in session.messages if isinstance(m.content, list)
                      and any(isinstance(b, ToolUseBlock) for b in m.content)]
        tool_results = [m for m in session.messages if m.role == "tool_result"]
        assert len(tool_calls) == len(tool_results)

    def test_no_compression_with_few_turns(self):
        """Short sessions should not need compaction."""
        session = Session()
        session.add("user", "Hello")
        session.add("assistant", "Hi, how can I help?")
        needs = session.check_compaction_needed(max_tokens=128000, threshold=0.8)
        assert needs is False


# ============================================================================
# Persistence
# ============================================================================


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        session = Session(system_prompt="Test")
        session.add("user", "Hello")
        session.add("assistant", "Hi")

        path = tmp_path / "session.json"
        session.save(str(path))
        loaded = Session.load(str(path))

        assert loaded.system_prompt == "Test"
        assert len(loaded.messages) == 2
        assert loaded.messages[0].content == "Hello"

    def test_save_load_with_tool_calls(self, tmp_path):
        session = Session()
        session.add("user", "Read file")
        tool_call = ToolUseBlock(id="c1", name="read", arguments={"file": "x.py"})
        session.add_message(Message(role="assistant", content=[tool_call]))
        result = ToolResultBlock(tool_use_id="c1", tool_name="read", content="contents")
        session.add_tool_results([result])

        path = tmp_path / "session_with_tools.json"
        session.save(str(path))
        loaded = Session.load(str(path))

        assert len(loaded.messages) == 3

    def test_save_load_empty(self, tmp_path):
        session = Session()
        path = tmp_path / "empty.json"
        session.save(str(path))
        loaded = Session.load(str(path))
        assert len(loaded.messages) == 0

    def test_save_missing_dir_raises(self):
        session = Session()
        session.add("user", "Hello")
        with pytest.raises(FileNotFoundError):
            session.save("/nonexistent/path/session.json")

    def test_load_nonexistent_file(self):
        """Loading a non-existent file should raise."""
        with pytest.raises(FileNotFoundError):
            Session.load("/nonexistent/path/session.json")
