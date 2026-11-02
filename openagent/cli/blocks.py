"""The text of each block in ``dev_docs/cli-design.md``, without any terminal
state, so plain output can be compared between engines."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openagent.cli.style import bold, cyan, dim, green, red, text_width, truncate, yellow

ARG_LIMIT = 80
DIFF_ROWS = 40


def display_name(tool: str) -> str:
    """``web_search`` to ``WebSearch``."""
    return "".join(part[:1].upper() + part[1:] for part in tool.split("_") if part)


def _relative(path: str, cwd: str) -> str:
    try:
        return str(Path(path).relative_to(cwd))
    except ValueError:
        return path


def call_argument(arguments: Any, cwd: str) -> str:
    """The argument that identifies a call."""
    if not isinstance(arguments, dict):
        return ""

    def text(key: str) -> str | None:
        value = arguments.get(key)
        return value if isinstance(value, str) else None

    path = text("path")
    if path is None:
        path = text("file")
    if path is not None:
        chosen = _relative(path, cwd)
    else:
        chosen = next(
            (v for v in (text(k) for k in ("command", "pattern", "query", "url")) if v is not None),
            next((v for v in arguments.values() if isinstance(v, str)), ""),
        )
    return truncate(chosen.replace("\n", " ⏎ "), ARG_LIMIT)


def call_line(tool: str, arguments: Any, cwd: str) -> str:
    return f"{cyan('●')} {bold(display_name(tool))}({dim(call_argument(arguments, cwd))})"


def result_text(result: Any) -> str:
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


def _lines(text: str) -> list[str]:
    """As Rust's ``str::lines``: no empty row after a final newline."""
    if not text:
        return []
    rows = [row[:-1] if row.endswith("\r") else row for row in text.split("\n")]
    if rows[-1] == "":
        rows.pop()
    return rows


def count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _preview(text: str, keep: int, width: int, glyph: Any, paint: Any) -> list[str]:
    """Text rows under a ``⎿``, with the tail counted."""
    lines = _lines(text.rstrip())
    rows = []
    for i, line in enumerate(lines[:keep]):
        lead = f"  {glyph('⎿')} " if i == 0 else "    "
        rows.append(f"{lead}{paint(truncate(line, max(width - 5, 0)))}")
    if len(lines) > keep:
        rows.append(f"    {dim('… +' + count(len(lines) - keep, 'line'))}")
    return rows


def summary(tool: str, arguments: Any, text: str) -> str | None:
    """One-line summary of a successful call, when the tool has one."""
    if tool == "read":
        return count(len(_lines(text.rstrip())), "line")
    if tool == "write":
        content = arguments.get("content", "") if isinstance(arguments, dict) else ""
        return f"Wrote {count(len(_lines(content if isinstance(content, str) else '')), 'line')}"
    return None


def _hunk_start(header: str) -> tuple[int, int] | None:
    """``@@ -12,3 +12,4 @@`` as the two starting line numbers."""
    parts = header.split()
    try:
        return int(parts[1][1:].split(",")[0]), int(parts[2][1:].split(",")[0])
    except (IndexError, ValueError):
        return None


def _diff_rows(text: str, width: int) -> tuple[int, int, list[str]]:
    added = removed = old = new = 0
    rows = []
    for line in _lines(text):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("@@"):
            start = _hunk_start(line)
            if start:
                old, new = start
            continue
        if line.startswith("+"):
            added += 1
            new += 1
            number, mark, paint = new - 1, "+", green
        elif line.startswith("-"):
            removed += 1
            old += 1
            number, mark, paint = old - 1, "-", red
        elif line.startswith(" "):
            old += 1
            new += 1
            number, mark, paint = new - 1, " ", dim
        else:
            continue
        rows.append(paint(truncate(f"{number:>6} {mark} {line[1:]}", max(width - 1, 0))))
    return added, removed, rows


def _edit_rows(text: str, width: int) -> list[str]:
    added, removed, diff = _diff_rows(text, width)
    if not added and not removed:
        return _preview(text, 3, width, green, dim)
    changed = f"Added {count(added, 'line')}, removed {count(removed, 'line')}"
    rows = [f"  {green('⎿')} {dim(changed)}"]
    rows.extend(diff[:DIFF_ROWS])
    if len(diff) > DIFF_ROWS:
        rows.append(f"    {dim('… +' + count(len(diff) - DIFF_ROWS, 'line'))}")
    return rows


def result_rows(tool: str, arguments: Any, result: Any, width: int, cwd: str) -> list[str]:
    """Rows under a completed call."""
    text = result_text(result)
    if tool in ("glob", "grep"):
        text = text.replace(f"{cwd}/", "")
    if text.startswith("Error"):
        return _preview(text, 5, width, red, red)
    if tool == "edit":
        return _edit_rows(text, width)
    line = summary(tool, arguments, text)
    if line is not None:
        return [f"  {green('⎿')} {dim(line)}"]
    if not text.strip():
        return [f"  {green('⎿')} {dim('(no output)')}"]
    return _preview(text, 5 if tool in ("bash", "grep", "glob") else 3, width, green, dim)


def failure_rows(error: str, width: int) -> list[str]:
    return _preview(error, 5, width, red, red)


def group_rows(tool: str, items: list[str], width: int) -> list[str]:
    """The grouped form of a repeated read-only tool."""
    amount = count(len(items), "file") if tool == "read" else f"{len(items)} searches"
    rows = [f"{cyan('●')} {bold(display_name(tool))} {dim(amount)}"]
    for item in items[-5:]:
        rows.append(f"  {green('⎿')} {dim(truncate(item, max(width - 5, 0)))}")
    return rows


def permission_rows(tool: str, arguments: Any, reason: str, width: int, cwd: str) -> list[str]:
    rows = [f"{yellow('⚠')} {bold(display_name(tool))} wants to run"]

    def cut(line: str) -> str:
        return truncate(line, max(width - 5, 0))

    def text(key: str) -> str:
        value = arguments.get(key) if isinstance(arguments, dict) else None
        return value if isinstance(value, str) else ""

    if tool == "bash":
        rows.extend(f"    {cut(line)}" for line in _lines(text("command"))[:8])
    elif tool == "edit":
        rows.append(f"    {dim(call_argument(arguments, cwd))}")
        rows.extend(f"    {red(cut(f'- {line}'))}" for line in _lines(text("find"))[:8])
        rows.extend(f"    {green(cut(f'+ {line}'))}" for line in _lines(text("replace"))[:8])
    elif tool == "write":
        rows.append(f"    {dim(call_argument(arguments, cwd))}")
        lines = _lines(text("content"))
        rows.extend(f"    {green(cut(f'+ {line}'))}" for line in lines[:5])
        if len(lines) > 5:
            rows.append(f"    {dim('… +' + count(len(lines) - 5, 'line'))}")
    elif isinstance(arguments, dict):
        for key, value in arguments.items():
            rows.append(f"    {key}: {dim(cut(result_text(value).replace(chr(10), ' ⏎ ')))}")
    if reason:
        rows.append(f"    {dim(reason)}")
    return rows


def header_box(title: str, rows: list[tuple[str, str]], width: int) -> list[str]:
    """A framed box of ``(label, value)`` rows."""
    label = max((text_width(name) for name, _ in rows), default=0)
    limit = max(width - (label + 7), 10)
    lines = [f"{name:<{label}}  {truncate(value, limit)}" for name, value in rows]
    inner = max([*(text_width(line) for line in lines), text_width(title) + 2]) + 2
    fill = max(inner - (text_width(title) + 3), 0)
    out = [f"{dim('╭─')} {bold(title)} {dim('─' * fill + '╮')}"]
    for line in lines:
        out.append(f"{dim('│')} {line}{' ' * (inner - 2 - text_width(line))} {dim('│')}")
    out.append(dim(f"╰{'─' * inner}╯"))
    return out


def tilde(path: str) -> str:
    """``~/x`` for a path under the home directory."""
    home = os.environ.get("HOME")
    if home and (path == home or path.startswith(home.rstrip("/") + "/")):
        return "~/" + path[len(home.rstrip("/")) + 1 :]
    return path


def compact_count(n: int) -> str:
    """``200k``, ``1M``, or the number."""
    if n >= 1_000_000 and n % 1_000_000 == 0:
        return f"{n // 1_000_000}M"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


def context_bar(used: int, window: int) -> str:
    bar_width = 30
    share = min(max(used / window, 0.0), 1.0) if window > 0 else 0.0
    filled = int(share * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    if share >= 0.9:
        return red(bar)
    return yellow(bar) if share >= 0.7 else green(bar)


def percent(used: int, window: int) -> int:
    """Rounded half away from zero, as Rust's ``f64::round``."""
    return int(used * 100 / window + 0.5) if window > 0 else 0
