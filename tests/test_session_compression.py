"""Tests for Session compression and persistence."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from openagent.core.session import Session
from openagent.core.types import (
    Message, TextBlock, ToolResultBlock, ToolUseBlock,
)


# ============================================================================
# Session basic operations
# ============================================================================


class TestSessionBasic:
    def test_add_text_message(self):
        session = Session()
        session.add("user", "hello")
        assert len(session) == 1
        assert session.messages[0].role == "user"
        assert session.messages[0].text == "hello"

    def test_add_tool_call_message(self):
        session = Session()
        msg = Message(
            role="assistant",
            content=[
                TextBlock(text="checking..."),
                ToolUseBlock(id="call_1", name="read", arguments={"file": "foo.py"}),
            ],
        )
        session.add_message(msg)
        assert len(session) == 1

    def test_add_tool_results(self):
        session = Session()
        session.add_tool_results([
            ToolResultBlock(tool_use_id="call_1", content="file contents"),
        ])
        assert len(session) == 1
        assert session.messages[0].role == "tool_result"

    def test_clear(self):
        session = Session()
        session.add("user", "hi")
        session.add("user", "bye")
        session.clear()
        assert len(session) == 0

    def test_to_list(self):
        session = Session(system_prompt="test")
        session.add("user", "hello")
        out = session.to_list()
        assert len(out) == 1
        assert out[0]["role"] == "user"
        assert out[0]["content"] == "hello"

    def test_save_and_load(self):
        session = Session(system_prompt="be nice")
        session.add("user", "hello")
        session.add_message(Message(
            role="assistant",
            content=[
                TextBlock(text="hi back"),
                ToolUseBlock(id="call_1", name="read", arguments={"file": "x.py"}),
            ],
        ))
        session.add_tool_results([
            ToolResultBlock(tool_use_id="call_1", content="contents", is_error=False),
        ])

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        try:
            session.save(path)
            loaded = Session.load(path)
            assert loaded.system_prompt == "be nice"
            assert len(loaded) == 3
            assert loaded.messages[0].text == "hello"
            assert loaded.messages[2].role == "tool_result"
        finally:
            Path(path).unlink(missing_ok=True)


# ============================================================================
# Token estimation
# ============================================================================


class TestTokenEstimation:
    def test_short_message(self):
        session = Session()
        tokens = session._estimate_tokens(Message(role="user", content="hi"))
        assert tokens >= 4  # minimum 4 tokens

    def test_long_message(self):
        session = Session()
        long_text = "word " * 100
        tokens = session._estimate_tokens(Message(role="user", content=long_text))
        assert tokens > 4

    def test_tool_call_overhead(self):
        session = Session()
        msg = Message(
            role="assistant",
            content=[
                ToolUseBlock(id="c1", name="read", arguments={"file": "x.py"}),
            ],
        )
        tokens = session._estimate_tokens(msg)
        assert tokens > 12  # overhead + argument size

    def test_total_tokens(self):
        session = Session()
        session.add("user", "a")
        session.add("user", "b")
        total = session._total_tokens()
        assert total >= 8  # at least 4 tokens per message


# ============================================================================
# Turn grouping
# ============================================================================


class TestTurnGrouping:
    def test_simple_turns(self):
        session = Session()
        session.add("user", "hi")
        session.add("assistant", "hello")
        session.add("user", "bye")
        turns = session._group_into_turns()
        assert len(turns) == 3

    def test_tool_call_paired_with_result(self):
        session = Session()
        session.add("user", "read file")
        session.add_message(Message(
            role="assistant",
            content=[
                TextBlock(text="reading..."),
                ToolUseBlock(id="c1", name="read", arguments={"file": "x.py"}),
            ],
        ))
        session.add_tool_results([
            ToolResultBlock(tool_use_id="c1", content="contents"),
        ])
        turns = session._group_into_turns()
        # user | assistant+tool_result paired = 2 turns
        assert len(turns) == 2
        assert len(turns[1]) == 2  # assistant + tool_result in same turn

    def test_unpaired_tool_call(self):
        session = Session()
        session.add("user", "do thing")
        session.add_message(Message(
            role="assistant",
            content=[
                ToolUseBlock(id="c1", name="bash", arguments={"cmd": "ls"}),
            ],
        ))
        # No tool result follows — orphan call
        turns = session._group_into_turns()
        assert len(turns) == 2


# ============================================================================
# Compression
# ============================================================================


class TestSessionCompression:
    def test_no_compression_under_threshold(self):
        session = Session(summary_threshold=20)
        for i in range(10):
            session.add("user", f"msg {i}")
            session.add("assistant", f"reply {i}")
        msg_count = len(session)
        # Should not compress — under threshold
        session._maybe_compress()
        assert len(session) == msg_count

    def test_compresses_when_exceeding_max_messages(self):
        session = Session(max_messages=10, summary_threshold=5)
        for i in range(8):
            session.add("user", f"msg {i}")
            session.add("assistant", f"reply {i}")
        # 16 messages, exceeds max_messages=10
        session._maybe_compress()
        # Should have fewer messages after compression
        assert len(session) < 16

    def test_compresses_when_exceeding_token_budget(self):
        session = Session(max_tokens=100, summary_threshold=2)
        # Each long message is ~50 tokens
        for i in range(5):
            session.add("user", f"long message {'word ' * 10} {i}")
            session.add("assistant", f"detailed reply {'word ' * 10} {i}")
        session._maybe_compress()
        assert session._total_tokens() < 500  # roughly reduced

    def test_compression_keeps_head_and_tail(self):
        session = Session(max_messages=10, summary_threshold=4)

        # Build enough turns to trigger compression
        session.add("user", "first context")
        session.add("assistant", "first reply")
        for i in range(4):
            session.add("user", f"middle {i}")
            session.add("assistant", f"middle reply {i}")
        session.add("user", "last important")
        session.add("assistant", "last reply")

        session._maybe_compress()

        # Check that "first context" and "last important" are preserved
        texts = []
        for msg in session._messages:
            if isinstance(msg.content, str):
                texts.append(msg.content)
            elif isinstance(msg.content, list):
                for b in msg.content:
                    if isinstance(b, TextBlock):
                        texts.append(b.text)

        combined = " ".join(texts).lower()
        assert "first" in combined
        assert "last" in combined

    def test_compression_inserts_summary(self):
        session = Session(max_messages=10, summary_threshold=4)

        for i in range(6):
            session.add("user", f"question {i}")
            session.add("assistant", f"answer {i}")

        session._maybe_compress()

        # Find the summary message
        has_summary = False
        for msg in session._messages:
            text = msg.text if isinstance(msg.content, str) else ""
            if "Summary" in text or "summary" in text:
                has_summary = True
                break
        assert has_summary, "Compression should insert a summary message"

    def test_create_summary_counts_operations(self):
        session = Session()
        messages = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="c1", name="write", arguments={"file": "a.py"}),
                ],
            ),
            Message(
                role="tool_result",
                content=[ToolResultBlock(tool_use_id="c1", content="wrote 100 bytes")],
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="c2", name="edit", arguments={"file": "b.py"}),
                ],
            ),
            Message(
                role="tool_result",
                content=[ToolResultBlock(tool_use_id="c2", content="1 replacement")],
            ),
        ]
        summary = session._create_summary(messages)
        assert "write" in summary
        assert "edit" in summary

    def test_create_summary_captures_errors(self):
        session = Session()
        messages = [
            Message(
                role="tool_result",
                content=[
                    ToolResultBlock(tool_use_id="c1", content="file not found", is_error=True),
                ],
            ),
        ]
        summary = session._create_summary(messages)
        assert "file not found" in summary
        assert "Error" in summary

    def test_create_summary_tracks_files(self):
        session = Session()
        messages = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="c1", name="write", arguments={"file": "src/main.py"}),
                ],
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="c2", name="edit", arguments={"file": "src/utils.py"}),
                ],
            ),
        ]
        summary = session._create_summary(messages)
        assert "src/main.py" in summary
        assert "src/utils.py" in summary

    def test_compression_never_splits_tool_pairs(self):
        """Compressed sessions should not have orphaned tool calls without results."""
        session = Session(max_messages=8, summary_threshold=3)

        # Build tool call + result pairs
        for i in range(5):
            session.add_message(Message(
                role="assistant",
                content=[
                    TextBlock(text=f"step {i}"),
                    ToolUseBlock(id=f"c{i}", name="bash", arguments={"cmd": f"echo {i}"}),
                ],
            ))
            session.add_tool_results([
                ToolResultBlock(tool_use_id=f"c{i}", content=f"output {i}"),
            ])

        session._maybe_compress()

        # Verify: every assistant message with tool calls should be followed
        # by a tool_result, or be part of the summary
        for i, msg in enumerate(session._messages):
            if msg.role == "assistant" and msg.has_tool_calls:
                # Either next message is tool_result, or this is the last message
                if i + 1 < len(session._messages):
                    next_msg = session._messages[i + 1]
                    assert next_msg.role == "tool_result", (
                        "Tool call should be paired with tool_result after compression"
                    )

    def test_no_compression_with_few_turns(self):
        """_compress_old_messages should be a no-op when there are <= 5 turns."""
        session = Session(max_messages=100, summary_threshold=2)
        session.add("user", "hi")
        session.add("assistant", "hello")
        session.add("user", "bye")
        session.add("assistant", "goodbye")
        # Only 4 turns, won't compress
        before = len(session)
        session._compress_old_messages()
        assert len(session) == before
