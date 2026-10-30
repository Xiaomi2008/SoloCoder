"""Auto mode's file-tool scope.

This is a path check, not an OS sandbox. Shell commands can still leave the
working directory; these tests cover only the built-in file tools.
"""

from __future__ import annotations

from pathlib import Path

from openagent.core.tool import ToolContext
from openagent.tools.builtin import write


def test_symlink_out_of_scope_is_rejected(tmp_path: Path):
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (work / "link").symlink_to(outside)

    ctx = ToolContext(working_dir=str(work), enforce_scope=True)
    assert ctx.contains(work / "inside.txt")
    assert not ctx.contains(work / "link"), "a symlink target outside the scope is not in scope"


def test_file_tool_refuses_a_path_outside_the_scope(tmp_path: Path):
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside.txt"

    ctx = ToolContext(working_dir=str(work), enforce_scope=True)
    result = write(str(outside), "not allowed", tool_context=ctx)

    assert "Access denied" in result
    assert not outside.exists()
