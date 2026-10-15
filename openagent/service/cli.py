"""``openagent serve`` and ``openagent tui``.

The TUI is not a toy: it is the headless client the Phase 2 gate is written
against. If a run streams, cancels, answers a permission prompt, and survives a
reconnect here, the socket contract works and the SwiftUI shell is a renderer,
not a debugging session.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from openagent.core.display import bold, cyan, dim, green, red, yellow
from openagent.runtime.events import (
    ContextCompactionCompleted,
    ContextCompactionStarted,
    MessageCompleted,
    MessageDelta,
    MessageFailed,
    PermissionResolved,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
)
from openagent.service.client import ServiceClient, ServiceClosed
from openagent.service.protocol import PROTOCOL_VERSION, RpcError
from openagent.service.server import ServiceSettings, serve


def add_subcommands(subparsers: Any) -> None:
    """Register ``serve`` and ``tui`` on an existing subparser object."""

    sp = subparsers.add_parser(
        "serve",
        help="Run the agent service on a Unix socket (the Mac app's sidecar)",
        description=(
            "Serves NDJSON JSON-RPC over a Unix socket. One connection runs one "
            "run at a time. See dev_docs/agent-service-protocol.md."
        ),
    )
    sp.add_argument("--socket", default=None, help="socket path (default: ~/.openagent/agent.sock)")
    sp.add_argument(
        "--root",
        action="append",
        default=[],
        metavar="DIR",
        help="directory runs may be scoped to; repeatable. Default: any absolute path",
    )
    sp.add_argument(
        "--permission-timeout",
        type=float,
        default=60.0,
        help="seconds to wait for a permission answer before refusing (default: 60)",
    )
    sp.add_argument("--max-turns", type=int, default=100, help="default turn budget per run")
    sp.add_argument(
        "--computer-use",
        action="store_true",
        help="expose the macOS GUI tools (screenshot, click, ...) to the model",
    )
    sp.add_argument("--quiet", action="store_true", help="do not print the startup banner")
    sp.set_defaults(handler=handle_serve)

    tp = subparsers.add_parser(
        "tui",
        help="Headless client: drive a run over the socket and render its events",
    )
    tp.add_argument("--socket", default=None, help="socket path (default: ~/.openagent/agent.sock)")
    tp.add_argument("-p", "--prompt", default=None, help="run this prompt and exit")
    tp.add_argument("-w", "--working-dir", default=None, help="project directory for the run")
    tp.add_argument("-m", "--model", default=None, help="model key, e.g. lmstudio/qwen3-coder-32b")
    tp.add_argument(
        "--permission-mode",
        choices=("ask", "allow_readonly", "allow_all"),
        default="ask",
        help="ask (default) prompts per risky tool; allow_readonly refuses them",
    )
    tp.add_argument("--max-turns", type=int, default=None, help="turn budget for the run")
    tp.add_argument("--session", default=None, help="resume a stored session id")
    tp.set_defaults(handler=handle_tui)


# ------------------------------------------------------------------- serve


async def handle_serve(args: argparse.Namespace) -> int:
    settings = ServiceSettings(
        socket_path=Path(args.socket) if args.socket else ServiceSettings().socket_path,
        roots=tuple(args.root or ()),
        permission_timeout=args.permission_timeout,
        max_turns=args.max_turns,
        computer_use=args.computer_use,
    )
    service = await serve(settings)
    # A sidecar whose app was killed must not keep serving. The parent holds a
    # pipe open on our stdin; when it dies the read EOFs and the service stops.
    if service.install_stdin_watchdog():
        print(dim("watching stdin for parent death"), flush=True)
    # The polite path. The app's `stop()` sends SIGTERM, and without a handler
    # the default action kills the process before the `finally` below can drain
    # its clients, close the socket and release its MCP subprocesses.
    service.install_signal_handlers()
    if not args.quiet:
        tools = service._tools()
        print(f"{bold('openagent')} service listening on {cyan(str(settings.socket_path))}")
        print(
            f"  protocol {PROTOCOL_VERSION} · {len(tools)} tools · "
            f"roots: {', '.join(settings.roots) if settings.roots else dim('any absolute path')}"
        )
        print(f"  {dim('Ctrl-C to stop')}\n")
    try:
        await service.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        await service.stop()
    return 0


# --------------------------------------------------------------------- tui


def _render(event: Any) -> None:
    """One event, one block of terminal output. No control of the cursor."""
    if isinstance(event, RunStarted):
        print(dim(f"· run {event.run_id}"))
    elif isinstance(event, MessageDelta):
        print(event.delta, end="", flush=True)
    elif isinstance(event, MessageCompleted):
        if event.output_text.strip():
            print()
    elif isinstance(event, MessageFailed):
        print(red(f"  ✗ {event.error}"))
    elif isinstance(event, ToolCallStarted):
        args = ", ".join(f"{k}={_short(v)}" for k, v in event.arguments.items())
        print(f"  {bold('➜')} {cyan(event.tool_name)}({dim(args)})")
    elif isinstance(event, ToolCallCompleted):
        print(f"    {green('⎿')} {dim(_short(event.result, 300))}")
    elif isinstance(event, ToolCallFailed):
        print(f"    {red('⎿')} {red(_short(event.error, 300))}")
    elif isinstance(event, PermissionResolved):
        mark = green("allowed") if event.granted else red("denied")
        print(dim(f"  · permission {mark} ({event.scope})"))
    elif isinstance(event, ContextCompactionStarted):
        print(dim("  · compacting context…"))
    elif isinstance(event, ContextCompactionCompleted):
        print(dim("  · context compacted"))
    elif isinstance(event, RunCompleted):
        reason = event.result.stop_reason
        if reason != "end_turn":
            print(yellow(f"· run stopped: {reason}"))
    elif isinstance(event, RunFailed):
        print(red(f"· run failed: {event.error}"))
    elif isinstance(event, RunCancelled):
        print(yellow(f"· run cancelled: {event.reason}"))


def _short(value: Any, limit: int = 80) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text.replace("\n", " ⏎ ")
    return text if len(text) <= limit else f"{text[:limit]}… ({len(text)} chars)"


async def _ask(event: Any) -> tuple[bool, str]:
    """Ask a human. Runs in a thread: ``input`` blocks the event loop otherwise."""
    print(f"\n{yellow('⚠')} {bold(event.tool_name)} wants to run:")
    for key, value in event.arguments.items():
        print(f"    {key}: {dim(_short(value, 400))}")
    print(f"    {dim(event.reason)}")
    answer = await asyncio.to_thread(
        input, f"{yellow('allow')} [y/n/a(lways for session)]? "
    )
    answer = answer.strip().lower()
    if answer in ("y", "yes"):
        return True, "once"
    if answer in ("a", "always", "session"):
        return True, "session"
    return False, "once"


async def _run_once(client: ServiceClient, args: argparse.Namespace, prompt: str) -> int:
    from openagent.service.scope import validate_working_dir

    working_dir = args.working_dir or str(Path.cwd())
    try:
        scope = validate_working_dir(working_dir)
    except RpcError as exc:
        print(red(f"{exc.name}: {exc.message}"), file=sys.stderr)
        return 2

    started = await client.start_run(
        prompt,
        scope,
        model=args.model,
        session_id=args.session,
        max_turns=args.max_turns,
        permission_mode=args.permission_mode,
    )
    print(dim(f"· model {started['model']} · session {started['session_id']}"))

    async for event in client.events():
        _render(event)
        if isinstance(event, RunCompleted):
            return 0
        if isinstance(event, (RunFailed, RunCancelled)):
            return 1
    return 1


async def handle_tui(args: argparse.Namespace) -> int:
    try:
        client = await ServiceClient.connect(
            args.socket, client="openagent-tui", permission_handler=_ask
        )
    except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
        print(
            red(f"cannot reach the service: {exc}\n")
            + dim("start it with: openagent serve"),
            file=sys.stderr,
        )
        return 2

    try:
        print(f"{bold('openagent')} · {cyan(str(client.info.get('server')))} · protocol {client.info.get('protocol_version')}")
        if args.prompt:
            return await _run_once(client, args, args.prompt)

        print(dim("type a prompt; Ctrl-D to quit\n"))
        while True:
            try:
                line = await asyncio.to_thread(input, f"{green('you')} › ")
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not line.strip():
                continue
            status = await _run_once(client, args, line)
            if status == 2:
                return 2
    except RpcError as exc:
        print(red(f"{exc.name}: {exc.message}"), file=sys.stderr)
        return 2
    except ServiceClosed as exc:
        print(yellow(f"service closed the connection: {exc}"), file=sys.stderr)
        return 2
    finally:
        await client.close()


__all__ = ["add_subcommands", "handle_serve", "handle_tui"]
