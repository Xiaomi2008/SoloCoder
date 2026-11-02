"""``openagent serve`` and ``openagent tui``.

The TUI is not a toy: it is the headless client the Phase 2 gate is written
against. If a run streams, cancels, answers a permission prompt, and survives a
reconnect here, the socket contract works and the SwiftUI shell is a renderer,
not a debugging session.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openagent.cli import blocks, style
from openagent.cli.render import Renderer, answer
from openagent.cli.style import bold, cyan, dim, red, yellow
from openagent.runtime.events import RunCancelled, RunCompleted, RunFailed
from openagent.service.client import ServiceClient, ServiceClosed
from openagent.service.protocol import PROTOCOL_VERSION, RpcError
from openagent.service.server import ServiceSettings, serve

if TYPE_CHECKING:
    from openagent.cli.input import Input, Keys


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
    sp.add_argument(
        "--browser-use",
        action="store_true",
        help="expose the browser tools (browser_navigate, browser_click, ...): the model's own Chromium window",
    )
    sp.add_argument(
        "--browser-profile",
        nargs="?",
        const="~/.openagent/browser-profile",
        default=None,
        metavar="DIR",
        help="keep the browser's profile (sign-ins) in DIR between runs (default DIR: ~/.openagent/browser-profile)",
    )
    sp.add_argument(
        "--browser-channel",
        choices=("chromium", "chrome"),
        default=None,
        help="the browser to drive: Playwright's Chromium (default) or your installed Google Chrome",
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
        browser_use=args.browser_use,
        browser_profile=args.browser_profile,
        browser_channel=args.browser_channel,
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


class _Terminal:
    """What one TUI connection draws with and reads from."""

    def __init__(self, live: bool, working_dir: str, input_: Input) -> None:
        self.out = Renderer(live, working_dir)
        self.input = input_
        self.keys: Keys | None = None

    async def ask(self, event: Any) -> tuple[bool, str]:
        """Ask a human. The question itself is drawn when the event is rendered."""
        from openagent.cli.input import ask_menu

        if self.keys is not None:
            return await ask_menu(self.keys)
        print(f"{yellow('allow')} [y/n/a]? ", end="", flush=True)
        return answer(await self.input.answer_line())


async def _run_once(client: ServiceClient, args: argparse.Namespace, prompt: str, term: _Terminal) -> int:
    from openagent.cli.input import ESC, Keys
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

    out = term.out
    run_id = started["run_id"]
    tasks: set[asyncio.Task[Any]] = set()

    def interrupt() -> None:
        task = asyncio.create_task(client.cancel(run_id))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    loop = asyncio.get_running_loop()
    term.keys = Keys(lambda key: interrupt() if key == ESC else None) if out.live else None
    keys = term.keys
    loop.add_signal_handler(signal.SIGINT, lambda: keys.handler(ESC) if keys else interrupt())

    async def tick() -> None:
        while True:
            await asyncio.sleep(0.08)
            out.tick()

    ticker = asyncio.create_task(tick()) if out.live else None
    try:
        async for event in client.events():
            out.event(event)
            if isinstance(event, RunCompleted):
                return 0
            if isinstance(event, (RunFailed, RunCancelled)):
                return 1
        return 1
    finally:
        if ticker is not None:
            ticker.cancel()
        loop.remove_signal_handler(signal.SIGINT)
        if keys is not None:
            keys.stop()
        term.keys = None


async def handle_tui(args: argparse.Namespace) -> int:
    # Here, not at the top: the app's sidecar runs `serve` and ships no line editor.
    from openagent.cli.input import Input, Interrupted
    from openagent.core.config import ConfigManager

    live = style.live_terminal() and not args.prompt
    history = None
    if style.interactive_input() and not args.prompt:
        history = ConfigManager.get_config_path().parent / "cli_history_py"
    term = _Terminal(live, str(Path(args.working_dir or Path.cwd()).resolve()), Input(history))
    try:
        client = await ServiceClient.connect(
            args.socket, client="openagent-tui", permission_handler=term.ask
        )
    except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
        print(
            red(f"cannot reach the service: {exc}\n")
            + dim("start it with: openagent serve"),
            file=sys.stderr,
        )
        return 2

    try:
        server = str(client.info.get("server"))
        protocol = client.info.get("protocol_version")
        if live:
            rows = [("server", server), ("protocol", str(protocol))]
            for row in blocks.header_box("openagent", rows, style.width()):
                print(row)
        else:
            print(f"{bold('openagent')} · {cyan(server)} · protocol {protocol}")
        if args.prompt:
            return await _run_once(client, args, args.prompt, term)

        print(dim("type a prompt; Ctrl-D to quit\n"))
        prompt = "❯ " if term.input.is_editor else "you › "
        while True:
            try:
                line = await term.input.read(prompt)
            except Interrupted:
                continue
            if line is None:
                print()
                return 0
            if not line.strip():
                continue
            status = await _run_once(client, args, line, term)
            usage = term.out.usage
            if usage and usage[1]:
                print(f"\n{dim(f'  context {blocks.percent(usage[0], usage[1])}%')}\n")
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
