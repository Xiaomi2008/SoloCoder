"""The agent service: a Unix-socket JSON-RPC boundary around the engine.

Spec in ``dev_docs/agent-service-protocol.md``. Import the protocol without the
server (``openagent.service.protocol``) to write a client, a test, or a schema
generator — the protocol module has no engine dependencies beyond event types.
"""

from __future__ import annotations

from openagent.service.protocol import (
    EVENT_METHOD,
    EVENT_TYPES,
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    ErrorCode,
    FrameReader,
    Method,
    ProtocolError,
    RpcError,
    RpcRequest,
    RpcResponse,
    event_from_dict,
    event_names,
    event_to_dict,
)

__all__ = [
    "EVENT_METHOD",
    "EVENT_TYPES",
    "MAX_FRAME_BYTES",
    "PROTOCOL_VERSION",
    "ErrorCode",
    "FrameReader",
    "Method",
    "ProtocolError",
    "RpcError",
    "RpcRequest",
    "RpcResponse",
    "event_from_dict",
    "event_names",
    "event_to_dict",
]
