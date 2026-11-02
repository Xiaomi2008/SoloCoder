"""Terminal output for a run. Live mode keeps one in-place line at the bottom
(the spinner or the row being written); plain mode only appends. Shared by
``tui`` and the interactive session."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any

from openagent.cli import blocks, style
from openagent.cli.markdown import Markdown
from openagent.cli.style import cyan, dim, green, red, truncate, yellow
from openagent.runtime.events import (
    ContextCompactionCompleted,
    ContextCompactionFailed,
    ContextCompactionStarted,
    ContextUsage,
    MessageCompleted,
    MessageDelta,
    MessageFailed,
    PermissionRequested,
    PermissionResolved,
    ReasoningDelta,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
)

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
GROUPED = ("read", "glob", "grep")


@dataclass
class _Group:
    """A run of consecutive read-only calls drawn as one block."""

    tool: str
    items: list[str]
    #: Rows on screen, so they can be drawn over.
    rows: int


class Renderer:
    def __init__(self, live: bool, cwd: str) -> None:
        self.live = live
        self.cwd = cwd
        self.md = Markdown()
        self.running = False
        self.started = time.monotonic()
        self.frame = 0
        self.chars = 0
        self.label = "Thinking"
        self.calls: dict[str, tuple[str, Any]] = {}
        #: The call whose header is the last thing printed.
        self.shown_call: str | None = None
        self.group: _Group | None = None
        self.wrote_any = False
        self.in_text = False
        #: The latest usage the run reported: (used, window).
        self.usage: tuple[int, int | None] | None = None

    def _width(self) -> int:
        return style.width() if self.live else 100

    def commit(self, rows: list[str]) -> None:
        """Permanent rows above the in-place line."""
        if self.live:
            sys.stdout.write("\r\x1b[2K")
        for row in rows:
            sys.stdout.write(f"{row}\n")
        if rows:
            self.wrote_any = True
        self._redraw()

    def _redraw(self) -> None:
        if self.live and self.running:
            width = self._width()
            line = self.md.tail(width) or self._spinner_line(width)
            sys.stdout.write(f"\r\x1b[2K{line}")
        sys.stdout.flush()

    def _spinner_line(self, width: int) -> str:
        meta = f"{self.label}… {int(time.monotonic() - self.started)}s"
        tokens = self.chars // 4
        if tokens:
            meta += f" · ~{tokens} tokens"
        meta += " · esc to interrupt"
        frame = SPINNER[self.frame % len(SPINNER)]
        return f"{cyan(frame)} {dim(truncate(meta, max(width - 3, 0)))}"

    def tick(self) -> None:
        """Advance the spinner. Call this every 80ms or so while a run is going."""
        self.frame += 1
        self._redraw()

    def _close_text(self) -> None:
        self.in_text = False
        if self.live:
            self.commit(self.md.finish(self._width()))

    def _gap(self) -> None:
        """A blank row between blocks, in live mode only."""
        if self.live and self.wrote_any:
            self.commit([""])

    def _end_run(self) -> None:
        self._close_text()
        self.running = False
        self.group = None
        if self.live:
            sys.stdout.write("\r\x1b[2K")
            sys.stdout.flush()

    def _call_started(self, call_id: str, tool: str, arguments: Any) -> None:
        self._close_text()
        self.calls[call_id] = (tool, arguments)
        self.label = f"Running {blocks.display_name(tool)}"
        if self.group is not None and self.group.tool == tool:
            self._redraw()
            return
        self.group = None
        self._gap()
        self.commit([blocks.call_line(tool, arguments, self.cwd)])
        self.shown_call = call_id

    def _header_again(self, call_id: str) -> None:
        """Print a header again if another call's output came since this one's."""
        if self.shown_call == call_id or call_id not in self.calls:
            return
        tool, arguments = self.calls[call_id]
        self.commit([blocks.call_line(tool, arguments, self.cwd)])
        self.shown_call = call_id

    def _call_completed(self, call_id: str, tool_name: str, result: Any) -> None:
        tool, arguments = self.calls.get(call_id, (tool_name, None))
        self.label = "Thinking"
        width = self._width()
        rows = blocks.result_rows(tool, arguments, result, width, self.cwd)
        failed = isinstance(result, str) and result.startswith("Error")
        if not self.live or failed or tool not in GROUPED:
            self.group = None
            self.calls[call_id] = (tool, arguments)
            self._header_again(call_id)
            self.calls.pop(call_id, None)
            self.commit(rows)
            self.shown_call = None
            return
        self.calls.pop(call_id, None)
        item = _group_item(arguments, result, self.cwd)
        if self.group is not None and self.group.tool == tool:
            self.group.items.append(item)
            drawn = blocks.group_rows(tool, self.group.items, width)
            up = self.group.rows
            self.group.rows = len(drawn)
            sys.stdout.write(f"\r\x1b[2K\x1b[{up}A\x1b[J")
            self.commit(drawn)
        else:
            self.group = _Group(tool, [item], len(rows) + 1)
            self.commit(rows)
        self.shown_call = None

    def event(self, event: Any) -> None:
        if isinstance(event, RunStarted):
            self.running = True
            self.started = time.monotonic()
            self.chars = 0
            self.label = "Thinking"
            # The prompt is on the row above; the first block keeps its distance.
            self.wrote_any = True
            self._redraw()
        elif isinstance(event, MessageDelta):
            self.chars += len(event.delta)
            self.shown_call = None
            if self.live:
                self.group = None
                if not self.in_text:
                    self._gap()
                    self.in_text = True
                self.commit(self.md.feed(event.delta, self._width()))
            else:
                sys.stdout.write(event.delta)
                sys.stdout.flush()
        elif isinstance(event, ReasoningDelta):
            self.chars += len(event.delta)
            self._redraw()
        elif isinstance(event, MessageCompleted):
            if self.live:
                self._close_text()
            elif event.output_text.strip():
                print()
        elif isinstance(event, MessageFailed):
            self._close_text()
            self.commit([red(f"✗ {event.error}")])
        elif isinstance(event, ToolCallStarted):
            self._call_started(event.tool_call_id, event.tool_name, event.arguments)
        elif isinstance(event, ToolCallCompleted):
            self._call_completed(event.tool_call_id, event.tool_name, event.result)
        elif isinstance(event, ToolCallFailed):
            self.label = "Thinking"
            self.group = None
            self._header_again(event.tool_call_id)
            self.calls.pop(event.tool_call_id, None)
            self.commit(blocks.failure_rows(event.error, self._width()))
            self.shown_call = None
        elif isinstance(event, PermissionRequested):
            self.ask(event.tool_name, event.arguments, event.reason)
        elif isinstance(event, PermissionResolved):
            mark = green("allowed") if event.granted else red("denied")
            self.commit([f"  {dim('· permission')} {mark} {dim(f'({event.scope})')}"])
            self.shown_call = None
            self.running = True
        elif isinstance(event, ContextCompactionStarted):
            self.commit([dim("· compacting context…")])
        elif isinstance(event, ContextCompactionCompleted):
            self.commit([dim("· context compacted")])
        elif isinstance(event, ContextCompactionFailed):
            self.commit([yellow(f"· compaction failed: {event.error}")])
        elif isinstance(event, ContextUsage):
            self.usage = (event.used_tokens, event.context_window)
        elif isinstance(event, RunCompleted):
            self._end_run()
            if event.result.stop_reason != "end_turn":
                self.commit([yellow(f"· run stopped: {event.result.stop_reason}")])
        elif isinstance(event, RunFailed):
            self._end_run()
            self.commit([red(f"· run failed: {event.error}")])
        elif isinstance(event, RunCancelled):
            self._end_run()
            self.commit([yellow(f"· run cancelled: {event.reason}")])

    def ask(self, tool: str, arguments: Any, reason: str) -> None:
        """Show the permission question; the spinner stays off until ``resume``."""
        self._close_text()
        self.group = None
        self.running = False
        self._gap()
        self.commit(blocks.permission_rows(tool, arguments, reason, self._width(), self.cwd))
        self.shown_call = None

    def resume(self) -> None:
        self.running = True
        self._redraw()


def _group_item(arguments: Any, result: Any, cwd: str) -> str:
    argument = blocks.call_argument(arguments, cwd)
    text = result if isinstance(result, str) else ""
    return f"{argument} · {blocks.count(len(blocks._lines(text.rstrip())), 'line')}"


def answer(line: str) -> tuple[bool, str]:
    """``y`` once, ``a`` for the session, anything else refuses."""
    reply = line.strip().lower()
    if reply in ("y", "yes"):
        return True, "once"
    if reply in ("a", "always", "session"):
        return True, "session"
    return False, "once"
