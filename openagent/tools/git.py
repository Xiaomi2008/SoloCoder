"""Git tools: status, diff and log without a shell, and commits.

``bash`` can run git too, but every bash call is ``exec`` and needs approval
in ask mode; these read-only tools run without a prompt, so checking what
changed costs the user nothing. They run in the session directory and return
plain text (no colour codes: the model reads them, not a terminal).
"""

from __future__ import annotations

import asyncio

from ..core.tool import ToolContext, tool

_TIMEOUT = 30
#: Diffs above this many characters are cut; a whole-repo diff can be huge.
MAX_DIFF_CHARS = 20_000


async def _git(ctx: ToolContext, *args: str) -> tuple[int, str]:
    """Run git in the session directory; returns (exit code, output)."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=ctx.resolve(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return 127, "git is not installed"
    try:
        output, _ = await asyncio.wait_for(process.communicate(), _TIMEOUT)
    except asyncio.TimeoutError:
        process.kill()
        return 124, f"git {args[0]} timed out after {_TIMEOUT}s"
    return process.returncode or 0, output.decode("utf-8", errors="replace").strip()


@tool
async def git_status(tool_context: ToolContext | None = None) -> str:
    """Show the current branch and which files are staged, changed or untracked.

    Returns:
        Branch and file status (git status --short --branch)
    """
    code, output = await _git(tool_context or ToolContext(), "status", "--short", "--branch")
    if code != 0:
        return f"Error: {output}"
    lines = output.splitlines()
    if len(lines) <= 1:
        return f"{output}\nWorking tree clean."
    return output


@tool
async def git_diff(
    path: str | None = None,
    staged: bool = False,
    ref: str | None = None,
    tool_context: ToolContext | None = None,
) -> str:
    """Show changes as a unified diff.

    Args:
        path: Limit the diff to this file or directory
        staged: Show staged changes instead of unstaged ones
        ref: Compare the working tree against this commit or branch (e.g. HEAD~1, main)

    Returns:
        Unified diff, or a note when there are no changes
    """
    args = ["diff"]
    if staged:
        args.append("--staged")
    if ref:
        if ref.startswith("-"):
            return f"Error: '{ref}' is not a commit or branch."
        args.append(ref)
    if path:
        args.extend(["--", path])
    code, output = await _git(tool_context or ToolContext(), *args)
    if code != 0:
        return f"Error: {output}"
    if not output:
        return "No staged changes." if staged else "No changes."
    if len(output) > MAX_DIFF_CHARS:
        return output[:MAX_DIFF_CHARS] + f"\n\n[diff cut at {MAX_DIFF_CHARS:,} characters; pass path to see one file]"
    return output


@tool
async def git_log(
    count: int = 10,
    path: str | None = None,
    tool_context: ToolContext | None = None,
) -> str:
    """Show recent commits, newest first.

    Args:
        count: How many commits to show (default 10, at most 100)
        path: Only commits that touched this file or directory

    Returns:
        One line per commit: short hash, date, author, subject
    """
    count = max(1, min(int(count), 100))
    args = ["log", f"-n{count}", "--format=%h %ad %an: %s", "--date=short"]
    if path:
        args.extend(["--", path])
    code, output = await _git(tool_context or ToolContext(), *args)
    if code != 0:
        return f"Error: {output}"
    return output or "No commits yet."


@tool
async def git_commit(
    message: str,
    paths: list[str] | None = None,
    all_changes: bool = False,
    tool_context: ToolContext | None = None,
) -> str:
    """Commit staged changes.

    Args:
        message: Commit message
        paths: Stage these files before committing
        all_changes: Stage every change to tracked files first (git commit -a)

    Returns:
        The new commit's hash and summary, or why nothing was committed
    """
    if not message.strip():
        return "Error: the commit message is empty."
    ctx = tool_context or ToolContext()
    if paths:
        code, output = await _git(ctx, "add", "--", *paths)
        if code != 0:
            return f"Error staging files: {output}"
    args = ["commit", "-m", message]
    if all_changes:
        args.append("-a")
    code, output = await _git(ctx, *args)
    if code != 0:
        if any(s in output for s in ("nothing to commit", "nothing added to commit", "no changes added")):
            return "Nothing to commit: stage files with paths=[...] or pass all_changes=true."
        return f"Error: {output}"
    return output.splitlines()[0] if output else "Committed."


git_tools = [git_status, git_diff, git_log, git_commit]

__all__ = ["git_commit", "git_diff", "git_log", "git_status", "git_tools"]
