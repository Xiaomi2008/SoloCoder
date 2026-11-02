"""The interactive session: the engine in-process, a prompt, and the quick
commands. ``AgentRunner`` does the work and reports it as events; this module
and ``render`` decide how it looks.

Commands: ``/help``, ``/list [dir]``, ``/read <file>``, ``/todo``,
``/image [file]``, ``/model [name]``, ``/context``, ``/compact``, ``/clear``,
``/quit``; ``! <command>`` runs a shell command. Esc or Ctrl-C cancels the run
in flight rather than ending the session, risky tools ask first, and the
conversation is saved as a session the app can open (``--session`` resumes one).
"""

from __future__ import annotations

import asyncio
import base64
import signal
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from openagent.cli import blocks, style
from openagent.cli.input import COMMANDS, ESC, Input, Interrupted, Keys, ask_menu
from openagent.cli.render import Renderer, answer
from openagent.cli.style import cyan, dim, green, red, yellow
from openagent.runtime.events import RunCompleted
from openagent.runtime.permissions import PermissionDecision, PermissionRequest, gate_for_mode
from openagent.runtime.runner import AgentRunner, _fixed_overhead
from openagent.service import sessions

SwitchModel = Callable[[str], Awaitable[str]]


async def _shell(command: str, cwd: str) -> str:
    done = await asyncio.create_subprocess_shell(
        command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    out, _ = await done.communicate()
    return out.decode(errors="replace").rstrip("\n")


class Chat:
    def __init__(
        self,
        coder: Any,
        *,
        live: bool,
        input_: Input,
        model_name: str,
        permission_mode: str = "ask",
        session_id: str | None = None,
        switch_model: SwitchModel | None = None,
        compact_threshold: float = 0.8,
        disable_compaction: bool = False,
    ) -> None:
        self.coder = coder
        self.live = live
        self.input = input_
        self.model_name = model_name
        self.permission_mode = permission_mode
        self.session_id = session_id or sessions.new_session_id()
        self.switch_model = switch_model
        self.compact_threshold = compact_threshold
        self.disable_compaction = disable_compaction
        self.out = Renderer(live, coder.working_dir)
        self.turns = 0
        self._keys: Keys | None = None
        self._gate = gate_for_mode(permission_mode, callback=self._decide)  # type: ignore[arg-type]

    def window(self) -> int | None:
        return self.coder.loop_defaults.get("max_context_tokens") or None

    def used_tokens(self) -> int:
        session = self.coder.session
        return session.token_count + _fixed_overhead(session.system_prompt, self.coder.tool_registry.definitions)

    def context_short(self) -> str:
        used, window = self.used_tokens(), self.window()
        return f"context {blocks.percent(used, window)}%" if window else f"context {used:,}"

    def context_rows(self) -> list[str]:
        used, window = self.used_tokens(), self.window()
        if not window:
            return [f"{green('⎿')} context {used:,} tokens"]
        return [
            f"{green('⎿')} context {used:,} / {window:,} tokens ({blocks.percent(used, window)}%)",
            f"  {blocks.context_bar(used, window)}",
        ]

    def save(self) -> None:
        try:
            sessions.save(self.coder.session, self.session_id)
        except OSError as exc:
            print(red(f"could not save the session: {exc}"))

    async def _decide(self, request: PermissionRequest) -> PermissionDecision:
        if self._keys is not None:
            granted, scope = await ask_menu(self._keys)
        else:
            print(f"{yellow('allow')} [y/n/a]? ", end="", flush=True)
            granted, scope = answer(await self.input.answer_line())
        return PermissionDecision(granted=granted, scope=scope, reason="" if granted else "the user refused")

    async def turn(self, prompt: str, images: list[str] | None = None) -> Any:
        """One prompt to its terminal event, with events rendered as they come."""
        coder = self.coder
        coder.tool_context.enforce_scope = self.permission_mode == "allow_all"
        coder.tool_context.outputs_dir = str(sessions.outputs_dir(self.session_id))
        runner = AgentRunner(
            coder.provider,
            session=coder.session,
            tools=coder.tool_registry,
            tool_context=coder.tool_context,
            max_turns=coder.max_turns,
            permission_gate=self._gate,
            context_window=self.window(),
        )
        last: list[Any] = []

        async def drain() -> None:
            stream = runner.stream(
                prompt,
                images=images,
                compact_threshold=self.compact_threshold,
                disable_compaction=self.disable_compaction,
            )
            async for event in stream:
                self.out.event(event)
                if event.type in ("run_completed", "run_failed", "run_cancelled"):
                    last.append(event)

        task = asyncio.create_task(drain())

        def interrupt() -> None:
            if not runner.cancelled and not task.done():
                runner.cancel("interrupted by user")
                task.cancel()

        def on_key(key: str) -> None:
            if key == ESC:
                interrupt()

        loop = asyncio.get_running_loop()
        self._keys = Keys(on_key) if self.live else None
        keys = self._keys

        def on_sigint() -> None:
            # A question on screen is refused; otherwise the run stops.
            if keys is not None:
                keys.handler(ESC)
            else:
                interrupt()

        loop.add_signal_handler(signal.SIGINT, on_sigint)
        ticker = asyncio.create_task(self._tick()) if self.live else None
        try:
            await asyncio.wait({task})
        finally:
            if ticker is not None:
                ticker.cancel()
            loop.remove_signal_handler(signal.SIGINT)
            if keys is not None:
                keys.stop()
            self._keys = None
        self.save()
        self.turns += 1
        return last[-1] if last else None

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(0.08)
            self.out.tick()

    async def command(self, line: str) -> bool:
        """Run a slash command. False ends the session."""
        cmd, _, arg = line.partition(" ")
        arg = arg.strip()
        cmd = cmd.lower()
        cwd = self.coder.working_dir
        if cmd in ("/help", "/?"):
            for name, about in COMMANDS:
                print(f"  {cyan(f'{name:<9}')}  {dim(about)}")
            shell = "! <cmd>"
            print(f"  {cyan(f'{shell:<9}')}  {dim('Run a shell command')}")
            print(dim("  Esc interrupts a run · Ctrl-J adds a line · Ctrl-D quits"))
        elif cmd in ("/list", "/ls"):
            print(await _shell(f"ls -la {arg}" if arg else "ls -la", cwd))
        elif cmd == "/read":
            if not arg:
                print(f"\n{green('⎿')} Usage: /read <filepath>")
            else:
                try:
                    content = (Path(cwd) / arg).read_text(errors="replace")
                    print(f"\n{dim(f'File: {arg}')}\n{content}")
                except OSError as exc:
                    print(f"\n{red('⎿')} Error reading file: {exc}")
        elif cmd == "/todo":
            from openagent.tools import todo_list

            print(f"\n{green('⎿')} {todo_list()}")
        elif cmd == "/image":
            await self._image(arg)
        elif cmd == "/model":
            await self._model(arg)
        elif cmd == "/context":
            print()
            for row in self.context_rows():
                print(row)
        elif cmd == "/compact":
            try:
                summary = await self.coder.session.compact_context(
                    provider=self.coder.provider, keep_recent=5, summary_type="detailed"
                )
            except Exception as exc:  # any provider failure: the session goes on
                print(f"\n{red('⎿')} Error during compaction: {exc}")
            else:
                self.save()
                print(f"\n{green('⎿')} Context compacted!")
                print(dim("Summary preview:"))
                print(dim(f"{summary[:300]}..." if len(summary) > 300 else summary))
        elif cmd in ("/clear", "/reset"):
            self.coder.session.clear()
            self.session_id = sessions.new_session_id()
            self.turns = 0
            print(f"\n{green('⎿')} Conversation history cleared.")
        elif cmd in ("/quit", "/exit"):
            self.farewell()
            return False
        else:
            print(f"{red('⎿')} Unknown command: {cmd}. /help lists them.")
        return True

    async def _image(self, arg: str) -> None:
        if arg:
            try:
                image = base64.b64encode((Path(self.coder.working_dir) / arg).read_bytes()).decode()
            except OSError as exc:
                print(f"\n{red('⎿')} Error loading image: {exc}")
                return
            await self.turn("Analyze this image.", [image])
            return
        from openagent.tools.computer_use import screenshot

        print(f"\n{green('⎿')} Taking screenshot...")
        image = await screenshot(return_base64=True)
        if image:
            await self.turn("Analyze this screenshot and describe what you see.", [image])

    async def _model(self, arg: str) -> None:
        if not arg:
            print(f"\n{green('⎿')} Current model: {self.model_name}")
            print(f"{green('⎿')} Provider: {type(self.coder.provider).__name__}")
            return
        if self.switch_model is None:
            print(f"\n{red('⎿')} This session cannot change its model.")
            return
        try:
            self.model_name = await self.switch_model(arg)
        except Exception as exc:  # a bad name or missing key: keep the current model
            print(f"\n{red('⎿')} Error switching model: {exc}")
        else:
            print(f"\n{green('⎿')} Switched to {self.model_name}")

    def farewell(self) -> None:
        print("Goodbye!")
        if len(self.coder.session):
            print(dim(f"Resume with: openagent --session {self.session_id}"))

    def header(self) -> None:
        model = self.model_name
        window = self.window()
        if window:
            model += f" · {blocks.compact_count(window)} context"
        rows = [("model", model), ("cwd", blocks.tilde(self.coder.working_dir))]
        for row in blocks.header_box("openagent", rows, style.width()):
            print(row)
        print(dim("/help for commands · ! runs a shell command · Ctrl-D quits") + "\n")

    def footer(self) -> None:
        lead = "\n" if self.live else ""
        print(f"{lead}{dim(f'  {self.model_name} · {self.context_short()} · turn {self.turns}')}\n")

    async def loop(self, image: str | None = None) -> int:
        if self.live:
            self.header()
        else:
            print(dim(f"session {self.session_id} · {self.coder.working_dir} · /quit to exit"))
        if image:
            await self.turn("Analyze this image.", [image])
        prompt = "❯ " if self.input.is_editor else "> "
        while True:
            try:
                line = await self.input.read(prompt)
            except Interrupted:
                continue
            if line is None:
                print()
                self.farewell()
                return 0
            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if not await self.command(line):
                    return 0
                continue
            if line.startswith("!"):
                command = line[1:].strip()
                if command:
                    print(await _shell(command, self.coder.working_dir))
                else:
                    print(f"\n{green('⎿')} Usage: ! <command>")
                continue
            if line.lower() in ("quit", "exit"):
                self.farewell()
                return 0
            await self.turn(line)
            self.footer()

    async def once(self, prompt: str, image: str | None = None) -> int:
        """Run this prompt and exit, instead of a session."""
        last = await self.turn(prompt, [image] if image else None)
        print(dim(f"{self.model_name} · {self.context_short()} · session {self.session_id}"))
        return 0 if isinstance(last, RunCompleted) else 1


__all__ = ["Chat"]
