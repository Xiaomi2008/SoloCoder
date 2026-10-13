"""The Unix-socket server: one connection, one run, a stream of events.

Spec: ``dev_docs/agent-service-protocol.md``. Design notes worth keeping,
because each one is a bug that was paid for:

- **One run per connection.** A run streams on the connection that started it,
  so cancellation and permission replies are addressed to the right run with no
  routing table. A second ``run.start`` on a busy connection is refused, not
  queued: a queue is a UI that silently loses work.
- **The reader loop and the run task are separate.** The run blocks on tool
  approval; if the same task read the socket, ``permission.respond`` would
  arrive after the thing it is answering.
- **Writes are serialized by a lock.** The run task writes events while the
  reader writes responses; interleaving two frames produces one corrupt frame.
- **Disconnect is a cancel.** A dead client must not leave an agent running
  ``bash`` with nobody watching.
- **The provider is closed after every run.** A long-lived app builds a
  provider per turn; leaking a connection pool per turn is how a sidecar grows
  to a gigabyte.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openagent.core.session import Session
from openagent.core.tool import ToolContext
from openagent.provider.auth.tokens import AuthRequired
from openagent.provider.auth.broker import AuthBroker
from openagent.provider.catalog import ModelCatalog
from openagent.provider.model_ref import Capabilities, ModelRef
from openagent.provider.registry import (
    BUILTIN_PROVIDERS,
    ProviderDependencyError,
    ProviderRegistry,
)
from openagent.provider.router import ModelRouter, NoCapableModel, RoutingRules
from openagent.runtime.events import RunFailed
from openagent.runtime.loop import next_id
from openagent.runtime.permissions import (
    ASK_BY_DEFAULT,
    PermissionDecision,
    PermissionMode,
    PermissionRequest,
    gate_for_mode,
    risk_reason,
    tool_risk,
)
from openagent.runtime.runner import AgentRunner
from openagent.service import sessions
from openagent.service.protocol import (
    MIN_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    SERVER_NAME,
    TERMINAL_EVENT_TYPES,
    ErrorCode,
    FrameReader,
    Method,
    ProtocolError,
    RpcError,
    RpcRequest,
    as_request,
    encode,
    error_response,
    event_names,
    event_to_notification,
    response,
)
from openagent.service.scope import validate_working_dir

SERVER_VERSION = "0.1.0"
READ_CHUNK = 64 * 1024

logger = logging.getLogger("openagent.service")
DEFAULT_PERMISSION_TIMEOUT = 60.0
DEFAULT_MAX_TURNS = 100

PERMISSION_MODES: tuple[str, ...] = ("allow_all", "allow_readonly", "ask")


@dataclass
class ServiceSettings:
    """How this server behaves. Everything here is a CLI flag on ``serve``."""

    socket_path: Path = field(
        default_factory=lambda: sessions.sessions_dir().parent / "agent.sock"
    )
    roots: tuple[str, ...] = ()
    permission_timeout: float = DEFAULT_PERMISSION_TIMEOUT
    max_turns: int = DEFAULT_MAX_TURNS
    computer_use: bool = False
    system_prompt: str | None = None
    #: Override the tool set. Tests run the service against a two-tool world.
    tools: Sequence[Callable[..., Any]] | None = None
    #: Override provider construction, so the contract tests never hit a model.
    provider_factory: Callable[[ModelRef], Any] | None = None
    #: Probe the local and LAN model servers at startup, so the app's picker is
    #: populated before the first call. Tests turn this off.
    discover_on_start: bool = True


@dataclass
class _Run:
    run_id: str
    runner: AgentRunner
    provider: Any
    session: Session
    session_id: str
    task: asyncio.Task | None = None

    @property
    def active(self) -> bool:
        return self.task is not None and not self.task.done()


class Connection:
    """One client on one socket."""

    def __init__(self, service: "AgentService", reader: asyncio.StreamReader, writer) -> None:
        self.service = service
        self._reader = reader
        self._writer = writer
        self._write_lock = asyncio.Lock()
        self.handshaked = False
        self.fatal = False
        self.run: _Run | None = None
        self.pending: dict[str, asyncio.Future] = {}
        self.peer = ""

    # ---------------------------------------------------------------- writing

    async def send(self, message: dict[str, Any]) -> None:
        async with self._write_lock:
            self._writer.write(encode(message))
            await self._writer.drain()

    async def send_event(self, event: Any) -> None:
        await self.send(event_to_notification(event).to_dict())

    # ----------------------------------------------------------------- reading

    async def serve(self) -> None:
        frames = FrameReader()
        try:
            while True:
                chunk = await self._reader.read(READ_CHUNK)
                if not chunk:
                    break
                try:
                    parsed = frames.feed(chunk)
                except ProtocolError as exc:
                    await self.send(
                        error_response(None, exc.code, exc.message, exc.data).to_dict()
                    )
                    self.fatal = True
                    break
                for frame in parsed:
                    await self._handle(frame)
                    if self.fatal:
                        break
                if self.fatal:
                    break
        except (asyncio.CancelledError, ConnectionError):
            pass
        finally:
            await self.shutdown()

    async def _handle(self, frame: Any) -> None:
        try:
            req = as_request(frame)
        except ProtocolError as exc:
            await self.send(error_response(None, exc.code, exc.message, exc.data).to_dict())
            return

        if req is None:
            # A response from the client. v1 has no server-initiated requests,
            # so there is nothing to correlate; ignore rather than error.
            return

        if req.is_notification:
            return  # fire and forget, by definition

        if not self.handshaked and req.method != Method.HELLO:
            await self.send(
                error_response(
                    req.id,
                    ErrorCode.INVALID_REQUEST,
                    "the first call must be 'hello'",
                    {"method": req.method},
                ).to_dict()
            )
            return

        try:
            result = await self.service.handle(self, req)
        except RpcError as exc:
            await self.send(
                error_response(req.id, exc.code, exc.message, exc.data).to_dict()
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # an engine bug, reported, not swallowed
            await self.send(
                error_response(
                    req.id, ErrorCode.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
                ).to_dict()
            )
            return
        await self.send(response(req.id, result).to_dict())

    # ------------------------------------------------------- permission round-trip

    async def ask_permission(self, request: PermissionRequest) -> PermissionDecision:
        """Block until the client answers, and refuse when it does not.

        The runner has already emitted ``permission_requested`` by the time
        this runs, so the client knows what it is being asked. Timeout is a
        denial: the alternative is running a command nobody approved.
        """
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[request.request_id] = future
        try:
            return await asyncio.wait_for(future, self.service.settings.permission_timeout)
        except asyncio.TimeoutError:
            return PermissionDecision(
                granted=False,
                scope="never",
                reason=(
                    f"no answer within {self.service.settings.permission_timeout:.0f}s; "
                    "refused by default"
                ),
            )
        except asyncio.CancelledError:
            return PermissionDecision(granted=False, scope="never", reason="run cancelled")
        finally:
            self.pending.pop(request.request_id, None)

    def deny_pending(self, reason: str) -> None:
        for future in list(self.pending.values()):
            if not future.done():
                future.set_result(
                    PermissionDecision(granted=False, scope="never", reason=reason)
                )
        self.pending.clear()

    # ---------------------------------------------------------------- closing

    async def shutdown(self) -> None:
        self.deny_pending("client disconnected")
        if self.run is not None and self.run.active:
            self.run.runner.cancel("client disconnected")
            if self.run.task is not None:
                self.run.task.cancel()
                try:
                    await self.run.task
                except (asyncio.CancelledError, Exception):
                    pass
        self.run = None
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, OSError):
            pass


class AgentService:
    """The method table, the engine, and the socket."""

    def __init__(
        self,
        settings: ServiceSettings | None = None,
        *,
        registry: ProviderRegistry | None = None,
        catalog: ModelCatalog | None = None,
        broker: AuthBroker | None = None,
    ) -> None:
        self.settings = settings or ServiceSettings()
        self.broker = broker or AuthBroker()
        self.registry = registry or ProviderRegistry(broker=self.broker)
        self.catalog = catalog or ModelCatalog(self.registry)
        self._server: asyncio.Server | None = None
        #: One task per client connection. Kept apart from background work: a
        #: shutdown must drain the clients, and a client disconnect must not
        #: wait on a model probe that has nothing to do with it.
        self._conn_tasks: set[asyncio.Task] = set()
        self._bg_tasks: set[asyncio.Task] = set()

    @property
    def active_connections(self) -> int:
        """Connections being served right now."""
        return len(self._conn_tasks)

    # ------------------------------------------------------------------ serve

    async def start(self) -> asyncio.Server:
        path = Path(self.settings.socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            # A stale socket from a dead server is the normal case after a
            # crash; a live one is not.
            if await _socket_alive(path):
                raise OSError(f"another agent service is already listening on {path}")
            path.unlink()

        server = await asyncio.start_unix_server(self._on_connection, path=str(path))
        path.chmod(0o600)  # the socket file is the credential
        self._server = server
        # Warm the catalog: a user-added model server only enters it through
        # discovery, and the app's model picker should not have to ask first.
        if self.settings.discover_on_start:
            task = asyncio.create_task(self._warm_catalog())
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        return server

    async def _warm_catalog(self) -> None:
        try:
            await self.catalog.discover()
        except Exception:  # discover() is documented never to raise; keep the promise cheap
            logger.debug("startup model discovery failed", exc_info=True)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            # ``Server.wait_closed()`` waits for connection handlers to finish,
            # and a handler is a client that stays connected for the life of the
            # app. Shut the clients down first or shutdown hangs on them.
            pending = [*self._conn_tasks, *self._bg_tasks]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await self._server.wait_closed()
            self._server = None
        path = Path(self.settings.socket_path)
        if path.exists():
            path.unlink(missing_ok=True)

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def _on_connection(self, reader: asyncio.StreamReader, writer) -> None:
        conn = Connection(self, reader, writer)
        task = asyncio.current_task()
        if task is not None:
            self._conn_tasks.add(task)
            task.add_done_callback(self._conn_tasks.discard)
        try:
            await conn.serve()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A sidecar that dies because one client sent something strange is
            # the worst possible failure mode for the app that owns it.
            logger.exception("connection ended badly")

    # --------------------------------------------------------------- dispatch

    async def handle(self, conn: Connection, req: RpcRequest) -> dict[str, Any]:
        handler = self._handlers().get(req.method)
        if handler is None:
            raise RpcError(
                ErrorCode.METHOD_NOT_FOUND,
                f"unknown method {req.method!r}",
                {"known": sorted(str(m) for m in self._handlers())},
            )
        return await handler(conn, req.params)

    def _handlers(self) -> dict[str, Callable]:
        return {
            Method.HELLO: self._hello,
            Method.PING: self._ping,
            Method.RUN_START: self._run_start,
            Method.RUN_CANCEL: self._run_cancel,
            Method.PERMISSION_RESPOND: self._permission_respond,
            Method.MODEL_LIST: self._model_list,
            Method.MODEL_PROBE: self._model_probe,
            Method.MODEL_SELECT: self._model_select,
            Method.AUTH_STATUS: self._auth_status,
            Method.AUTH_LOGOUT: self._auth_logout,
            Method.AUTH_LOGIN: self._auth_login,
            Method.TOOL_LIST: self._tool_list,
            Method.SESSION_LIST: self._session_list,
            Method.SESSION_LOAD: self._session_load,
            Method.SESSION_SAVE: self._session_save,
            Method.CONFIG_GET: self._config_get,
            Method.CONFIG_SET: self._config_set,
        }

    # ---------------------------------------------------------- handshake/liveness

    async def _hello(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        version = params.get("protocol_version")
        if not isinstance(version, int):
            raise RpcError(
                ErrorCode.INVALID_PARAMS,
                "protocol_version must be an integer",
                {"protocol_version": version},
            )
        if version < MIN_PROTOCOL_VERSION or version > PROTOCOL_VERSION:
            conn.fatal = True
            raise RpcError(
                ErrorCode.PROTOCOL_MISMATCH,
                f"client speaks protocol {version}; server serves "
                f"{MIN_PROTOCOL_VERSION}-{PROTOCOL_VERSION}",
                {
                    "min_protocol_version": MIN_PROTOCOL_VERSION,
                    "protocol_version": PROTOCOL_VERSION,
                },
            )
        conn.handshaked = True
        return {
            "protocol_version": PROTOCOL_VERSION,
            "min_protocol_version": MIN_PROTOCOL_VERSION,
            "server": SERVER_NAME,
            "server_version": SERVER_VERSION,
            "methods": sorted(str(m) for m in self._handlers()),
            "events": event_names(),
            # A client treats the union of its own terminal set and this one as
            # terminal. Without it, a terminal event added in a later protocol
            # version leaves an older client's event stream waiting forever: the
            # connection stays open after a run, so nothing else ends it.
            "terminal_event_types": sorted(TERMINAL_EVENT_TYPES),
            "permission_modes": list(PERMISSION_MODES),
        }

    async def _ping(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        return {"pong": True, "server_time": time.time()}

    # -------------------------------------------------------------------- runs

    async def _run_start(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        if conn.run is not None and conn.run.active:
            raise RpcError(
                ErrorCode.INVALID_REQUEST,
                "this connection already has a run in flight; call run.cancel",
                {"run_id": conn.run.run_id},
            )

        prompt = params.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise RpcError(ErrorCode.INVALID_PARAMS, "prompt is required and must be text")

        working_dir = validate_working_dir(params.get("working_dir"), self.settings.roots)

        mode = params.get("permission_mode", "ask")
        if mode not in PERMISSION_MODES:
            raise RpcError(
                ErrorCode.INVALID_PARAMS,
                f"permission_mode must be one of {', '.join(PERMISSION_MODES)}",
                {"permission_mode": mode},
            )

        images = params.get("images")
        if images is not None and (
            not isinstance(images, list) or not all(isinstance(i, str) for i in images)
        ):
            raise RpcError(ErrorCode.INVALID_PARAMS, "images must be a list of base64 strings")

        max_turns = params.get("max_turns", self.settings.max_turns)
        if not isinstance(max_turns, int) or max_turns < 1:
            raise RpcError(ErrorCode.INVALID_PARAMS, "max_turns must be a positive integer")

        ref = await self._resolve_model(params.get("model"))
        resume = params.get("session_id")
        session_id = sessions.check_session_id(resume) if resume else sessions.new_session_id()
        session = (
            sessions.load_or_start(session_id) if resume else Session(system_prompt=self._system_prompt())
        )

        provider = await self._build_provider(ref)
        run_id = next_id("run")
        runner = AgentRunner(
            provider,
            session=session,
            tools=self._tools(),
            tool_context=ToolContext(working_dir=str(working_dir)),
            max_turns=max_turns,
            permission_gate=gate_for_mode(mode, callback=conn.ask_permission),  # type: ignore[arg-type]
        )
        run = _Run(
            run_id=run_id,
            runner=runner,
            provider=provider,
            session=session,
            session_id=session_id,
        )
        conn.run = run
        run.task = asyncio.create_task(self._pump(conn, run, prompt, images))
        return {
            "run_id": run_id,
            "session_id": session_id,
            "model": ref.key,
            "working_dir": str(working_dir),
        }

    async def _pump(self, conn: Connection, run: _Run, prompt: str, images: Any) -> None:
        """Stream one run to its terminal event, then clean up.

        Cleanup is unconditional: the session is saved even when the run
        failed, because the work done before the failure is the thing the user
        wants back after a reconnect.
        """
        try:
            async for event in run.runner.stream(prompt, images=images, run_id=run.run_id):
                await conn.send_event(event)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            try:
                await conn.send_event(
                    RunFailed(run_id=run.run_id, error=f"{type(exc).__name__}: {exc}")
                )
            except ConnectionError:
                pass
        finally:
            try:
                sessions.save(run.session, run.session_id)
            except OSError:
                pass
            await _close_provider(run.provider)
            conn.deny_pending("run ended")
            if conn.run is run:
                conn.run = None

    async def _run_cancel(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        run = conn.run
        run_id = params.get("run_id")
        if run is None or run.run_id != run_id:
            raise RpcError(
                ErrorCode.RUN_NOT_FOUND,
                "no run with that id on this connection",
                {"run_id": run_id, "active": run.run_id if run and run.active else None},
            )
        # Cooperative first, then the hard cancel: the flag is what makes the
        # runner end on run_cancelled instead of vanishing mid-generator.
        run.runner.cancel("cancelled by client")
        if run.task is not None:
            run.task.cancel()
        return {"cancelled": True, "run_id": run.run_id}

    async def _permission_respond(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        request_id = str(params.get("request_id") or "")
        granted = bool(params.get("granted"))
        scope = str(params.get("scope") or "once")
        if scope not in ("once", "session", "never"):
            raise RpcError(
                ErrorCode.INVALID_PARAMS,
                "scope must be once, session, or never",
                {"scope": scope},
            )
        future = conn.pending.get(request_id)
        if future is None or future.done():
            raise RpcError(
                ErrorCode.INVALID_PARAMS,
                f"no pending permission request {request_id!r} "
                "(it timed out, was already answered, or never existed)",
                {"request_id": request_id},
            )
        future.set_result(PermissionDecision(granted=granted, scope=scope))
        return {"ok": True, "granted": granted, "scope": scope}

    # ------------------------------------------------------------------ models

    async def _resolve_model(self, key: Any) -> ModelRef:
        """An explicit ``provider/model``, or the router's choice for the main role."""
        if isinstance(key, str) and key.strip():
            wanted = key.strip()
            ref = self.catalog.get(wanted)
            if ref is None:
                # A model on a server the user added is only in the catalog
                # after discovery, and `run.start` must not require the client
                # to have listed models first.
                await self.catalog.discover()
                ref = self.catalog.get(wanted)
            if ref is None:
                raise RpcError(
                    ErrorCode.MODEL_UNAVAILABLE,
                    f"unknown model {wanted!r}; call model.list",
                    {"model": wanted},
                )
            return ref
        requirement = Capabilities(tools=True)
        try:
            return self._router().select(requirement, role="main")
        except NoCapableModel as exc:
            raise RpcError(
                ErrorCode.MODEL_UNAVAILABLE,
                str(exc),
                {"policy": str(self._router().rules.policy)},
            ) from exc

    def _router(self) -> ModelRouter:
        from openagent.core.config import get_config

        models = get_config().models
        return ModelRouter(
            self.catalog,
            rules=RoutingRules.from_dict(
                {
                    "policy": models.policy,
                    "default_key": models.default_model,
                    "roles": models.roles,
                }
            ),
        )

    def _system_prompt(self) -> str:
        """The prompt a fresh session starts with.

        The service runs the same product as the CLI, so it uses the same
        prompt; ``ServiceSettings.system_prompt`` exists so tests and the app
        can narrow the persona without touching the CLI.
        """
        if self.settings.system_prompt is not None:
            return self.settings.system_prompt
        from openagent.prompts import load_prompt

        return load_prompt("solocoder")

    async def _build_provider(self, ref: ModelRef) -> Any:
        if self.settings.provider_factory is not None:
            return self.settings.provider_factory(ref)
        try:
            return await self.registry.build_async(ref)
        except ProviderDependencyError as exc:
            raise RpcError(ErrorCode.DEPENDENCY_MISSING, str(exc)) from exc
        except Exception as exc:
            if isinstance(exc, AuthRequired):
                raise RpcError(
                    ErrorCode.NOT_AUTHENTICATED,
                    f"{ref.provider} needs credentials: {exc}",
                    {"provider": ref.provider, "sign_in_hint": getattr(exc, "sign_in_hint", "")},
                ) from exc
            raise RpcError(ErrorCode.PROVIDER_ERROR, f"cannot build {ref.key}: {exc}") from exc

    async def _model_list(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        refs = list(self.catalog.all_models())
        if bool(params.get("discover")):
            discovered = await self.catalog.discover()
            seen = {r.key for r in refs}
            for models in discovered.values():
                for ref in models:
                    if ref.key not in seen:
                        seen.add(ref.key)
                        refs.append(ref)
        router = self._router()
        selected: str | None = None
        try:
            selected = router.select(Capabilities(tools=True), role="main").key
        except NoCapableModel:
            selected = None
        return {
            "models": [r.to_dict() for r in refs],
            "policy": str(router.rules.policy),
            "default_model": router.rules.default_key,
            "selected": selected,
            "discovery_errors": dict(self.catalog.discovery_errors),
        }

    async def _model_probe(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        ref = await self._resolve_model(params.get("model"))
        result = await self.catalog.probe(ref)
        return {
            "model": ref.key,
            "ok": result.ok,
            "tools": result.tools_ok,
            "latency_ms": result.latency_ms,
            "detail": result.error,
        }

    async def _model_select(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        from openagent.core.config import ConfigManager

        ref = await self._resolve_model(params.get("model"))
        ConfigManager.update_config(default_model=ref.key)
        return {"default_model": ref.key}

    # ------------------------------------------------------------------- auth

    async def _auth_status(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        rows = [
            {**dataclasses.asdict(row), "mode": str(row.mode)}
            for row in self.broker.status()
        ]
        servers = []
        for spec in self.registry.endpoints.values():
            if spec.provider in BUILTIN_PROVIDERS:
                continue
            creds = self.broker.load_credentials(spec.provider)
            has_key = bool(getattr(creds, "api_key", None) or getattr(creds, "bearer_token", None))
            servers.append(
                {
                    "provider": spec.provider,
                    "label": spec.label,
                    "base_url": spec.base_url,
                    "key_set": has_key,
                    "local": spec.local,
                }
            )
        return {"providers": rows, "servers": servers}

    async def _auth_logout(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        provider = str(params.get("provider") or "")
        if not provider:
            raise RpcError(ErrorCode.INVALID_PARAMS, "provider is required")
        return {"provider": provider, "removed": self.broker.delete_credentials(provider)}

    async def _auth_login(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        raise RpcError(
            ErrorCode.NOT_IMPLEMENTED,
            "sign-in needs an interactive browser; run `openagent auth login <provider>` in a terminal",
        )

    # ------------------------------------------------------------------ tools

    def _tools(self) -> list[Callable[..., Any]]:
        if self.settings.tools is not None:
            return list(self.settings.tools)
        from openagent.apps.solocoder.agent import build_solocoder_tools

        return build_solocoder_tools(computer_use=self.settings.computer_use)

    async def _tool_list(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        from openagent.core.tool import ToolRegistry

        registry = ToolRegistry()
        for fn in self._tools():
            registry.register(fn)
        return {
            "tools": [
                {
                    "name": entry.name,
                    "description": entry.description,
                    "risk": tool_risk(entry.name),
                    "requires_permission": tool_risk(entry.name) in ASK_BY_DEFAULT,
                    "reason": risk_reason(entry.name),
                }
                for entry in registry.entries
            ]
        }

    # --------------------------------------------------------------- sessions

    async def _session_list(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        return {"sessions": sessions.list_sessions()}

    async def _session_load(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        session_id = sessions.check_session_id(params.get("session_id"))
        session = sessions.load(session_id)
        return {
            "session_id": session_id,
            "system_prompt": session.system_prompt,
            "messages": session.to_list(),
        }

    async def _session_save(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        run = conn.run
        if run is None:
            raise RpcError(
                ErrorCode.INVALID_REQUEST,
                "nothing to save on this connection: session.save runs after a run",
            )
        target = sessions.save(run.session, run.session_id)
        return {"path": str(target), "session_id": run.session_id}

    # ----------------------------------------------------------------- config

    async def _config_get(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        from openagent.core.config import get_config

        data = get_config().to_dict()
        keys = params.get("keys")
        if isinstance(keys, list) and keys:
            data = {k: v for k, v in data.items() if k in keys}
        return {"config": data}

    #: What a client may change. Credentials are not on this list: they go
    #: through the CLI, where a human types them.
    MUTABLE_CONFIG = ("policy", "default_model", "roles", "provider")

    async def _config_set(self, conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
        from openagent.core.config import ConfigManager

        values = params.get("values")
        if not isinstance(values, dict) or not values:
            raise RpcError(ErrorCode.INVALID_PARAMS, "values must be a non-empty object")
        unknown = [k for k in values if k not in self.MUTABLE_CONFIG]
        if unknown:
            raise RpcError(
                ErrorCode.INVALID_PARAMS,
                f"not settable over the socket: {', '.join(unknown)}; "
                f"allowed: {', '.join(self.MUTABLE_CONFIG)}",
                {"rejected": unknown},
            )
        ConfigManager.update_config(**values)
        from openagent.core.config import get_config

        return {"config": get_config().to_dict()}


async def _socket_alive(path: Path) -> bool:
    writer = None
    try:
        _, writer = await asyncio.open_unix_connection(path=str(path))
        return True
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return False
    finally:
        if writer is not None:
            writer.close()


async def _close_provider(provider: Any) -> None:
    closer = getattr(provider, "close", None)
    if not callable(closer):
        return
    try:
        result = closer()
        if hasattr(result, "__await__"):
            await result
    except Exception:
        pass


async def serve(settings: ServiceSettings | None = None) -> AgentService:
    """Start the service and return it (the caller keeps it running)."""
    service = AgentService(settings)
    await service.start()
    return service


__all__ = [
    "AgentService",
    "Connection",
    "DEFAULT_MAX_TURNS",
    "DEFAULT_PERMISSION_TIMEOUT",
    "PERMISSION_MODES",
    "SERVER_VERSION",
    "ServiceSettings",
    "serve",
]
