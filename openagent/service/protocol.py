"""The wire contract between the engine and its clients.

Spec: ``dev_docs/agent-service-protocol.md``. This module is that document
written in Python: envelope types, NDJSON framing, method names, error codes,
and the event codec. It knows nothing about sockets, agents, or models — the
server and the client both import it, so they cannot drift from each other, and
the Swift package replays the same JSON fixtures.

Three rules this file enforces:

- **Events serialize 1:1.** ``event_to_dict`` is ``dataclasses.asdict`` plus the
  ``type`` literal, so a runtime event on the wire is the runtime event in the
  process. Field names are snake_case; Swift decodes with
  ``convertFromSnakeCase``.
- **Unknown fields are ignored, missing required fields are not.** That is what
  lets a new server talk to an old client.
- **Every frame is bounded.** A client that streams an unbounded line is a
  memory exhaustion bug in whoever wrote it; the server refuses it instead.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, get_type_hints

from openagent.runtime.events import (
    AgentResult,
    ContextCompactionCompleted,
    ContextCompactionFailed,
    ContextCompactionStarted,
    MessageCompleted,
    MessageDelta,
    MessageFailed,
    MessageStarted,
    PermissionRequested,
    PermissionResolved,
    ReasoningDelta,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
)

PROTOCOL_VERSION = 1
MIN_PROTOCOL_VERSION = 1

#: One NDJSON line. A larger frame is refused rather than buffered.
MAX_FRAME_BYTES = 8 * 1024 * 1024

#: Notification method carrying a runtime event in ``params``.
EVENT_METHOD = "event"

SERVER_NAME = "openagent"


class Method(StrEnum):
    """Client→server methods. Dotted, lowercase, no camelCase."""

    HELLO = "hello"
    PING = "ping"
    RUN_START = "run.start"
    RUN_CANCEL = "run.cancel"
    PERMISSION_RESPOND = "permission.respond"
    MODEL_LIST = "model.list"
    MODEL_PROBE = "model.probe"
    MODEL_SELECT = "model.select"
    AUTH_STATUS = "auth.status"
    AUTH_LOGOUT = "auth.logout"
    AUTH_LOGIN = "auth.login"
    TOOL_LIST = "tool.list"
    SESSION_LIST = "session.list"
    SESSION_LOAD = "session.load"
    SESSION_SAVE = "session.save"
    CONFIG_GET = "config.get"
    CONFIG_SET = "config.set"


class ErrorCode:
    """JSON-RPC reserved codes, then the app's own.

    Names are stable: the client switches on ``code``, not on ``message``.
    """

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    NOT_IMPLEMENTED = -32604

    PROVIDER_ERROR = -33000
    MODEL_UNAVAILABLE = -33001
    NOT_AUTHENTICATED = -33002
    PERMISSION_DENIED = -33003
    CANCELLED = -33004
    WORKING_DIR_DENIED = -33005
    SESSION_NOT_FOUND = -33006
    RUN_NOT_FOUND = -33007
    PROTOCOL_MISMATCH = -33008
    DEPENDENCY_MISSING = -33009


ERROR_NAMES: dict[int, str] = {
    ErrorCode.PARSE_ERROR: "parse_error",
    ErrorCode.INVALID_REQUEST: "invalid_request",
    ErrorCode.METHOD_NOT_FOUND: "method_not_found",
    ErrorCode.INVALID_PARAMS: "invalid_params",
    ErrorCode.INTERNAL_ERROR: "internal_error",
    ErrorCode.NOT_IMPLEMENTED: "not_implemented",
    ErrorCode.PROVIDER_ERROR: "provider_error",
    ErrorCode.MODEL_UNAVAILABLE: "model_unavailable",
    ErrorCode.NOT_AUTHENTICATED: "not_authenticated",
    ErrorCode.PERMISSION_DENIED: "permission_denied",
    ErrorCode.CANCELLED: "cancelled",
    ErrorCode.WORKING_DIR_DENIED: "working_dir_denied",
    ErrorCode.SESSION_NOT_FOUND: "session_not_found",
    ErrorCode.RUN_NOT_FOUND: "run_not_found",
    ErrorCode.PROTOCOL_MISMATCH: "protocol_mismatch",
    ErrorCode.DEPENDENCY_MISSING: "dependency_missing",
}


@dataclass
class RpcError(Exception):
    """An RPC error object. It is an Exception so a handler can ``raise`` it and
    the dispatcher turns it into the error response; ``code`` is the contract.

    Not frozen, deliberately: the interpreter assigns ``__traceback__`` to every
    raised exception, and Python 3.14's ``contextlib`` does it explicitly on the
    way out of ``asyncio.timeout`` / ``suppress``. A frozen exception raises
    ``FrozenInstanceError`` from inside the exception handler.
    """

    code: int
    message: str
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            out["data"] = self.data
        return out

    @property
    def name(self) -> str:
        return ERROR_NAMES.get(self.code, "unknown_error")


class ProtocolError(Exception):
    """Framing/envelope violation. Carries the JSON-RPC code to report."""

    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class FrameTooLarge(ProtocolError):
    def __init__(self, size: int, limit: int) -> None:
        super().__init__(
            ErrorCode.INVALID_REQUEST,
            f"frame of {size} bytes exceeds the {limit} byte limit",
            {"size": size, "limit": limit},
        )


@dataclass(frozen=True)
class RpcRequest:
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str | None = None  # None => notification: no response is sent

    @property
    def is_notification(self) -> bool:
        return self.id is None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"jsonrpc": "2.0", "method": self.method}
        if self.params:
            out["params"] = self.params
        if self.id is not None:
            out["id"] = self.id
        return out


@dataclass(frozen=True)
class RpcResponse:
    id: str
    result: dict[str, Any] | None = None
    error: RpcError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"jsonrpc": "2.0", "id": self.id}
        if self.error is not None:
            out["error"] = self.error.to_dict()
        else:
            out["result"] = self.result if self.result is not None else {}
        return out


# --------------------------------------------------------------- constructors

def request(method: str, params: dict[str, Any] | None = None, id: str | None = None) -> RpcRequest:
    return RpcRequest(method=method, params=params or {}, id=id)


def notification(method: str, params: dict[str, Any] | None = None) -> RpcRequest:
    return RpcRequest(method=method, params=params or {}, id=None)


def response(id: str, result: dict[str, Any] | None = None) -> RpcResponse:
    return RpcResponse(id=id, result=result)


def error_response(id: str | None, code: int, message: str, data: dict[str, Any] | None = None) -> RpcResponse:
    return RpcResponse(id=id if id is not None else "", error=RpcError(code, message, data))


# ------------------------------------------------------------------- framing

def encode(obj: Any) -> bytes:
    """One JSON value as a newline-terminated frame."""
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(line) > MAX_FRAME_BYTES:
        raise FrameTooLarge(len(line), MAX_FRAME_BYTES)
    return line + b"\n"


def decode(line: bytes | str) -> Any:
    text = line.decode("utf-8") if isinstance(line, bytes) else line
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(ErrorCode.PARSE_ERROR, f"invalid JSON: {exc}") from exc


class FrameReader:
    """Incremental NDJSON parser: feed bytes, get whole frames out.

    A socket read ends at an arbitrary byte, so a frame can be split across
    reads and several can arrive in one. Keeping that in one place is the
    difference between a transport bug and a protocol bug.
    """

    def __init__(self, max_bytes: int = MAX_FRAME_BYTES) -> None:
        self._buffer = bytearray()
        self.max_bytes = max_bytes

    def feed(self, data: bytes) -> list[Any]:
        """Append bytes and return every complete decoded frame they completed."""
        self._buffer.extend(data)
        frames: list[Any] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                if len(self._buffer) > self.max_bytes:
                    size = len(self._buffer)
                    self._buffer.clear()
                    raise FrameTooLarge(size, self.max_bytes)
                return frames
            line = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            if not line.strip():
                continue
            if len(line) > self.max_bytes:
                raise FrameTooLarge(len(line), self.max_bytes)
            frames.append(decode(line))
        return frames

    @property
    def buffered(self) -> int:
        return len(self._buffer)


# -------------------------------------------------------------- message shape

def as_request(message: Any) -> RpcRequest | None:
    """Parse a client→server request/notification, or None if it is not one."""
    if not isinstance(message, dict) or "method" not in message:
        return None
    params = message.get("params")
    if params is not None and not isinstance(params, dict):
        raise ProtocolError(
            ErrorCode.INVALID_PARAMS, "`params` must be an object", {"method": message.get("method")}
        )
    return RpcRequest(
        method=str(message["method"]),
        params=dict(params or {}),
        id=message.get("id") if message.get("id") is not None else None,
    )


def as_response(message: Any) -> RpcResponse | None:
    if not isinstance(message, dict) or "method" in message:
        return None
    if "id" not in message:
        raise ProtocolError(ErrorCode.INVALID_REQUEST, "response without an id")
    raw_error = message.get("error")
    error = None
    if isinstance(raw_error, dict):
        error = RpcError(
            code=int(raw_error.get("code", ErrorCode.INTERNAL_ERROR)),
            message=str(raw_error.get("message", "")),
            data=raw_error.get("data") if isinstance(raw_error.get("data"), dict) else None,
        )
    result = message.get("result")
    return RpcResponse(
        id=str(message["id"]),
        result=result if isinstance(result, dict) else None,
        error=error,
    )


# -------------------------------------------------------------- event codec

#: ``type`` literal -> dataclass. The single source of truth for what the wire
#: can carry; the handshake advertises it and the Swift schema mirrors it.
EVENT_TYPES: dict[str, type] = {
    "run_started": RunStarted,
    "run_completed": RunCompleted,
    "run_failed": RunFailed,
    "run_cancelled": RunCancelled,
    "message_started": MessageStarted,
    "message_delta": MessageDelta,
    "reasoning_delta": ReasoningDelta,
    "message_completed": MessageCompleted,
    "message_failed": MessageFailed,
    "tool_call_started": ToolCallStarted,
    "tool_call_completed": ToolCallCompleted,
    "tool_call_failed": ToolCallFailed,
    "permission_requested": PermissionRequested,
    "permission_resolved": PermissionResolved,
    "context_compaction_started": ContextCompactionStarted,
    "context_compaction_completed": ContextCompactionCompleted,
    "context_compaction_failed": ContextCompactionFailed,
}

TERMINAL_EVENT_TYPES = frozenset({"run_completed", "run_failed", "run_cancelled"})


def event_names() -> list[str]:
    return sorted(EVENT_TYPES)


def event_to_dict(event: RuntimeEvent | AgentResult) -> dict[str, Any]:
    """A runtime event as a plain dict: fields verbatim, plus ``type``.

    ``AgentResult`` has no ``type`` of its own (it is a payload, not an event);
    it is accepted here so ``run_completed``'s nested ``result`` serializes the
    same way.
    """
    data = dataclasses.asdict(event)  # type: ignore[type-arg]
    data.setdefault("type", getattr(event, "type", type(event).__name__.lower()))
    return data


def event_to_notification(event: RuntimeEvent) -> RpcRequest:
    return notification(EVENT_METHOD, event_to_dict(event))


def event_to_line(event: RuntimeEvent) -> bytes:
    return encode(event_to_notification(event).to_dict())


def _dataclass_fields(cls: type) -> dict[str, Any]:
    return get_type_hints(cls)


def event_from_dict(data: dict[str, Any]) -> RuntimeEvent:
    """Rebuild a runtime event from the wire.

    Unknown ``type`` raises — a client that cannot name an event cannot render
    it, and silently dropping it is worse than a loud decode error in tests.
    Unknown *fields* are ignored, so a server may add fields without a
    version bump. Missing required fields raise.
    """
    event_type = data.get("type")
    cls = EVENT_TYPES.get(str(event_type))
    if cls is None:
        raise ProtocolError(
            ErrorCode.INVALID_PARAMS, f"unknown event type {event_type!r}", {"type": event_type}
        )

    hints = _dataclass_fields(cls)
    kwargs: dict[str, Any] = {}
    missing: list[str] = []
    for f in dataclasses.fields(cls):
        if f.name == "type":
            continue
        if f.name not in data:
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
                missing.append(f.name)
            continue
        value = data[f.name]
        hint = hints.get(f.name)
        if dataclasses.is_dataclass(hint) and isinstance(value, dict):
            value = _from_dict(hint, value)  # type: ignore[arg-type]
        kwargs[f.name] = value

    if missing:
        raise ProtocolError(
            ErrorCode.INVALID_PARAMS,
            f"event {event_type!r} is missing required fields: {', '.join(missing)}",
            {"type": str(event_type), "missing": missing},
        )
    return cls(**kwargs)  # type: ignore[call-arg]


def _from_dict(cls: type, data: dict[str, Any]) -> Any:
    hints = _dataclass_fields(cls)
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name in data:
            hint = hints.get(f.name)
            value = data[f.name]
            if dataclasses.is_dataclass(hint) and isinstance(value, dict):
                value = _from_dict(hint, value)  # type: ignore[arg-type]
            kwargs[f.name] = value
    return cls(**kwargs)


def event_from_message(message: Any) -> RuntimeEvent | None:
    """Parse an incoming frame as an event notification, else None."""
    req = as_request(message)
    if req is None or req.method != EVENT_METHOD:
        return None
    return event_from_dict(req.params)


def iter_events(lines: Iterator[bytes] | list[bytes]) -> Iterator[RuntimeEvent]:
    """Decode frames (skipping responses) into events. Used by clients and tests."""
    reader = FrameReader()
    for line in lines:
        for frame in reader.feed(line):
            event = event_from_message(frame)
            if event is not None:
                yield event


__all__ = [
    "ERROR_NAMES",
    "EVENT_METHOD",
    "EVENT_TYPES",
    "ErrorCode",
    "FrameReader",
    "FrameTooLarge",
    "MAX_FRAME_BYTES",
    "MIN_PROTOCOL_VERSION",
    "Method",
    "PROTOCOL_VERSION",
    "ProtocolError",
    "RpcError",
    "RpcRequest",
    "RpcResponse",
    "SERVER_NAME",
    "TERMINAL_EVENT_TYPES",
    "as_request",
    "as_response",
    "decode",
    "encode",
    "error_response",
    "event_from_dict",
    "event_from_message",
    "event_names",
    "event_to_dict",
    "event_to_line",
    "event_to_notification",
    "iter_events",
    "notification",
    "request",
    "response",
]
