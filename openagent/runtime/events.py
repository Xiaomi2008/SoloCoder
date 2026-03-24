from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class AgentResult:
    run_id: str
    final_message_id: str
    output_text: str


@dataclass
class RunStarted:
    run_id: str
    type: Literal["run_started"] = "run_started"


@dataclass
class RunCompleted:
    run_id: str
    final_message_id: str
    result: AgentResult
    type: Literal["run_completed"] = "run_completed"


@dataclass
class RunFailed:
    run_id: str
    error: str
    type: Literal["run_failed"] = "run_failed"


@dataclass
class RunCancelled:
    run_id: str
    reason: str
    type: Literal["run_cancelled"] = "run_cancelled"


@dataclass
class MessageStarted:
    run_id: str
    message_id: str
    type: Literal["message_started"] = "message_started"


@dataclass
class MessageDelta:
    run_id: str
    message_id: str
    delta: str
    type: Literal["message_delta"] = "message_delta"


@dataclass
class MessageCompleted:
    run_id: str
    message_id: str
    output_text: str
    type: Literal["message_completed"] = "message_completed"


@dataclass
class MessageFailed:
    run_id: str
    message_id: str
    error: str
    type: Literal["message_failed"] = "message_failed"


@dataclass
class ToolCallStarted:
    run_id: str
    message_id: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    type: Literal["tool_call_started"] = "tool_call_started"


@dataclass
class ToolCallCompleted:
    run_id: str
    message_id: str
    tool_call_id: str
    tool_name: str
    result: Any
    type: Literal["tool_call_completed"] = "tool_call_completed"


@dataclass
class ToolCallFailed:
    run_id: str
    message_id: str
    tool_call_id: str
    error: str
    tool_name: str | None = None
    type: Literal["tool_call_failed"] = "tool_call_failed"


@dataclass
class ContextCompactionStarted:
    run_id: str
    reason: str
    type: Literal["context_compaction_started"] = "context_compaction_started"


@dataclass
class ContextCompactionCompleted:
    run_id: str
    reason: str
    type: Literal["context_compaction_completed"] = "context_compaction_completed"


@dataclass
class ContextCompactionFailed:
    run_id: str
    reason: str
    error: str
    type: Literal["context_compaction_failed"] = "context_compaction_failed"


RuntimeEvent = (
    RunStarted
    | RunCompleted
    | RunFailed
    | RunCancelled
    | MessageStarted
    | MessageDelta
    | MessageCompleted
    | MessageFailed
    | ToolCallStarted
    | ToolCallCompleted
    | ToolCallFailed
    | ContextCompactionStarted
    | ContextCompactionCompleted
    | ContextCompactionFailed
)


__all__ = [
    "AgentResult",
    "ContextCompactionCompleted",
    "ContextCompactionFailed",
    "ContextCompactionStarted",
    "MessageCompleted",
    "MessageDelta",
    "MessageFailed",
    "MessageStarted",
    "RunCancelled",
    "RunCompleted",
    "RunFailed",
    "RunStarted",
    "RuntimeEvent",
    "ToolCallCompleted",
    "ToolCallFailed",
    "ToolCallStarted",
]
