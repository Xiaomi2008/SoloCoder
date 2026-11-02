"""Streaming markdown for the terminal: feed deltas, get finished rows back.

The unfinished row is available from :meth:`Markdown.tail` for the caller to
redraw in place. Rows already returned are never changed, so an inline style
open across a wrap is carried over in ``state`` instead of being re-parsed. The
same algorithm lives in the Rust CLI; plain transcripts are compared.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from openagent.cli import style
from openagent.cli.style import bold, char_width, cyan, dim, heading, italic, text_width, truncate, yellow

BOLD = 1
ITALIC = 2
CODE = 4
INLINE = BOLD | ITALIC | CODE
HEADING = 8
QUOTE = 16

LEAD = 2
HOLD = "hold"
BLANK = "blank"
RULE = "rule"
TABLE = "table"


@dataclass
class Cell:
    ch: str
    style: int
    #: Where this cell's character sits in the text it was parsed from.
    start: int


@dataclass
class Fenced:
    lang: str


@dataclass
class Text:
    strip: int
    first: str
    rest: str
    style: int


def classify(line: str, complete: bool) -> str | Fenced | Text:
    """What a line turned out to be, or ``HOLD`` if too little has arrived."""
    t = line.lstrip(" ")
    indent = len(line) - len(t)
    pad = " " * indent
    plain = Text(indent, pad, pad, 0)
    if not t.strip():
        return BLANK if complete else HOLD
    if t.startswith("```") or t.startswith("~~~"):
        if not complete:
            return HOLD
        parts = t.lstrip("`~").strip().split(" ")
        return Fenced(parts[0] if parts else "")
    if t.startswith("|"):
        return TABLE if complete else HOLD
    trimmed = t.rstrip()
    first = trimmed[0] if trimmed else " "
    if first in "-*_`~" and all(c == first for c in trimmed):
        if not complete:
            return HOLD
        if first not in "`~" and len(trimmed) >= 3:
            return RULE
    if t.startswith("#"):
        hashes = len(t) - len(t.lstrip("#"))
        after = t[hashes:hashes + 1]
        if not after and not complete:
            return HOLD
        if after == " " and hashes <= 6:
            if not complete and not t[hashes:].strip():
                return HOLD
            return Text(indent + hashes + 1, "", "", HEADING)
        return plain
    if first in "-*+":
        after = t[1:2]
        if not after and not complete:
            return HOLD
        if after == " ":
            return Text(indent + 2, f"{pad}• ", f"{pad}  ", 0)
        return plain
    if first in "0123456789":
        digits = 0
        while digits < len(t) and t[digits] in "0123456789":
            digits += 1
        rest = t[digits:]
        if rest in ("", "."):
            return plain if complete else HOLD
        if rest.startswith(". "):
            return Text(indent + digits + 2, f"{pad}{t[:digits + 2]}", f"{pad}{' ' * (digits + 2)}", 0)
        return plain
    if first == ">":
        strip = 2 if t[1:2] == " " else 1
        return Text(indent + strip, f"{pad}│ ", f"{pad}│ ", QUOTE)
    return plain


def _is_ascii_punct(char: str) -> bool:
    return char.isascii() and char.isprintable() and not char.isalnum() and char != " "


def inline(text: str, state: int, base: int, complete: bool) -> list[Cell]:
    """Inline styles to cells. An unfinished line stops before a marker it
    cannot judge yet; those characters stay in the buffer for the next delta."""
    cells: list[Cell] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else None
        start = i
        if state & CODE:
            if c == "`":
                state &= ~CODE
            else:
                cells.append(Cell(c, state | base, start))
            i += 1
        elif c == "`":
            state |= CODE
            i += 1
        elif c == "\\":
            if nxt is None and not complete:
                break
            if nxt is not None and _is_ascii_punct(nxt):
                cells.append(Cell(nxt, state | base, start))
                i += 1
            else:
                cells.append(Cell(c, state | base, start))
            i += 1
        elif c == "*":
            if nxt is None and not complete:
                break
            if nxt == "*":
                after = text[i + 2] if i + 2 < n else None
                if state & BOLD:
                    state &= ~BOLD
                elif after is None and not complete:
                    break
                elif after is not None and not after.isspace():
                    state |= BOLD
                else:
                    cells.append(Cell("*", state | base, start))
                    cells.append(Cell("*", state | base, start))
                i += 2
                continue
            before = cells[-1].ch if cells else None
            if state & ITALIC and before is not None and not before.isspace():
                state &= ~ITALIC
            elif (
                not state & ITALIC
                and nxt is not None
                and not nxt.isspace()
                and not (before is not None and before.isalnum())
            ):
                state |= ITALIC
            else:
                cells.append(Cell("*", state | base, start))
            i += 1
        else:
            cells.append(Cell(c, state | base, start))
            i += 1
    return cells


def wrap(cells: list[Cell], columns: int) -> list[tuple[int, int]]:
    """Greedy wrap at spaces; a word longer than a row breaks. Ranges into ``cells``."""
    rows: list[tuple[int, int]] = []
    start = 0
    used = 0
    space: int | None = None
    i = 0
    while i < len(cells):
        columns_here = char_width(cells[i].ch)
        if used + columns_here > columns and i > start:
            if space is not None and space > start:
                end, resume = space, space + 1
            else:
                end, resume = i, i
            rows.append((start, end))
            start = resume
            while start < len(cells) and cells[start].ch == " ":
                start += 1
            used = 0
            space = None
            i = start
            continue
        if cells[i].ch == " ":
            space = i
        used += columns_here
        i += 1
    rows.append((start, len(cells)))
    return rows


def paint_cells(cells: list[Cell]) -> str:
    out: list[str] = []
    i = 0
    while i < len(cells):
        kind = cells[i].style
        end = i
        while end < len(cells) and cells[end].style == kind:
            end += 1
        text = "".join(c.ch for c in cells[i:end])
        if kind & CODE:
            text = yellow(text)
        elif kind & HEADING:
            text = heading(text)
        elif kind & BOLD:
            text = bold(text)
        elif kind & ITALIC:
            text = italic(text)
        elif kind & QUOTE:
            text = dim(text)
        out.append(text)
        i = end
    return "".join(out)


def _light_background() -> bool:
    """``COLORFGBG`` ends in the background colour; 7 and 15 are the light ones."""
    return os.environ.get("COLORFGBG", "").rsplit(";", 1)[-1] in ("7", "15")


class _Fence:
    """Highlights line by line; earlier lines are re-lexed so strings stay open."""

    KEEP = 200

    def __init__(self, lang: str) -> None:
        self.lexer = None
        self.formatter = None
        self.lines: list[str] = []
        if not lang or not style.colors_enabled():
            return
        from pygments.formatters import Terminal256Formatter, TerminalTrueColorFormatter
        from pygments.lexers import get_lexer_by_name
        from pygments.util import ClassNotFound

        try:
            self.lexer = get_lexer_by_name(lang, stripnl=False, ensurenl=False)
        except ClassNotFound:
            return
        scheme = "default" if _light_background() else "native"
        formatter = TerminalTrueColorFormatter if style.truecolor() else Terminal256Formatter
        self.formatter = formatter(style=scheme)

    def line(self, line: str) -> str:
        if self.lexer is None or self.formatter is None:
            return line
        from pygments import highlight

        self.lines = [*self.lines[-self.KEEP :], line]
        painted = highlight("\n".join(self.lines), self.lexer, self.formatter)
        return painted.rstrip("\n").split("\n")[-1]


@dataclass
class _Block:
    first: str
    rest: str
    style: int
    first_done: bool = False


def _dim_if_quote(prefix: str, kind: int) -> str:
    return dim(prefix) if kind & QUOTE else prefix


class Markdown:
    def __init__(self) -> None:
        self.buf = ""
        self.state = 0
        self.block: _Block | None = None
        self.fence: _Fence | None = None
        self.marked = False
        self.blank_pending = False

    @staticmethod
    def _lead(marked: bool) -> str:
        return "  " if marked else f"{cyan('●')} "

    def feed(self, delta: str, columns: int) -> list[str]:
        """Finished rows out of this delta."""
        self.buf += delta
        out: list[str] = []
        while (end := self.buf.find("\n")) >= 0:
            line, self.buf = self.buf[:end], self.buf[end + 1 :]
            self._complete(line.rstrip("\r"), columns, out)
        self._partial(columns, out)
        return out

    def finish(self, columns: int) -> list[str]:
        """The message ended: whatever is left, as rows, and reset for the next one."""
        out: list[str] = []
        if self.buf:
            line, self.buf = self.buf, ""
            self._complete(line, columns, out)
        self.__init__()  # type: ignore[misc]
        return out

    def tail(self, columns: int) -> str:
        """The row still being written, for drawing in place."""
        if not self.buf:
            return ""
        if self.fence is not None:
            return dim(truncate(f"    {self.buf}", max(columns - 1, 0)))
        block = self.block
        if block is None:
            return ""
        cells = inline(self.buf, self.state, block.style, False)
        start, end = wrap(cells, self._columns(block, columns))[-1]
        if start >= end:
            return ""
        prefix = block.rest if block.first_done else block.first
        return f"{self._lead(self.marked)}{_dim_if_quote(prefix, block.style)}{paint_cells(cells[start:end])}"

    @staticmethod
    def _columns(block: _Block, columns: int) -> int:
        return max(columns - (LEAD + text_width(block.rest) + 1), 10)

    def _row(self, body: str, out: list[str]) -> None:
        if self.blank_pending:
            out.append("")
            self.blank_pending = False
        out.append(f"{self._lead(self.marked)}{body}")
        self.marked = True

    def _emit(self, cells: list[Cell], rows: list[tuple[int, int]], out: list[str]) -> None:
        for start, end in rows:
            block = self.block
            assert block is not None
            prefix = block.rest if block.first_done else block.first
            block.first_done = True
            self._row(f"{_dim_if_quote(prefix, block.style)}{paint_cells(cells[start:end])}", out)

    def _complete(self, line: str, columns: int, out: list[str]) -> None:
        if self.fence is not None:
            t = line.strip()
            closing = len(t) >= 3 and (set(t) == {"`"} or set(t) == {"~"})
            if closing:
                self.fence = None
                return
            code = self.fence.line(line)
            if self.blank_pending:
                out.append("")
                self.blank_pending = False
            out.append(f"    {code}")
            return
        text = line
        if self.block is None:
            kind = classify(line, True)
            if kind == BLANK:
                self.blank_pending = self.marked or bool(out)
                return
            if kind == RULE:
                self._row(dim("─" * min(max(columns - (LEAD + 1), 0), 78)), out)
                return
            if kind == TABLE:
                self._row(line.lstrip(), out)
                return
            if isinstance(kind, Fenced):
                self.fence = _Fence(kind.lang)
                return
            assert isinstance(kind, Text)
            self.block = _Block(kind.first, kind.rest, kind.style)
            text = line[kind.strip :]
        block = self.block
        cells = inline(text, self.state, block.style, True)
        self._emit(cells, wrap(cells, self._columns(block, columns)), out)
        self.block = None
        self.state = 0

    def _partial(self, columns: int, out: list[str]) -> None:
        if self.fence is not None or not self.buf:
            return
        if self.block is None:
            kind = classify(self.buf, False)
            if not isinstance(kind, Text):
                return
            self.block = _Block(kind.first, kind.rest, kind.style)
            self.buf = self.buf[kind.strip :]
        block = self.block
        cells = inline(self.buf, self.state, block.style, False)
        rows = wrap(cells, self._columns(block, columns))
        if len(rows) < 2:
            return
        last_start = rows[-1][0]
        if last_start >= len(cells):
            # Everything so far is on finished rows; only skipped spaces remain.
            return
        cell = cells[last_start]
        self._emit(cells, rows[:-1], out)
        self.buf = self.buf[cell.start :]
        self.state = cell.style & INLINE
