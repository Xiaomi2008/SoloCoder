"""The Unix-socket service, end to end.

These are contract tests, not unit tests: they speak to a real server on a real
socket with the real client, because what goes wrong in a sidecar is framing,
ordering, and lifecycle — the things a mocked-stream unit test cannot see. The
SwiftUI ``AgentClient`` is written against ``dev_docs/agent-service-protocol.md``;
these tests execute the same document.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

from openagent.core.session import Session
from openagent.core.tool import ToolContext, tool
from openagent.provider.auth.tokens import AuthRequired
from openagent.provider.catalog import ModelCatalog
from openagent.service.client import ServiceClient
from openagent.service.protocol import (
    PROTOCOL_VERSION,
    ErrorCode,
    Method,
    RpcError,
    encode,
    request,
)
from openagent.service.server import AgentService, ServiceSettings
from tests.support.streaming_provider import (
    HangingProvider,
    ScriptedStreamProvider,
    error_turn,
    text_turn,
    tool_turn,
)

TIMEOUT = 10.0


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep the suite out of the real ~/.openagent.

    Every run saves a session, so without this the contract tests litter the
    developer's own session store with fixtures named after test cases.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))


@tool
def echo(text: str) -> str:
    """Echo the text back."""
    return f"echo:{text}"


@tool
def peek(text: str) -> str:
    """A tool the risk classifier has never seen, so it needs approval."""
    return f"peeked:{text}"


@tool
def recall(text: str) -> str:
    """A tool classified read-only, so 'ask' mode never prompts for it."""
    return f"recalled:{text}"


class Harness:
    """Services and clients that are guaranteed to be shut down.

    Every test here owns a socket and a client. Leaking one leaves a server
    task running into the next test, and the failure it produces is a timeout
    in an unrelated file.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        # AF_UNIX paths are capped at 104 bytes on macOS and pytest's tmp_path
        # is ~100 already, so sockets get a short folder of their own.
        self.sock_dir = Path(tempfile.mkdtemp(prefix="oa"))
        self.providers: list[Any] = []
        self._services: list[AgentService] = []
        self._clients: list[ServiceClient] = []

    async def serve(self, **overrides: Any) -> AgentService:
        index = len(self._services)
        settings = ServiceSettings(
            socket_path=self.sock_dir / f"a{index}.sock",
            tools=[echo, peek, recall],
            provider_factory=self.script(
                [tool_turn("peek", {"text": "x"}), text_turn("done")]
            ),
            permission_timeout=0.3,
            # The suite must not probe localhost for models it does not use.
            discover_on_start=False,
        )
        for key, value in overrides.items():
            setattr(settings, key, value)
        service = AgentService(settings)
        await service.start()
        self._services.append(service)
        return service

    def script(self, turns: list[list[Any]]):
        """A provider factory that replays ``turns`` and remembers the provider."""

        def build(ref):
            provider = ScriptedStreamProvider(turns)
            self.providers.append(provider)
            return provider

        return build

    async def connect(self, service: AgentService, **kwargs: Any) -> ServiceClient:
        client = await ServiceClient.connect(
            service.settings.socket_path, client="pytest", **kwargs
        )
        self._clients.append(client)
        return client

    async def run(self, client: ServiceClient, working_dir: Path, **kwargs: Any) -> list[Any]:
        """Start a run and drain its events, with the run's start result attached."""
        started = await client.start_run("go", working_dir, **kwargs)
        events = await collect_events(client)
        return [started, *events]

    async def aclose(self) -> None:
        for client in self._clients:
            await client.close()
        for service in self._services:
            await service.stop()
        shutil.rmtree(self.sock_dir, ignore_errors=True)


async def collect_events(client: ServiceClient) -> list[Any]:
    """Drain one run's events. Times out loudly; a hang in CI is a bug report."""
    async with asyncio.timeout(TIMEOUT):
        return [event async for event in client.events()]


@pytest.fixture
async def harness(tmp_path: Path):
    runner = Harness(tmp_path)
    try:
        yield runner
    finally:
        await runner.aclose()


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


@pytest.fixture
async def connected(harness: Harness, workdir: Path):
    """A client on a service whose default script calls a tool that needs approval.

    It grants, because the interesting default is the approved path; the tests
    about refusal build their own client.
    """
    service = await harness.serve()
    return await harness.connect(service, permission_handler=_grant_once)


async def _grant_once(event):
    return True, "once"


# --------------------------------------------------------------- handshake


async def test_hello_reports_the_contract(connected: ServiceClient):
    info = connected.info
    assert info["protocol_version"] == PROTOCOL_VERSION
    assert info["server"] == "openagent"
    assert Method.RUN_START in info["methods"]
    assert "permission_requested" in info["events"]
    assert info["permission_modes"] == ["allow_all", "allow_readonly", "ask"]


async def test_a_protocol_version_the_server_does_not_serve_is_refused(
    harness: Harness,
):
    service = await harness.serve()
    with pytest.raises(RpcError) as exc:
        await harness.connect(service, protocol_version=99)
    assert exc.value.code == ErrorCode.PROTOCOL_MISMATCH
    assert exc.value.data["protocol_version"] == PROTOCOL_VERSION


async def test_the_first_call_must_be_hello(harness: Harness):
    service = await harness.serve()
    reader, writer = await asyncio.open_unix_connection(path=str(service.settings.socket_path))
    try:
        writer.write(encode(request(Method.PING, id="c1").to_dict()))
        await writer.drain()
        async with asyncio.timeout(TIMEOUT):
            reply = json.loads(await reader.readline())
        assert reply["error"]["code"] == ErrorCode.INVALID_REQUEST
        assert reply["id"] == "c1"
    finally:
        writer.close()
        await service.stop()


async def test_ping(connected: ServiceClient):
    assert (await connected.ping())["pong"] is True


async def test_unknown_method_is_reported(connected: ServiceClient):
    with pytest.raises(RpcError) as exc:
        await connected.call("does.not.exist")
    assert exc.value.code == ErrorCode.METHOD_NOT_FOUND
    assert Method.RUN_START in exc.value.data["known"]


async def test_a_malformed_frame_is_answered_then_the_socket_closes(harness: Harness):
    """One bad frame must not take the sidecar down while the app is connected."""
    service = await harness.serve()
    reader, writer = await asyncio.open_unix_connection(path=str(service.settings.socket_path))
    try:
        writer.write(b"this is not json\n")
        await writer.drain()
        async with asyncio.timeout(TIMEOUT):
            reply = json.loads(await reader.readline())
        assert reply["error"]["code"] == ErrorCode.PARSE_ERROR
        assert not await reader.readline()  # the server hung up
    finally:
        writer.close()
        await service.stop()


# --------------------------------------------------------------------- runs


async def test_run_streams_the_documented_sequence(
    harness: Harness, connected: ServiceClient, workdir: Path
):
    """The ordering in §4, asserted literally.

    ``permission_requested`` / ``permission_resolved`` precede the tool events:
    the prompt gates execution, so a client that rendered the tool row first
    would show work that had not been approved.
    """
    started, *events = await harness.run(connected, workdir, permission_mode="ask")
    assert [e.type for e in events] == [
        "run_started",
        "message_started",
        "message_completed",
        "permission_requested",
        "permission_resolved",
        "tool_call_started",
        "tool_call_completed",
        "message_started",
        "message_delta",
        "message_completed",
        "run_completed",
    ]
    assert events[-1].result.stop_reason == "end_turn"
    assert events[-1].run_id == started["run_id"]


async def test_run_start_answers_before_the_first_token(
    harness: Harness, connected: ServiceClient, workdir: Path
):
    """The client needs run_id and session_id to render a cancellable spinner."""
    started = await connected.start_run("go", workdir)
    assert started["run_id"]
    assert started["session_id"]
    assert started["working_dir"] == str(workdir.resolve())
    assert started["model"]
    await collect_events(connected)


async def test_run_start_requires_a_prompt(connected: ServiceClient, workdir: Path):
    with pytest.raises(RpcError) as exc:
        await connected.start_run("   ", workdir)
    assert exc.value.code == ErrorCode.INVALID_PARAMS


async def test_working_dir_must_be_absolute_existing_and_not_root(
    connected: ServiceClient, tmp_path: Path
):
    for bad, why in [
        ("relative/dir", "relative path"),
        (str(tmp_path / "missing"), "missing directory"),
        ("/", "filesystem root"),
    ]:
        with pytest.raises(RpcError) as exc:
            await connected.start_run("go", bad)
        assert exc.value.code == ErrorCode.WORKING_DIR_DENIED, why


async def test_roots_are_enforced(harness: Harness, tmp_path: Path):
    allowed = tmp_path / "granted"
    allowed.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    service = await harness.serve(roots=(str(allowed),))
    client = await harness.connect(service)

    with pytest.raises(RpcError) as exc:
        await client.start_run("go", str(outside))
    assert exc.value.code == ErrorCode.WORKING_DIR_DENIED
    assert str(allowed) in exc.value.data["roots"]

    nested = allowed / "nested"
    nested.mkdir()
    started = await client.start_run("go", str(nested), permission_mode="allow_all")
    assert started["working_dir"] == str(nested.resolve())
    await collect_events(client)


async def test_working_dir_reaches_the_tools(harness: Harness, tmp_path: Path):
    """The run's scope is what a relative path means — the point of §5.

    A tool that wrote through the process CWD would write into the .app bundle
    when launchd starts the sidecar.
    """
    seen: list[str] = []

    @tool
    def touch(name: str, tool_context: ToolContext) -> str:
        """Write a file inside the run's working directory."""
        path = tool_context.resolve(name)
        seen.append(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("hi")
        return f"wrote {path.name}"

    project = tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    service = await harness.serve(
        tools=[touch],
        provider_factory=harness.script(
            [tool_turn("touch", {"name": "sub/note.txt"}), text_turn("done")]
        ),
    )
    client = await harness.connect(service)
    started, *events = await harness.run(client, project, permission_mode="allow_all")

    assert "tool_call_completed" in [e.type for e in events]
    assert seen == [str(project / "sub" / "note.txt")]
    assert (project / "sub" / "note.txt").read_text() == "hi"


async def test_a_second_run_on_a_busy_connection_is_refused(harness: Harness, workdir: Path):
    """A queue here is a UI that silently loses work; refuse it out loud."""
    service = await harness.serve(provider_factory=lambda ref: HangingProvider())
    client = await harness.connect(service)

    started = await client.start_run("go", workdir)
    with pytest.raises(RpcError) as exc:
        await client.start_run("second", workdir)
    assert exc.value.code == ErrorCode.INVALID_REQUEST
    assert exc.value.data["run_id"] == started["run_id"]

    await client.cancel(started["run_id"])
    events = await collect_events(client)
    assert events[-1].type == "run_cancelled"


async def test_run_failed_survives_the_wire(harness: Harness, workdir: Path):
    """A provider failure is an event, not a closed socket: the client needs to
    render the error and keep the session it built."""
    service = await harness.serve(
        provider_factory=harness.script([error_turn("upstream exploded")])
    )
    client = await harness.connect(service)
    _, *events = await harness.run(client, workdir)

    assert events[-1].type == "run_failed"
    assert "upstream exploded" in events[-1].error
    assert "run_completed" not in [e.type for e in events]


# -------------------------------------------------------------- permissions


async def test_permission_grant_runs_the_tool(harness: Harness, workdir: Path):
    asked: list[str] = []

    async def grant(event):
        asked.append(event.tool_name)
        return True, "once"

    service = await harness.serve()
    client = await harness.connect(service, permission_handler=grant)
    _, *events = await harness.run(client, workdir, permission_mode="ask")

    types = [e.type for e in events]
    assert asked == ["peek"]
    assert types.index("permission_resolved") < types.index("tool_call_started")
    resolved = next(e for e in events if e.type == "permission_resolved")
    assert (resolved.granted, resolved.scope) == (True, "once")


async def test_denial_is_a_tool_result_and_no_tool_event_is_emitted(
    harness: Harness, workdir: Path
):
    """A refusal is not a crash: the model must be told, and the UI must not
    show a tool that never ran."""
    service = await harness.serve()
    client = await harness.connect(service, permission_handler=_deny)
    _, *events = await harness.run(client, workdir, permission_mode="ask")

    types = [e.type for e in events]
    assert "tool_call_started" not in types
    assert types[-1] == "run_completed"
    shown_to_model = carried_history(harness.providers[0].calls[1]["messages"])
    assert "Permission denied" in shown_to_model


async def _deny(event):
    return False, "once"


def carried_history(messages: list[Any]) -> str:
    """JSON for a message list, in the shape the model is given.

    Takes provider ``Message`` objects — serialized through ``Session``, the same
    path the providers themselves use — or already-serialized session dicts from
    ``session.load``. Both answer the same question: did this reach the model?
    """
    if messages and not dataclasses.is_dataclass(messages[0]):
        return json.dumps(messages)
    session = Session()
    session.replace_history(messages)
    return json.dumps(session.to_list())


async def test_an_unanswered_prompt_refuses_the_tool(harness: Harness, workdir: Path):
    """No answer is not consent. The run continues, on a denial."""
    service = await harness.serve(permission_timeout=0.3)
    client = await harness.connect(service)  # no permission handler
    _, *events = await harness.run(client, workdir, permission_mode="ask")

    resolved = next(e for e in events if e.type == "permission_resolved")
    assert resolved.granted is False
    assert "no answer" in resolved.reason
    assert events[-1].type == "run_completed"


async def test_session_scope_stops_asking_after_the_first_yes(harness: Harness, workdir: Path):
    asked: list[str] = []

    async def grant(event):
        asked.append(event.tool_name)
        return True, "session"

    service = await harness.serve(
        provider_factory=harness.script(
            [
                tool_turn("peek", {"text": "a"}, id="c1"),
                tool_turn("peek", {"text": "b"}, id="c2"),
                text_turn("done"),
            ]
        )
    )
    client = await harness.connect(service, permission_handler=grant)
    _, *events = await harness.run(client, workdir, permission_mode="ask")

    assert asked == ["peek"], "the second call must reuse the session grant"
    assert [e.type for e in events].count("permission_requested") == 1
    assert [e.type for e in events].count("tool_call_completed") == 2


async def test_allow_readonly_never_prompts_and_refuses_writes(harness: Harness, workdir: Path):
    asked: list[str] = []

    async def grant(event):
        asked.append(event.tool_name)
        return True, "once"

    service = await harness.serve()
    client = await harness.connect(service, permission_handler=grant)
    _, *events = await harness.run(client, workdir, permission_mode="allow_readonly")

    assert asked == [], "nobody is asked in this mode"
    assert "permission_requested" not in [e.type for e in events]
    assert "tool_call_started" not in [e.type for e in events]
    assert events[-1].type == "run_completed"


# ------------------------------------------------------------- cancellation


async def test_cancel_ends_the_run_on_run_cancelled(harness: Harness, workdir: Path):
    service = await harness.serve(provider_factory=lambda ref: HangingProvider())
    client = await harness.connect(service)

    started = await client.start_run("go", workdir)
    await client.cancel(started["run_id"])
    events = await collect_events(client)

    assert events[-1].type == "run_cancelled"
    assert events[-1].reason


async def test_cancelling_an_unknown_run_is_reported(connected: ServiceClient):
    with pytest.raises(RpcError) as exc:
        await connected.cancel("run_nope")
    assert exc.value.code == ErrorCode.RUN_NOT_FOUND


async def test_disconnect_cancels_the_run_and_closes_the_provider(
    harness: Harness, workdir: Path
):
    """A dead client must not leave an agent running bash with nobody watching."""
    provider = HangingProvider()
    service = await harness.serve(provider_factory=lambda ref: provider)
    client = await harness.connect(service)
    await client.start_run("go", workdir)
    await client.close()

    async with asyncio.timeout(TIMEOUT):
        while service.active_connections:
            await asyncio.sleep(0.02)
    assert provider.closed, "the provider is closed even when the client vanishes"


async def test_the_provider_is_closed_after_every_run(harness: Harness, workdir: Path):
    service = await harness.serve()
    client = await harness.connect(service)
    await harness.run(client, workdir, permission_mode="allow_all")

    assert harness.providers, "the service built a provider"
    assert all(p.closed for p in harness.providers), "a long-lived app leaks a pool per run"


# ----------------------------------------------------------- sessions, models


async def test_a_session_survives_a_reconnect(harness: Harness, workdir: Path):
    """The event stream is not resumable; the session is. That is what a reconnect is."""
    first = await harness.connect(await harness.serve())
    started = await first.start_run(
        "first question", workdir, permission_mode="allow_all"
    )
    await collect_events(first)
    await first.close()

    second = await harness.connect(await harness.serve())
    rows = await second.sessions()
    ids = [row["session_id"] for row in rows]
    assert started["session_id"] in ids
    row = rows[ids.index(started["session_id"])]
    assert row["message_count"] > 0, "the session was saved empty"

    loaded = await second.load_session(started["session_id"])
    assert loaded[0]["content"] == "first question"

    await second.start_run(
        "second question", workdir, session_id=started["session_id"], permission_mode="allow_all"
    )
    events = await collect_events(second)
    assert events[-1].type == "run_completed"

    resumed = harness.providers[1].calls[0]["messages"]
    history = carried_history(resumed)
    assert "first question" in history, "the resumed run must carry the conversation"
    assert "second question" in history


async def test_session_ids_are_validated(connected: ServiceClient, workdir: Path):
    for bad in ["../../etc/passwd", "a/b", "sess/../x", "x" * 65, "..", ".hidden"]:
        with pytest.raises(RpcError) as exc:
            await connected.start_run("go", workdir, session_id=bad)
        assert exc.value.code == ErrorCode.INVALID_PARAMS, bad


async def test_an_empty_session_id_starts_a_fresh_session(harness: Harness, workdir: Path):
    """`""` is not a resume target: it means "start fresh".

    Forwarding it as a resume id would be a session-not-found error, which is
    not what the client means and not what the user asked for.
    """
    service = await harness.serve()
    client = await harness.connect(service)

    started = await client.start_run("go", workdir, session_id="")
    assert started["session_id"]
    events = await collect_events(client)
    assert events[-1].type == "run_completed"


async def test_model_list_reports_policy_and_selection(connected: ServiceClient):
    data = await connected.models()
    assert data["models"], "the catalog is static, so it is never empty"
    assert data["policy"] == "local_first"
    assert "selected" in data
    assert {m["provider"] for m in data["models"]} >= {"lmstudio", "anthropic"}


async def test_startup_warms_the_catalog(harness: Harness):
    """A model server the user added is only in the catalog after discovery.

    The app opens the model picker before it runs anything, so the sidecar
    probes at startup; a client must not have to know to ask.
    """

    class SpyCatalog:
        def __init__(self):
            self.discovered = 0

        async def discover(self):
            self.discovered += 1

    spy = SpyCatalog()
    settings = ServiceSettings(
        socket_path=harness.sock_dir / "warm.sock",
        tools=[echo],
        provider_factory=harness.script([text_turn("ok")]),
    )
    service = AgentService(settings, catalog=spy)  # type: ignore[arg-type]
    await service.start()
    try:
        async with asyncio.timeout(TIMEOUT):
            while spy.discovered == 0:
                await asyncio.sleep(0.02)
    finally:
        await service.stop()

    settings_quiet = ServiceSettings(
        socket_path=harness.sock_dir / "quiet.sock",
        tools=[echo],
        provider_factory=harness.script([text_turn("ok")]),
        discover_on_start=False,
    )
    spy2 = SpyCatalog()
    quiet = AgentService(settings_quiet, catalog=spy2)  # type: ignore[arg-type]
    await quiet.start()
    await quiet.stop()
    assert spy2.discovered == 0, "tests and embedders can turn probing off"


async def test_naming_a_session_starts_it(connected: ServiceClient, workdir: Path):
    """`--session my-name` on a first run must work, not report not-found."""
    started = await connected.start_run(
        "first", workdir, session_id="proj_alpha", permission_mode="allow_all"
    )
    assert started["session_id"] == "proj_alpha"
    await collect_events(connected)

    rows = await connected.sessions()
    assert "proj_alpha" in {row["session_id"] for row in rows}

    # ...and the next run on that name carries the first one.
    second = await connected.start_run(
        "second", workdir, session_id="proj_alpha", permission_mode="allow_all"
    )
    assert second["session_id"] == "proj_alpha"
    await collect_events(connected)
    carried = await connected.load_session("proj_alpha")
    # Tool results are user-role messages with list content; the prompts are the
    # plain strings, in order.
    prompts = [m["content"] for m in carried if m["role"] == "user" and isinstance(m["content"], str)]
    assert prompts == ["first", "second"]


async def test_a_model_the_catalog_only_knows_after_discovery_can_be_selected(
    harness: Harness, workdir: Path
):
    """``run.start(model=...)`` must not require the client to have listed first."""

    class LateCatalog:
        def __init__(self):
            self.calls = 0

        def get(self, key):
            if self.calls == 0:
                return None
            from openagent.provider.model_ref import ModelRef

            return ModelRef(model="late", provider="lan", wire_api="openai-chat")

        async def discover(self):
            self.calls += 1

        def all_models(self):
            return []

    catalog = LateCatalog()
    settings = ServiceSettings(
        socket_path=harness.sock_dir / "late.sock",
        tools=[echo],
        provider_factory=harness.script([text_turn("ok")]),
        discover_on_start=False,
    )
    service = AgentService(settings, catalog=catalog)  # type: ignore[arg-type]
    await service.start()
    try:
        client = await harness.connect(service)
        started = await client.start_run("go", workdir, model="lan/late")
        assert started["model"] == "lan/late"
        events = await collect_events(client)
        assert events[-1].type == "run_completed"
        assert catalog.calls == 1
    finally:
        await service.stop()


async def test_an_unknown_model_is_reported_as_unavailable(
    connected: ServiceClient, workdir: Path
):
    with pytest.raises(RpcError) as exc:
        await connected.start_run("go", workdir, model="nosuchprovider/nosuchmodel")
    assert exc.value.code == ErrorCode.MODEL_UNAVAILABLE


async def test_missing_credentials_become_not_authenticated(harness: Harness, workdir: Path):
    """The app's sign-in screen is driven by this error, so it must carry the hint."""

    class NoAuthRegistry:
        endpoints: dict = {}

        def __init__(self):
            self.broker = None

        async def build_async(self, ref):
            raise AuthRequired(
                "anthropic",
                "no API key for anthropic",
                "openagent auth login anthropic",
            )

    registry = NoAuthRegistry()
    from openagent.provider.auth.broker import AuthBroker
    from openagent.provider.registry import ProviderRegistry

    signed_out = AgentService(
        ServiceSettings(
            socket_path=harness.sock_dir / "noauth.sock",
            tools=[echo],
        ),
        registry=registry,  # type: ignore[arg-type]
        # A real catalog: the model must resolve and the *credential* must be
        # what fails, which is the case the app's sign-in screen handles.
        catalog=ModelCatalog(ProviderRegistry(broker=AuthBroker())),
    )
    await signed_out.start()
    try:
        client = await harness.connect(signed_out)
        with pytest.raises(RpcError) as exc:
            await client.start_run("go", workdir, model="anthropic/claude-sonnet-4-5")
        assert exc.value.code == ErrorCode.NOT_AUTHENTICATED
        assert exc.value.data["sign_in_hint"] == "openagent auth login anthropic"
    finally:
        await signed_out.stop()


async def test_tool_list_names_the_risk_of_every_tool(connected: ServiceClient):
    tools = {row["name"]: row for row in await connected.tools()}
    assert set(tools) == {"echo", "peek", "recall"}
    # An unclassified tool is the fail-closed case: it needs approval.
    assert tools["peek"]["risk"] == "unknown"
    assert tools["peek"]["requires_permission"] is True
    assert tools["recall"]["risk"] == "read"
    assert tools["recall"]["requires_permission"] is False


async def test_config_get_and_set_are_narrow(connected: ServiceClient):
    config = await connected.config(["models"])
    assert config["models"]["policy"] == "local_first"

    with pytest.raises(RpcError) as exc:
        await connected.call(Method.CONFIG_SET, {"values": {"tavily_api_key": "sk-nope"}})
    assert exc.value.code == ErrorCode.INVALID_PARAMS
    assert "tavily_api_key" in exc.value.data["rejected"]

    updated = await connected.call(Method.CONFIG_SET, {"values": {"policy": "cloud_first"}})
    assert updated["config"]["models"]["policy"] == "cloud_first"


async def test_auth_status_reports_providers_and_servers(connected: ServiceClient):
    data = await connected.auth_status()
    assert data["providers"], "the built-in providers are always listed"
    assert {"provider", "mode", "configured"} <= set(data["providers"][0])
    assert isinstance(data["servers"], list)


async def test_auth_login_is_refused_with_a_usable_hint(connected: ServiceClient):
    with pytest.raises(RpcError) as exc:
        await connected.call(Method.AUTH_LOGIN, {"provider": "anthropic"})
    assert exc.value.code == ErrorCode.NOT_IMPLEMENTED
    assert "openagent auth login" in exc.value.message
