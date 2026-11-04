"""A terminal front end for the super-agent: a line is a Trigger.

    python -m openagent.apps.superagent --model qwen3.5-35b-a3b \
        --base-url http://localhost:1234/v1

Everything typed here is one event from one person ("owner" unless you say
otherwise). A reply printed with a purpose is what that person would see; a
line with no purpose is internal and stays internal. The commands are the
panel, reduced to what a terminal can do:

    :thread <id>        the next lines are follow-ups on that thread
    :tickets            every ticket with its stage
    :log                what the host did, in order
    :quit               leave
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import signal
import sys
from pathlib import Path

from openagent.provider.auth import AuthRequired
from openagent.provider.registry import ProviderRegistry

from .domain import Policy
from .router import SuperAgentRouter
from .runtime import SuperAgentRuntime
from .terminal import Terminal
from .execution import Execution
from .worktrees import Worktrees
from .service_runtime import ServiceRuntime
from .webhooks import create_app
from .github import GitHubIssues
from .storage import StateStore, attach_store, DurableBoard

DEFAULT_MODEL = "qwen3.5-35b-a3b"
DEFAULT_BASE_URL = "http://localhost:1234/v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m openagent.apps.superagent",
        description="Run a super-agent deployment from a terminal.",
    )
    parser.add_argument("--provider", default="lmstudio",
                        help="registered provider name, e.g. lmstudio, openai_oauth, taopc")
    parser.add_argument("--assignment-timeout", type=float, default=600,
                        help="worker wall-clock deadline in seconds (default: 600)")
    parser.add_argument("--router-timeout", type=float, default=120,
                        help="router wall-clock deadline in seconds (default: 120)")
    parser.add_argument("--max-concurrent", type=int, default=1,
                        help="simultaneous worker assignments (default: 1)")
    parser.add_argument("--heartbeat-timeout", type=float, default=120,
                        help="seconds of worker silence before the stall watchdog kills it")
    parser.add_argument("--auto-retries", type=int, default=0,
                        help="host-detected failure retries (default: 0); worker-declared failures never retry")
    parser.add_argument("--retry-backoff", type=float, default=30,
                        help="base exponential retry delay in seconds")
    parser.add_argument("--execution", choices=("deny", "ask", "trusted"), default="deny",
                        help="shell policy; trusted opts into arbitrary execution")
    parser.add_argument("--sandbox", choices=("host", "docker"), default="host")
    parser.add_argument("--sandbox-image", default="python:3.12-slim")
    parser.add_argument("--verify-command", action="append", default=[],
                        help="operator-controlled completion check; repeat for multiple checks")
    parser.add_argument("--repo", help="repository root for new ticket worktrees")
    parser.add_argument("--worker-turns", type=int, default=12,
                        help="model turns one Worker assignment may take (default: 12)")
    parser.add_argument("--page-check", action="store_true",
                        help="let Workers run pages from their ticket directory in an offline headless browser")
    parser.add_argument("--desktop-app", action="append", default=[], metavar="NAME",
                        help="let Workers control this one desktop app through agent-desktop; repeatable. "
                             "This drives the real desktop and can take focus from you")
    parser.add_argument("--service", action="store_true",
                        help="headless mode: poll workspace/service/inbox for JSON events")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--notify-hook", help="command receiving one JSON record on stdin per notification")
    parser.add_argument("--http-bind", help="bind address for signed HTTP intake, e.g. 127.0.0.1")
    parser.add_argument("--http-port", type=int, default=8766)
    parser.add_argument("--webhook-secret-file",
                        help="file containing the HMAC webhook secret (required with --http-bind)")
    parser.add_argument("--dashboard-token-file",
                        help="file containing the dashboard bearer token; dashboard is served "
                             "on the same port and is disabled without it")
    parser.add_argument("--github-repo", help='poll GitHub Issues, e.g. "owner/name"')
    parser.add_argument("--github-label", default="agent",
                        help="only issues with this label become work (default: agent)")
    parser.add_argument("--github-token-file",
                        help="GitHub token file; defaults to GITHUB_TOKEN environment variable")
    parser.add_argument("--model", help="model ID (defaults to the selected provider's default)")
    parser.add_argument("--base-url", help="local server URL; not allowed for subscription auth")
    parser.add_argument(
        "--workspace",
        default=".superagent-work",
        help="where ticket directories live (default: ./.superagent-work)",
    )
    parser.add_argument(
        "--accept-mode",
        choices=("off", "observe", "auto"),
        default="observe",
        help="inferred accepts: off refuses, observe logs only (default), auto launches",
    )
    parser.add_argument(
        "--allowed",
        nargs="*",
        default=["owner"],
        help="who may start work (default: owner)",
    )
    parser.add_argument(
        "--actor",
        default="owner",
        help="the person typing at this terminal (default: owner)",
    )
    return parser


async def _build(args: argparse.Namespace) -> SuperAgentRuntime:
    registry = ProviderRegistry()
    if args.provider == "openai_oauth" and args.base_url:
        raise ValueError("--base-url is only supported for lmstudio, not subscription auth")
    try:
        registry.endpoint(args.provider)
    except KeyError as error:
        raise ValueError(str(error)) from None
    model = args.model or (DEFAULT_MODEL if args.provider == "lmstudio" else None)
    base_url = (args.base_url or DEFAULT_BASE_URL) if args.provider == "lmstudio" else None
    ref = registry.ref(args.provider, model, base_url=base_url)
    provider = await registry.build_async(ref)
    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    policy = Policy(accept_mode=args.accept_mode, allowed=frozenset(args.allowed))
    store = StateStore(str(workspace))
    board = store.load() if store.path.exists() else DurableBoard(workspace=str(workspace), policy=policy)
    if Path(board.workspace).resolve() != workspace:
        raise ValueError("Stored workspace moved; use its original directory or a fresh workspace")
    board.policy = policy
    attach_store(board, store)
    store.save(board)
    board.verification_required = True
    execution = Execution(mode=args.execution, sandbox=args.sandbox, image=args.sandbox_image)
    worktrees = Worktrees(args.repo) if args.repo else None
    if worktrees:
        await worktrees.validate()
    router = SuperAgentRouter(provider=provider, board=board)
    return SuperAgentRuntime(board=board, router=router, store=store,
                             assignment_timeout=args.assignment_timeout,
                             router_timeout=args.router_timeout, execution=execution,
                             max_concurrent=args.max_concurrent,
                             heartbeat_timeout=args.heartbeat_timeout,
                             auto_retries=args.auto_retries,
                             retry_backoff=args.retry_backoff,
                             verify_commands=args.verify_command, worktrees=worktrees,
                             worker_options={'max_turns': args.worker_turns, 'page_check': args.page_check,
                                             'desktop_apps': tuple(args.desktop_app)})


async def _repl(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    # A snapshot store is single-writer. Refuse two hosts on the same workspace.
    with (workspace / ".host.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another super-agent host owns this workspace") from None
        runtime = await _build(args)
        try:
            if args.service:
                await _service(runtime, args)
            else:
                await _interactive(runtime, args)
        finally:
            runtime.checkpoint()
            close = getattr(runtime.router.provider, "close", None)
            if close is not None:
                await close()


async def _service(runtime: SuperAgentRuntime, args: argparse.Namespace) -> None:
    service = ServiceRuntime(runtime, runtime.board.workspace, poll_interval=args.poll_interval,
                             hook=args.notify_hook, actor=args.actor)
    server = None
    github = None
    if args.http_bind:
        if not args.webhook_secret_file:
            raise ValueError("--http-bind requires --webhook-secret-file")
        secret = Path(args.webhook_secret_file).read_bytes().strip()
        if len(secret) < 16:
            raise ValueError("webhook secret must be at least 16 bytes")
        token = None
        if args.dashboard_token_file:
            token = Path(args.dashboard_token_file).read_text().strip()
            if len(token) < 16:
                raise ValueError("dashboard token must be at least 16 characters")
        app = create_app(service, secret,
                         board_getter=lambda: runtime.board,
                         dashboard_token=token)
        import uvicorn
        config = uvicorn.Config(app, host=args.http_bind, port=args.http_port,
                                log_level="warning")
        server = uvicorn.Server(config)
        print(f'HTTP intake: http://{args.http_bind}:{args.http_port}/events '
              f'({"dashboard enabled" if token else "dashboard disabled"})')
    if args.github_repo:
        token = Path(args.github_token_file).read_text().strip() if args.github_token_file else None
        github = GitHubIssues(args.github_repo, args.github_label, token=token,
                              state_file=Path(runtime.board.workspace) / 'service' / 'github-seen.json')
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    print(f'super-agent service: {args.provider}/{runtime.router.provider.model}')
    print(f'Inbox: {service.inbox.inbox}  Outbox: {service.inbox.outbox}')
    print('No network listener. Drop JSON events into inbox/; Ctrl-C to stop.')
    server_task = asyncio.create_task(server.serve()) if server else None
    try:
        while not stop.is_set():
            try:
                await service.poll_once()
                if github is not None:
                    try:
                        for number in await github.poll(service):
                            print(f'GitHub issue #{number} queued')
                    except Exception as error:
                        service.emit({'type': 'connector_error', 'source': 'github',
                                      'error': f'{type(error).__name__}: {error}'})
            except Exception as error:
                service.emit({'type': 'service_error', 'error': f'{type(error).__name__}: {error}'})
            try:
                await asyncio.wait_for(stop.wait(), args.poll_interval)
            except TimeoutError:
                pass
    finally:
        if server_task is not None:
            server.should_exit = True
            await server_task
        runtime.checkpoint()


async def _interactive(runtime: SuperAgentRuntime, args: argparse.Namespace) -> None:
    board = runtime.board
    ui = Terminal()
    thread_id: str | None = None
    wake = asyncio.Event()
    wake.set()  # resume queued intake and relay persisted notices at startup

    async def scheduler():
        while True:
            await wake.wait()
            wake.clear()
            try:
                for reply in await runtime.run_pending():
                    ui.reply(reply)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                ui.message(f"Error: host / {type(error).__name__}: {error}")

    async def approval_notices():
        announced = set()
        while True:
            for request_id, request in runtime.execution.pending.copy().items():
                if request_id not in announced:
                    ui.heading(f"Execution approval / {request_id}")
                    ui.paragraph(f"Ticket: {request.ticket_id}  Directory: {request.directory}")
                    ui.paragraph(request.command)
                    ui.message(f":allow {request_id} or :deny {request_id} (one command only)")
                    announced.add(request_id)
            await asyncio.sleep(.1)

    runner = asyncio.create_task(scheduler())
    approvals = asyncio.create_task(approval_notices())
    ui.banner(runtime, args)
    try:
        while True:
            try:
                line = (await asyncio.to_thread(input, ui.prompt(thread_id))).strip()
            except EOFError:
                return
            if not line:
                continue
            if line in (":quit", ":q", ":exit"):
                return
            if line in (":help", ":h"):
                ui.help()
            elif line.startswith(":allow ") or line.startswith(":deny "):
                action, request_id = line.split(maxsplit=1)
                ui.message(runtime.execution.decide(request_id, action == ":allow"))
            elif line.startswith(":approve ") or line.startswith(":reject "):
                action, ticket_id = line.split(maxsplit=1)
                ui.message(runtime.review_decision(ticket_id, action == ":approve"))
                wake.set()
            elif line.startswith(":diff "):
                from .worktrees import Worktrees
                ticket = board.tickets.get(line.split(maxsplit=1)[1])
                if ticket is None or not ticket.repository:
                    ui.message("Error: no worktree for this ticket.")
                elif ticket.id in runtime._active:
                    ui.message("Error: wait for the active assignment before reviewing its diff.")
                else:
                    try:
                        ui.paragraph(await Worktrees(ticket.repository).review(ticket))
                    except Exception as error:
                        ui.message(f"Error: {error}")
            elif line == ":status":
                ui.heading("Workspace status")
                ui.dashboard(board)
            elif line == ":threads":
                ui.threads(board, thread_id)
            elif line == ":tickets":
                ui.tickets(board)
            elif line.startswith(":ticket "):
                ui.ticket(board, line.split(maxsplit=1)[1])
            elif line == ":log":
                ui.heading("Activity log")
                for entry in board.log_lines:
                    ui.paragraph(entry, kind="muted")
            elif line == ":thread" or line.startswith(":thread "):
                parts = line.split()
                candidate = parts[1] if len(parts) > 1 else None
                if candidate and candidate not in board.threads:
                    ui.message("Error: unknown thread. Use :threads to see IDs.")
                    continue
                thread_id = candidate
                ui.message(f"Follow-ups → {thread_id}" if thread_id else "New conversation per request")
            elif line.startswith(":stop "):
                ui.message(await runtime.stop(line.split(maxsplit=1)[1]))
            elif line.startswith(":retry "):
                ui.message(runtime.retry(line.split(maxsplit=1)[1]))
                wake.set()
            elif line == ":drain":
                # Useful for piped requests: wait until current queued work finishes.
                ui.message("Waiting for queued work…")
                wake.set()
                while wake.is_set() or runtime._lock.locked():
                    await asyncio.sleep(.05)
                ui.message("Queue drained.")
            elif line.startswith(":"):
                ui.message("Error: unknown command. Type :help.")
            else:
                trigger = board.submit(line, actor=args.actor, source="cli", thread_id=thread_id)
                runtime.checkpoint()
                ui.message(f"Queued {trigger.id} → {trigger.thread_id}  ·  :thread {trigger.thread_id} for follow-ups")
                wake.set()
    finally:
        runner.cancel()
        approvals.cancel()
        await asyncio.gather(runner, approvals, return_exceptions=True)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        asyncio.run(_repl(args))
    except AuthRequired as error:
        print(f"Sign-in required: {error}. Run: openagent auth login chatgpt", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
