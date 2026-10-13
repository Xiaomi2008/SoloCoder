"""The wire contract, enforced from the Python side.

``tests/service/fixtures/`` is consumed by the Swift package. These tests keep
the fixtures honest: if an event gains a field and the fixtures are not
regenerated, this suite fails rather than letting the app decode a schema that
no longer exists.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import get_args

import pytest

from openagent.runtime.events import (
    AgentResult,
    MessageDelta,
    RunCompleted,
    RuntimeEvent,
)
from openagent.service.protocol import (
    ERROR_NAMES,
    EVENT_TYPES,
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    TERMINAL_EVENT_TYPES,
    ErrorCode,
    FrameReader,
    Method,
    ProtocolError,
    RpcError,
    as_request,
    as_response,
    decode,
    encode,
    error_response,
    event_from_dict,
    event_from_message,
    event_names,
    event_to_dict,
    event_to_line,
    event_to_notification,
    iter_events,
    notification,
    request,
    response,
)
from tests.support.protocol_samples import protocol_manifest, sample_events

FIXTURES = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------- completeness


def test_every_runtime_event_is_sendable():
    """The union in ``runtime.events`` and the wire registry cannot drift.

    This is the test that makes adding an event a two-file change instead of a
    silent gap the app discovers at runtime.
    """
    union = set(get_args(RuntimeEvent))
    registered = set(EVENT_TYPES.values())
    assert union - registered == set()
    assert registered - union == set()


def test_every_wire_event_carries_type_and_run_id():
    for name, cls in EVENT_TYPES.items():
        fields = {f.name for f in dataclasses.fields(cls)}
        assert "type" in fields, name
        assert "run_id" in fields, name


def test_event_names_are_the_type_literals():
    for name, cls in EVENT_TYPES.items():
        sample = cls(**{f.name: "x" for f in dataclasses.fields(cls) if f.name != "type"})
        assert sample.type == name


def test_every_error_code_has_a_stable_name():
    """Clients switch on the code; the name is what they log and document."""
    codes = {
        value
        for name, value in vars(ErrorCode).items()
        if not name.startswith("_") and isinstance(value, int)
    }
    assert set(ERROR_NAMES) == codes


def test_manifest_lists_every_method_and_event():
    manifest = protocol_manifest()
    assert manifest["protocol_version"] == PROTOCOL_VERSION
    assert set(manifest["methods"]) == {str(m.value) for m in Method}
    assert set(manifest["terminal_event_types"]) == TERMINAL_EVENT_TYPES
    assert {e["type"] for e in manifest["events"]} == set(EVENT_TYPES)
    assert manifest["error_codes"]["working_dir_denied"] == ErrorCode.WORKING_DIR_DENIED


# ------------------------------------------------------------------ codec


def test_every_sample_event_round_trips():
    for event in sample_events():
        data = event_to_dict(event)
        assert data["type"] == event.type
        assert event_from_dict(data) == event


def test_nested_agent_result_round_trips():
    event = next(e for e in sample_events() if isinstance(e, RunCompleted))
    data = event_to_dict(event)
    assert isinstance(data["result"], dict)
    assert data["result"]["stop_reason"] == "end_turn"

    rebuilt = event_from_dict(data)
    assert isinstance(rebuilt.result, AgentResult)
    assert rebuilt.result.output_text == "Done."
    assert rebuilt.result.stop_reason == "end_turn"


def test_unknown_fields_are_ignored_so_servers_can_add_them():
    data = event_to_dict(MessageDelta(run_id="r", message_id="m", delta="hi"))
    data["model_used"] = "qwen3-coder-32b"
    data["server_version"] = "9.9.9"
    assert event_from_dict(data) == MessageDelta(run_id="r", message_id="m", delta="hi")


def test_missing_required_field_raises_with_the_field_named():
    with pytest.raises(ProtocolError) as exc:
        event_from_dict({"type": "message_delta", "run_id": "r"})
    assert exc.value.code == ErrorCode.INVALID_PARAMS
    assert "delta" in exc.value.message


def test_unknown_event_type_raises():
    with pytest.raises(ProtocolError, match="unknown event type"):
        event_from_dict({"type": "quantum_leap", "run_id": "r"})


def test_event_encodes_as_a_notification_frame():
    event = MessageDelta(run_id="r", message_id="m", delta="hi")
    frame = decode(encode(event_to_notification(event).to_dict()))

    assert frame["method"] == "event"
    assert "id" not in frame
    assert frame["params"]["delta"] == "hi"
    assert event_from_message(frame) == event


def test_event_to_line_is_the_same_frame():
    event = MessageDelta(run_id="r", message_id="m", delta="hi")
    assert event_to_line(event).endswith(b"\n")
    assert event_from_message(decode(event_to_line(event))) == event


def test_iter_events_skips_responses():
    frames = [
        event_to_line(MessageDelta(run_id="r", message_id="m", delta="a")),
        encode(response("c1", {"ok": True}).to_dict()),
        event_to_line(MessageDelta(run_id="r", message_id="m", delta="b")),
    ]
    events = list(iter_events(frames))
    assert [e.delta for e in events] == ["a", "b"]


# ----------------------------------------------------------------- framing


def test_frames_survive_being_split_and_batched():
    a = encode(request("ping", {}, id="c1").to_dict())
    b = encode(response("c1", {"pong": True}).to_dict())
    blob = a + b
    reader = FrameReader()

    assert reader.feed(blob[:3]) == []
    frames = reader.feed(blob[3:])

    assert len(frames) == 2
    assert as_request(frames[0]).method == "ping"
    assert as_response(frames[1]).result == {"pong": True}


def test_blank_lines_are_ignored():
    reader = FrameReader()
    frames = reader.feed(b"\n\n" + encode({"jsonrpc": "2.0", "id": "c1", "result": {}}) + b"\n")
    assert len(frames) == 1


def test_oversized_frame_is_refused_not_buffered():
    reader = FrameReader(max_bytes=64)
    with pytest.raises(ProtocolError) as exc:
        reader.feed(b"x" * 100)
    assert exc.value.code == ErrorCode.INVALID_REQUEST
    assert reader.buffered == 0


def test_oversized_frame_on_encode():
    with pytest.raises(ProtocolError):
        encode({"blob": "x" * (MAX_FRAME_BYTES + 1)})


def test_max_frame_is_a_documented_number():
    assert MAX_FRAME_BYTES == 8 * 1024 * 1024


def test_decode_reports_parse_error_code():
    with pytest.raises(ProtocolError) as exc:
        decode(b"{not json")
    assert exc.value.code == ErrorCode.PARSE_ERROR


# -------------------------------------------------------------- envelopes


def test_request_notification_and_response_shapes():
    req = request("run.start", {"prompt": "hi"}, id="c1")
    assert req.to_dict() == {"jsonrpc": "2.0", "method": "run.start", "params": {"prompt": "hi"}, "id": "c1"}
    assert not req.is_notification

    note = notification("event", {"type": "run_started"})
    assert note.is_notification
    assert "id" not in note.to_dict()

    ok = response("c1", {"run_id": "run_1"})
    assert ok.ok and ok.to_dict()["result"] == {"run_id": "run_1"}

    err = error_response("c1", ErrorCode.WORKING_DIR_DENIED, "refused", {"path": "/"})
    assert not err.ok
    assert err.to_dict()["error"] == {
        "code": -33005,
        "message": "refused",
        "data": {"path": "/"},
    }


def test_rpc_error_is_raisable_and_named():
    with pytest.raises(RpcError) as exc:
        raise RpcError(ErrorCode.NOT_AUTHENTICATED, "run auth login")
    assert exc.value.name == "not_authenticated"
    assert exc.value.to_dict()["code"] == ErrorCode.NOT_AUTHENTICATED


def test_rpc_error_survives_contextlib_and_asyncio_timeout():
    """Python 3.14 assigns ``__traceback__`` on the way out of a context manager.

    A frozen exception raises ``FrozenInstanceError`` *from inside the handler*,
    which is the worst place to find it: a client that wraps a call in
    ``asyncio.timeout`` — which the service client does — would get a crash
    where it expected the error it was catching.
    """
    import asyncio
    import contextlib

    with contextlib.suppress(RpcError):
        raise RpcError(ErrorCode.METHOD_NOT_FOUND, "gone")

    async def call():
        async with asyncio.timeout(5):
            raise RpcError(ErrorCode.METHOD_NOT_FOUND, "gone")

    with pytest.raises(RpcError):
        asyncio.run(call())


def test_as_request_rejects_non_object_params():
    with pytest.raises(ProtocolError, match="params"):
        as_request({"method": "ping", "params": ["nope"], "id": "c1"})


def test_as_request_returns_none_for_responses():
    assert as_request({"jsonrpc": "2.0", "id": "c1", "result": {}}) is None
    assert as_response({"jsonrpc": "2.0", "id": "c1", "result": {}}).id == "c1"


def test_as_response_requires_an_id():
    with pytest.raises(ProtocolError, match="response without an id"):
        as_response({"jsonrpc": "2.0", "result": {}})


def test_error_response_for_a_notification_has_an_empty_id():
    assert error_response(None, ErrorCode.METHOD_NOT_FOUND, "nope").id == ""


# ---------------------------------------------------------------- fixtures


def test_fixtures_on_disk_match_the_codec():
    """The Swift package decodes these files; they must not rot silently."""
    on_disk = json.loads((FIXTURES / "events.json").read_text(encoding="utf-8"))
    manifest = json.loads((FIXTURES / "protocol.json").read_text(encoding="utf-8"))

    assert on_disk == {"events": protocol_manifest()["events"]}
    assert manifest == protocol_manifest()

    for frame in on_disk["events"]:
        assert event_from_dict(frame["params"]) is not None
