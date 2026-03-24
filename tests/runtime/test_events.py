from __future__ import annotations

from openagent import (
    AgentResult,
    ContextCompactionCompleted,
    ContextCompactionFailed,
    ContextCompactionStarted,
    MessageCompleted,
    MessageDelta,
    MessageFailed,
    MessageStarted,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
)
from openagent.runtime import (
    AgentResult as RuntimeAgentResult,
    ContextCompactionCompleted as RuntimeContextCompactionCompleted,
    ContextCompactionFailed as RuntimeContextCompactionFailed,
    ContextCompactionStarted as RuntimeContextCompactionStarted,
    MessageCompleted as RuntimeMessageCompleted,
    MessageDelta as RuntimeMessageDelta,
    MessageFailed as RuntimeMessageFailed,
    MessageStarted as RuntimeMessageStarted,
    RunCancelled as RuntimeRunCancelled,
    RunCompleted as RuntimeRunCompleted,
    RunFailed as RuntimeRunFailed,
    RunStarted as RuntimeRunStarted,
    ToolCallCompleted as RuntimeToolCallCompleted,
    ToolCallFailed as RuntimeToolCallFailed,
    ToolCallStarted as RuntimeToolCallStarted,
)
from openagent.runtime.events import (
    AgentResult as EventsAgentResult,
    ContextCompactionCompleted as EventsContextCompactionCompleted,
    ContextCompactionFailed as EventsContextCompactionFailed,
    ContextCompactionStarted as EventsContextCompactionStarted,
    MessageCompleted as EventsMessageCompleted,
    MessageDelta as EventsMessageDelta,
    MessageFailed as EventsMessageFailed,
    MessageStarted as EventsMessageStarted,
    RunCancelled as EventsRunCancelled,
    RunCompleted as EventsRunCompleted,
    RunFailed as EventsRunFailed,
    RunStarted as EventsRunStarted,
    ToolCallCompleted as EventsToolCallCompleted,
    ToolCallFailed as EventsToolCallFailed,
    ToolCallStarted as EventsToolCallStarted,
)


def test_runtime_exports_canonical_event_types():
    assert RuntimeRunStarted is EventsRunStarted is RunStarted
    assert RuntimeRunCompleted is EventsRunCompleted is RunCompleted
    assert RuntimeRunFailed is EventsRunFailed is RunFailed
    assert RuntimeRunCancelled is EventsRunCancelled is RunCancelled
    assert RuntimeMessageStarted is EventsMessageStarted is MessageStarted
    assert RuntimeMessageDelta is EventsMessageDelta is MessageDelta
    assert RuntimeMessageCompleted is EventsMessageCompleted is MessageCompleted
    assert RuntimeMessageFailed is EventsMessageFailed is MessageFailed
    assert RuntimeToolCallStarted is EventsToolCallStarted is ToolCallStarted
    assert RuntimeToolCallCompleted is EventsToolCallCompleted is ToolCallCompleted
    assert RuntimeToolCallFailed is EventsToolCallFailed is ToolCallFailed
    assert (
        RuntimeContextCompactionStarted
        is EventsContextCompactionStarted
        is ContextCompactionStarted
    )
    assert (
        RuntimeContextCompactionCompleted
        is EventsContextCompactionCompleted
        is ContextCompactionCompleted
    )
    assert (
        RuntimeContextCompactionFailed
        is EventsContextCompactionFailed
        is ContextCompactionFailed
    )
    assert RuntimeAgentResult is EventsAgentResult is AgentResult


def test_agent_result_exposes_public_result_fields():
    result = AgentResult(
        run_id="run_123",
        final_message_id="msg_456",
        output_text="Done.",
    )

    assert result.run_id == "run_123"
    assert result.final_message_id == "msg_456"
    assert result.output_text == "Done."


def test_run_events_expose_bootstrap_contract_fields():
    started = RunStarted(run_id="run_123")
    completed = RunCompleted(
        run_id="run_123",
        final_message_id="msg_456",
        result=AgentResult(
            run_id="run_123",
            final_message_id="msg_456",
            output_text="Done.",
        ),
    )
    failed = RunFailed(run_id="run_123", error="provider timeout")
    cancelled = RunCancelled(run_id="run_123", reason="user_cancelled")

    assert started.type == "run_started"
    assert started.run_id == "run_123"
    assert completed.type == "run_completed"
    assert completed.run_id == "run_123"
    assert completed.final_message_id == "msg_456"
    assert completed.result.final_message_id == "msg_456"
    assert failed.type == "run_failed"
    assert failed.run_id == "run_123"
    assert failed.error == "provider timeout"
    assert cancelled.type == "run_cancelled"
    assert cancelled.run_id == "run_123"
    assert cancelled.reason == "user_cancelled"


def test_message_events_expose_bootstrap_contract_fields():
    started = MessageStarted(run_id="run_123", message_id="msg_456")
    delta = MessageDelta(run_id="run_123", message_id="msg_456", delta="Hel")
    completed = MessageCompleted(
        run_id="run_123",
        message_id="msg_456",
        output_text="Hello",
    )
    failed = MessageFailed(
        run_id="run_123",
        message_id="msg_456",
        error="stream interrupted",
    )

    assert started.type == "message_started"
    assert started.run_id == "run_123"
    assert started.message_id == "msg_456"
    assert delta.type == "message_delta"
    assert delta.run_id == "run_123"
    assert delta.message_id == "msg_456"
    assert delta.delta == "Hel"
    assert completed.type == "message_completed"
    assert completed.run_id == "run_123"
    assert completed.message_id == "msg_456"
    assert completed.output_text == "Hello"
    assert failed.type == "message_failed"
    assert failed.run_id == "run_123"
    assert failed.message_id == "msg_456"
    assert failed.error == "stream interrupted"


def test_tool_call_events_expose_bootstrap_contract_fields():
    started = ToolCallStarted(
        run_id="run_123",
        message_id="msg_456",
        tool_call_id="tool_789",
        tool_name="search",
        arguments={"q": "weather"},
    )
    completed = ToolCallCompleted(
        run_id="run_123",
        message_id="msg_456",
        tool_call_id="tool_789",
        tool_name="search",
        result="ok",
    )
    failed = ToolCallFailed(
        run_id="run_123",
        message_id="msg_456",
        tool_call_id="tool_789",
        tool_name="search",
        error="tool crashed",
    )

    assert started.type == "tool_call_started"
    assert started.run_id == "run_123"
    assert started.message_id == "msg_456"
    assert started.tool_call_id == "tool_789"
    assert started.tool_name == "search"
    assert started.arguments == {"q": "weather"}
    assert completed.type == "tool_call_completed"
    assert completed.run_id == "run_123"
    assert completed.message_id == "msg_456"
    assert completed.tool_call_id == "tool_789"
    assert completed.tool_name == "search"
    assert completed.result == "ok"
    assert failed.type == "tool_call_failed"
    assert failed.run_id == "run_123"
    assert failed.message_id == "msg_456"
    assert failed.tool_call_id == "tool_789"
    assert failed.tool_name == "search"
    assert failed.error == "tool crashed"


def test_context_compaction_events_expose_bootstrap_contract_fields():
    started = ContextCompactionStarted(run_id="run_123", reason="context_window")
    completed = ContextCompactionCompleted(run_id="run_123", reason="context_window")
    failed = ContextCompactionFailed(
        run_id="run_123",
        reason="context_window",
        error="compaction failed",
    )

    assert started.type == "context_compaction_started"
    assert started.run_id == "run_123"
    assert started.reason == "context_window"
    assert completed.type == "context_compaction_completed"
    assert completed.run_id == "run_123"
    assert completed.reason == "context_window"
    assert failed.type == "context_compaction_failed"
    assert failed.run_id == "run_123"
    assert failed.reason == "context_window"
    assert failed.error == "compaction failed"


def test_tool_call_failed_does_not_require_tool_name_in_bootstrap_contract():
    failed = ToolCallFailed(
        run_id="run_123",
        message_id="msg_456",
        tool_call_id="tool_789",
        error="tool crashed",
    )

    assert failed.type == "tool_call_failed"
    assert failed.run_id == "run_123"
    assert failed.message_id == "msg_456"
    assert failed.tool_call_id == "tool_789"
    assert failed.error == "tool crashed"
    assert getattr(failed, "tool_name", None) is None
