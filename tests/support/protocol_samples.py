"""One sample of every wire event, shared by the fixture generator and the tests.

The fixtures under ``tests/service/fixtures/`` are what the Swift package
decodes. Keeping the samples in one place means the Python codec, the JSON on
disk, and the Swift Codable schema are checked against the same objects.
"""

from __future__ import annotations

from typing import Any

from openagent.runtime.events import (
    AgentResult,
    BrowserFrame,
    ContextUsage,
    ContextCompactionCompleted,
    ContextCompactionFailed,
    ContextCompactionStarted,
    MessageCompleted,
    MessageDelta,
    MessageFailed,
    MessageStarted,
    ReasoningDelta,
    PermissionRequested,
    PermissionResolved,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
)
from openagent.service.protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    TERMINAL_EVENT_TYPES,
    ERROR_NAMES,
    ErrorCode,
    Method,
    event_to_dict,
)


def sample_events() -> list[RuntimeEvent]:
    """Every event type, with the awkward values: unicode, nesting, empty dicts."""
    return [
        RunStarted(run_id="run_sample"),
        MessageStarted(run_id="run_sample", message_id="msg_sample"),
        ReasoningDelta(
            run_id="run_sample",
            message_id="msg_sample",
            delta="Thinking: the user wants 中文 — 1,900 tokens of this arrive before the first answer character. ",
        ),
        MessageDelta(
            run_id="run_sample", message_id="msg_sample", delta="Writing — 中文 ✓\n"
        ),
        MessageCompleted(run_id="run_sample", message_id="msg_sample", output_text="Hello"),
        MessageFailed(run_id="run_sample", message_id="msg_sample", error="upstream 503"),
        ToolCallStarted(
            run_id="run_sample",
            message_id="msg_sample",
            tool_call_id="call_sample",
            tool_name="bash",
            arguments={"command": "ls -la", "nested": {"a": 1, "b": [1, 2]}},
        ),
        ToolCallCompleted(
            run_id="run_sample",
            message_id="msg_sample",
            tool_call_id="call_sample",
            tool_name="bash",
            result="total 0",
        ),
        ToolCallFailed(
            run_id="run_sample",
            message_id="msg_sample",
            tool_call_id="call_sample",
            tool_name="bash",
            error="exit code 2",
        ),
        ToolCallFailed(
            run_id="run_sample",
            message_id="msg_sample",
            tool_call_id="call_sample",
            tool_name="mystery",
            error="no tool name known",
        ),
        PermissionRequested(
            run_id="run_sample",
            request_id="req_sample",
            tool_name="bash",
            arguments={"command": "rm -rf build"},
            reason="exec: runs arbitrary commands",
        ),
        PermissionResolved(
            run_id="run_sample",
            request_id="req_sample",
            granted=True,
            scope="session",
            reason="approved by the user for this session",
        ),
        PermissionResolved(
            run_id="run_sample",
            request_id="req_sample",
            granted=False,
            scope="never",
            reason="no answer within 60s; refused by default",
        ),
        ContextCompactionStarted(run_id="run_sample", reason="context approaching limit"),
        ContextCompactionCompleted(run_id="run_sample", reason="context approaching limit"),
        ContextCompactionFailed(
            run_id="run_sample", reason="context approaching limit", error="summary model failed"
        ),
        RunCompleted(
            run_id="run_sample",
            final_message_id="msg_sample",
            result=AgentResult(
                run_id="run_sample",
                final_message_id="msg_sample",
                output_text="Done.",
                stop_reason="end_turn",
            ),
        ),
        RunCompleted(
            run_id="run_sample",
            final_message_id="msg_sample",
            result=AgentResult(
                run_id="run_sample",
                final_message_id="msg_sample",
                output_text="Turn limit reached.",
                stop_reason="max_turns",
            ),
        ),
        RunFailed(run_id="run_sample", error="provider exploded"),
        RunCancelled(run_id="run_sample", reason="user pressed stop"),
        ContextUsage(run_id="run_sample", used_tokens=12170, context_window=262144, compact_at=203161),
        ContextUsage(run_id="run_sample", used_tokens=900, context_window=None, compact_at=None),
        BrowserFrame(
            run_id="run_sample",
            data="/9j/4AAQSkZJRgABAQ==",
            url="https://example.com/search?q=中文",
            title="Example — 中文",
            width=1280,
            height=800,
        ),
    ]


def sample_frames() -> list[dict[str, Any]]:
    """The wire form: notification ``params`` objects, one per sample."""
    return [{"type": event.type, "params": event_to_dict(event)} for event in sample_events()]


def protocol_manifest() -> dict[str, Any]:
    """Everything a client generator needs that is not an event shape."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "max_frame_bytes": MAX_FRAME_BYTES,
        "methods": sorted(str(m.value) for m in Method),
        "terminal_event_types": sorted(TERMINAL_EVENT_TYPES),
        "error_codes": {name: code for code, name in ERROR_NAMES.items()},
        "events": sample_frames(),
    }


__all__ = ["protocol_manifest", "sample_events", "sample_frames"]
