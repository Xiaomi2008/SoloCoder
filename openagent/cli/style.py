"""Colour roles and terminal facts, as ``dev_docs/cli-design.md`` lists them."""

from __future__ import annotations

import os
import sys

from wcwidth import wcwidth


def live_terminal() -> bool:
    """May output use the spinner, in-place lines and markdown?"""
    return sys.stdout.isatty() and os.environ.get("TERM") != "dumb"


def interactive_input() -> bool:
    """Can keys be read from a person, not a pipe?"""
    return sys.stdin.isatty() and live_terminal()


def colors_enabled() -> bool:
    return "NO_COLOR" not in os.environ and sys.stdout.isatty()


def truecolor() -> bool:
    return os.environ.get("COLORTERM") in ("truecolor", "24bit")


def paint(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if colors_enabled() else text


def _role(rgb: str, basic: str, text: str) -> str:
    return paint(rgb if truecolor() else basic, text)


def bold(text: str) -> str:
    return paint("1", text)


def italic(text: str) -> str:
    return paint("3", text)


def dim(text: str) -> str:
    return paint("2", text)


def red(text: str) -> str:
    return _role("38;2;247;118;142", "31", text)


def green(text: str) -> str:
    return _role("38;2;115;201;145", "32", text)


def yellow(text: str) -> str:
    return _role("38;2;224;175;104", "33", text)


def cyan(text: str) -> str:
    return _role("38;2;122;162;247", "36", text)


def heading(text: str) -> str:
    return _role("1;38;2;122;162;247", "1;36", text)


def width() -> int:
    """Columns the terminal has; 100 when it is not one, so plain output is stable."""
    try:
        columns = os.get_terminal_size(sys.stdout.fileno()).columns
    except (OSError, ValueError):
        return 100
    return columns or 100


def char_width(char: str) -> int:
    return max(wcwidth(char), 0)


def text_width(text: str) -> int:
    return sum(char_width(c) for c in text)


def truncate(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` columns, ending in ``…`` when anything was cut."""
    if text_width(text) <= limit:
        return text
    out: list[str] = []
    used = 0
    for char in text:
        columns = char_width(char)
        if used + columns + 1 > limit:
            break
        out.append(char)
        used += columns
    return "".join(out) + "…"
