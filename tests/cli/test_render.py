"""The blocks and markdown in ``dev_docs/cli-design.md``. The Rust CLI has the
same cases in ``blocks.rs`` and ``markdown.rs``; tests run without a terminal,
so rows carry no escapes and read as a person would see them."""

from __future__ import annotations

from openagent.cli import blocks
from openagent.cli.input import DOWN, ENTER, ESC, UP, decode, tidy, unfinished
from openagent.cli.markdown import Markdown
from openagent.cli.render import answer
from openagent.cli.style import truncate

CWD = "/work"


def rows_of(deltas: list[str], width: int) -> list[str]:
    md = Markdown()
    out: list[str] = []
    for delta in deltas:
        out.extend(md.feed(delta, width))
    out.extend(md.finish(width))
    return out


def test_truncation_counts_columns() -> None:
    assert truncate("abcdef", 6) == "abcdef"
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("日本語日本語", 5) == "日本…"


def test_markdown_blocks() -> None:
    text = "# Title\n\n\n- one\n  - nested\n1. first\n> quote\n---\ntext\n"
    assert rows_of([text], 40) == [
        "● Title",
        "",
        "  • one",
        "    • nested",
        "  1. first",
        "  │ quote",
        "  " + "─" * 37,
        "  text",
    ]


def test_inline_markers_are_hidden_and_literals_survive() -> None:
    assert rows_of(["a **b** *c* `d` 2 ** 3 snake_case \\*e\\* 2*3*4\n"], 80) == [
        "● a b c d 2 ** 3 snake_case *e* 2*3*4"
    ]


def test_any_split_of_the_stream_gives_the_same_rows() -> None:
    text = (
        "Intro with **bold words that run on and on across the wrap point** and `code` too.\n\n"
        "- a bullet that is long enough to need wrapping around the edge\n\n"
        "```rust\nlet x = 1;\n```\nend *it*"
    )
    whole = rows_of([text], 30)
    assert rows_of(list(text), 30) == whole
    for at in (3, 17, 40, 90, 120):
        assert rows_of([text[:at], text[at:]], 30) == whole


def test_wrapping_keeps_the_indent_and_code_stays_whole() -> None:
    rows = rows_of(["- aaaa bbbb cccc dddd eeee\n```\nlong code line that is not wrapped\n```\n"], 16)
    assert rows == ["● • aaaa bbbb", "    cccc dddd", "    eeee", "    long code line that is not wrapped"]


def test_the_tail_shows_the_unfinished_row() -> None:
    md = Markdown()
    assert md.feed("Hel", 40) == []
    assert md.tail(40) == "● Hel"
    assert md.feed("lo **wo", 40) == []
    assert md.tail(40) == "● Hello wo"
    assert md.feed("rld**\nnext", 40) == ["● Hello world"]
    assert md.tail(40) == "  next"


def test_a_table_is_left_alone() -> None:
    assert rows_of(["| a | b |\n|---|---|\n"], 80) == ["● | a | b |", "  |---|---|"]


def test_calls() -> None:
    assert blocks.call_line("read", {"path": "/work/src/a.rs"}, CWD) == "● Read(src/a.rs)"
    assert blocks.call_line("web_search", {"query": "rust"}, CWD) == "● WebSearch(rust)"
    assert blocks.call_line("bash", {"command": "a\nb"}, CWD) == "● Bash(a ⏎ b)"
    assert blocks.call_line("todo_list", {}, CWD) == "● TodoList()"
    assert len(blocks.call_argument({"command": "x" * 200}, CWD)) == 80


def test_results() -> None:
    assert blocks.result_rows("read", {}, "a\nb\n", 100, CWD) == ["  ⎿ 2 lines"]
    assert blocks.result_rows("write", {"content": "a\nb\nc"}, "ok", 100, CWD) == ["  ⎿ Wrote 3 lines"]
    assert blocks.result_rows("bash", {}, "1\n2\n3\n4\n5\n6\n7\n", 100, CWD) == [
        "  ⎿ 1",
        "    2",
        "    3",
        "    4",
        "    5",
        "    … +2 lines",
    ]
    assert blocks.result_rows("bash", {}, "", 100, CWD) == ["  ⎿ (no output)"]
    assert blocks.result_rows("read", {}, "Error: gone", 100, CWD) == ["  ⎿ Error: gone"]
    assert blocks.result_rows("bash", {}, "y" * 200, 20, CWD) == [f"  ⎿ {'y' * 14}…"]


def test_searches_show_relative_paths() -> None:
    assert blocks.result_rows("glob", {}, "/work/a.txt\n/other/b.txt", 100, CWD) == ["  ⎿ a.txt", "    /other/b.txt"]


def test_edits_show_the_diff_with_line_numbers() -> None:
    text = "Successfully made 1 replacement(s).\n--- a/x\n+++ b/x\n@@ -11,3 +11,3 @@\n keep\n-old\n+new\n after\n"
    assert blocks.result_rows("edit", {}, text, 100, CWD) == [
        "  ⎿ Added 1 line, removed 1 line",
        "    11   keep",
        "    12 - old",
        "    12 + new",
        "    13   after",
    ]


def test_the_edit_tool_writes_a_diff_the_block_can_read(tmp_path) -> None:
    from openagent.tools.builtin import edit

    target = tmp_path / "a.txt"
    target.write_text("one\ntwo\nthree\n")
    rows = blocks.result_rows("edit", {}, edit(str(target), "two", "TWO"), 100, CWD)
    assert rows == [
        "  ⎿ Added 1 line, removed 1 line",
        "     1   one",
        "     2 - two",
        "     2 + TWO",
        "     3   three",
    ]


def test_groups() -> None:
    assert blocks.group_rows("read", ["a · 1 line", "b · 2 lines"], 100) == [
        "● Read 2 files",
        "  ⎿ a · 1 line",
        "  ⎿ b · 2 lines",
    ]
    assert blocks.group_rows("grep", ["x", "y"], 100)[0] == "● Grep 2 searches"


def test_permission_questions() -> None:
    assert blocks.permission_rows("bash", {"command": "rm x"}, "changes files", 100, CWD) == [
        "⚠ Bash wants to run",
        "    rm x",
        "    changes files",
    ]
    assert blocks.permission_rows("edit", {"path": "/work/a", "find": "o", "replace": "n"}, "", 100, CWD) == [
        "⚠ Edit wants to run",
        "    a",
        "    - o",
        "    + n",
    ]


def test_numbers_and_the_box() -> None:
    assert blocks.compact_count(200_000) == "200k"
    assert blocks.compact_count(1_000_000) == "1M"
    assert blocks.percent(1, 8) == 13
    assert blocks.header_box("openagent", [("model", "m"), ("cwd", "~/x")], 80) == [
        "╭─ openagent ─╮",
        "│ model  m    │",
        "│ cwd    ~/x  │",
        "╰─────────────╯",
    ]


def test_input_helpers() -> None:
    assert unfinished("first \\")
    assert unfinished("```\ncode")
    assert not unfinished("```\ncode\n```")
    assert not unfinished("plain")
    assert tidy("first \\\nsecond\n") == "first \nsecond"
    assert decode(b"\x1b") == [ESC]
    assert decode(b"\x1b[A\x1b[B\r") == [UP, DOWN, ENTER]
    assert decode(b"y") == ["y"]
    assert decode(b"\x1b[3~") == []
    assert answer("A") == (True, "session")
    assert answer("") == (False, "once")
