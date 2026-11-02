"""Reading from a person: the line editor between runs, and single keys during
one. Pipes get plain line reads, so scripts drive the CLI the same way."""

from __future__ import annotations

import asyncio
import os
import select
import sys
import termios
import threading
from collections.abc import Callable
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

from openagent.cli.style import cyan, dim

#: The slash commands, for ``/help`` and completion.
COMMANDS = [
    ("/help", "Show the commands"),
    ("/list", "List files"),
    ("/read", "Show a file"),
    ("/image", "Analyze an image, or a screenshot"),
    ("/todo", "Show the task list"),
    ("/model", "Show or change the model"),
    ("/context", "Show context usage"),
    ("/compact", "Summarize the conversation to free context"),
    ("/clear", "Start a new conversation"),
    ("/quit", "Leave"),
]


def unfinished(text: str) -> bool:
    """More to come after a trailing backslash or inside a code fence?"""
    if text.endswith("\\"):
        return True
    return sum(1 for line in text.split("\n") if line.lstrip().startswith("```")) % 2 == 1


def tidy(text: str) -> str:
    """The backslash was only there to ask for another line."""
    return text.replace("\\\n", "\n").strip()


class _SlashCommands(Completer):
    def get_completions(self, document, complete_event):  # type: ignore[no-untyped-def]
        typed = document.text_before_cursor
        if not typed.startswith("/") or any(c.isspace() for c in typed):
            return
        for name, about in COMMANDS:
            if name.startswith(typed):
                yield Completion(f"{name} ", start_position=-len(typed), display=name, display_meta=about)


class Interrupted(Exception):
    """Ctrl-C at the prompt: the line is gone, the session is not."""


class Input:
    """``read`` returns a line, ``None`` at the end of input, or raises ``Interrupted``."""

    def __init__(self, history: Path | None) -> None:
        self.session: PromptSession[str] | None = None
        if history is None:
            return
        history.parent.mkdir(parents=True, exist_ok=True)
        history.touch(exist_ok=True)
        # Prompts can hold secrets; the history is only the user's to read.
        history.chmod(0o600)
        keys = KeyBindings()

        @keys.add("enter")
        def _enter(event) -> None:  # type: ignore[no-untyped-def]
            buffer = event.current_buffer
            if unfinished(buffer.text):
                buffer.insert_text("\n")
            else:
                buffer.validate_and_handle()

        @keys.add("c-j")
        @keys.add("escape", "enter")
        def _newline(event) -> None:  # type: ignore[no-untyped-def]
            event.current_buffer.insert_text("\n")

        self.session = PromptSession(
            history=FileHistory(str(history)),
            completer=_SlashCommands(),
            complete_while_typing=True,
            key_bindings=keys,
            multiline=True,
            prompt_continuation=lambda width, line, soft: " " * width,
        )

    @property
    def is_editor(self) -> bool:
        return self.session is not None

    async def read(self, prompt: str) -> str | None:
        if self.session is None:
            sys.stdout.write(prompt)
            sys.stdout.flush()
            line = await asyncio.to_thread(sys.stdin.readline)
            return line.rstrip("\r\n") if line else None
        try:
            return tidy(await self.session.prompt_async(ANSI(cyan(prompt))))
        except KeyboardInterrupt:
            raise Interrupted from None
        except EOFError:
            return None

    async def answer_line(self) -> str:
        """One answer read as a line, for pipes and ``-p``."""
        try:
            return await self.read("") or ""
        except Interrupted:
            return ""


ESC, UP, DOWN, ENTER = "esc", "up", "down", "enter"


def decode(data: bytes) -> list[str]:
    keys: list[str] = []
    i = 0
    while i < len(data):
        byte = data[i]
        if byte == 0x1B:
            lead = data[i + 1 : i + 2]
            final = data[i + 2 : i + 3]
            if lead in (b"[", b"O") and final == b"A":
                keys.append(UP)
                i += 2
            elif lead in (b"[", b"O") and final == b"B":
                keys.append(DOWN)
                i += 2
            elif lead in (b"[", b"O") and final:
                # Some other escape sequence: not a person pressing Esc.
                break
            else:
                keys.append(ESC)
        elif byte in (0x0D, 0x0A):
            keys.append(ENTER)
        elif 0x21 <= byte <= 0x7E:
            keys.append(chr(byte))
        i += 1
    return keys


class Keys:
    """Keys during a run. The terminal stays cooked for output and Ctrl-C still
    signals; only line buffering and echo are off, until ``stop``."""

    def __init__(self, handler: Callable[[str], None]) -> None:
        self.handler = handler
        self._loop = asyncio.get_running_loop()
        self._fd = sys.stdin.fileno()
        self._saved = termios.tcgetattr(self._fd)
        raw = termios.tcgetattr(self._fd)
        raw[3] &= ~(termios.ICANON | termios.ECHO)
        raw[6][termios.VMIN] = 1
        raw[6][termios.VTIME] = 0
        termios.tcsetattr(self._fd, termios.TCSANOW, raw)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _dispatch(self, key: str) -> None:
        self.handler(key)

    def _read(self) -> None:
        while not self._stop.is_set():
            ready, _, _ = select.select([self._fd], [], [], 0.05)
            if not ready:
                continue
            data = os.read(self._fd, 64)
            if not data:
                return
            for key in decode(data):
                self._loop.call_soon_threadsafe(self._dispatch, key)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
        termios.tcsetattr(self._fd, termios.TCSANOW, self._saved)


MENU = ("Yes", "Yes, for this session", "No")


def _draw_menu(selected: int, redraw: bool) -> None:
    if redraw:
        sys.stdout.write(f"\x1b[{len(MENU)}A")
    for i, row in enumerate(MENU):
        line = f"{cyan('❯')} {row}" if i == selected else dim(f"  {row}")
        sys.stdout.write(f"\r\x1b[2K{line}\n")
    sys.stdout.flush()


async def ask_menu(keys: Keys) -> tuple[bool, str]:
    """The permission menu: arrows and Enter, or ``y``, ``a``, ``n``. Esc refuses,
    and so does Ctrl-C, which the caller delivers by calling ``keys.handler(ESC)``."""
    picked: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    state = {"selected": 0}
    previous = keys.handler

    def choose(index: int) -> None:
        if not picked.done():
            picked.set_result(index)

    def on_key(key: str) -> None:
        if key == UP:
            state["selected"] = (state["selected"] - 1) % len(MENU)
        elif key == DOWN:
            state["selected"] = (state["selected"] + 1) % len(MENU)
        elif key == ENTER:
            choose(state["selected"])
            return
        elif key in ("y", "Y"):
            choose(0)
            return
        elif key in ("a", "A"):
            choose(1)
            return
        elif key in ("n", "N", ESC):
            choose(2)
            return
        else:
            return
        if not picked.done():
            _draw_menu(state["selected"], True)

    keys.handler = on_key
    _draw_menu(0, False)
    try:
        index = await picked
    finally:
        keys.handler = previous
    sys.stdout.write(f"\x1b[{len(MENU)}A\x1b[J")
    sys.stdout.flush()
    return (True, "once") if index == 0 else (True, "session") if index == 1 else (False, "once")
