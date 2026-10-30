"""Reading back a tool output that compaction cleared from the conversation."""

from __future__ import annotations

from ..core.tool import ToolContext, tool


@tool
def recall_output(
    ref: str,
    start_line: int = 1,
    max_lines: int = 200,
    tool_context: ToolContext | None = None,
) -> str:
    """Read a tool output (or a long tool input) cleared to save context.

    Old outputs are replaced in the conversation by a line like
    `[output cleared to save context: bash ... — recall_output("call_1") reads it]`.
    This returns the saved text as it was then. For things that change, such
    as a file's contents or test results, rerun the tool instead to see the
    current state. Read only the lines you need.

    Args:
        ref: The id in the placeholder, e.g. "call_1" or "call_1.content".
        start_line: First line to return, counting from 1.
        max_lines: How many lines to return at most.
    """
    from openagent.runtime.clearing import output_file

    folder = tool_context.outputs_dir if tool_context else None
    if not folder:
        return "Error: this session keeps no cleared outputs."
    path = output_file(folder, ref)
    if not path.is_file():
        return f"Error: no cleared output {ref!r} in this session."
    lines = path.read_text(encoding="utf-8").splitlines()
    start = max(1, int(start_line))
    end = min(len(lines), start - 1 + max(1, int(max_lines)))
    if start > len(lines):
        return f"{ref} has {len(lines)} lines; start_line {start} is past the end."
    body = "\n".join(lines[start - 1 : end])
    more = f"\n[lines {start}-{end} of {len(lines)}]" if (start > 1 or end < len(lines)) else ""
    return body + more
