"""Built-in tools for OpenAgent framework."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from ..core.tool import ToolContext, tool


# ============================================================================
# File Operations Tools
# ============================================================================


def _scope(ctx: ToolContext, path: str | None = None) -> tuple[Path | None, str | None]:
    """Resolve ``path`` and enforce the auto-mode file scope.

    This is a path check, not an OS sandbox: it keeps the built-in file tools
    inside the run's directory, but shell commands can still leave it until a
    VM/container boundary exists.
    """
    resolved = ctx.resolve(path)
    if ctx.enforce_scope and not ctx.contains(resolved):
        return None, (
            f"Error: Access denied. Path '{resolved}' is outside the working "
            f"directory '{ctx.working_dir}'."
        )
    return resolved, None


@tool
def read(
    path: str,
    line_start: int | None = None,
    line_end: int | None = None,
    tool_context: ToolContext | None = None,
) -> str:
    """Read files (including images) by absolute path.

    Args:
        path: Absolute path to the file
        line_start: Optional starting line number (1-indexed). If None, starts from beginning.
        line_end: Optional ending line number (1-indexed). If None, reads to end of file.

    Returns:
        File contents as string, or base64-encoded image data for image files
    """
    ctx = tool_context or ToolContext()
    file_path, denied = _scope(ctx, path)
    if denied:
        return denied

    if not file_path.exists():
        return f"Error: File '{path}' does not exist."

    if not file_path.is_file():
        if file_path.is_dir():
            return f"Error: '{path}' is a directory, not a file."
        return f"Error: '{path}' does not exist or is not accessible."

    # Check for image files
    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}
    if file_path.suffix.lower() in image_extensions:
        import base64

        with open(file_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        return f"[Image file ({file_path.suffix}): {len(encoded)} bytes of base64 data]"

    try:
        content = file_path.read_text(encoding="utf-8")

        # Apply line range if specified
        if line_start is not None or line_end is not None:
            lines = content.splitlines()
            start_idx = (line_start - 1) if line_start is not None else 0
            end_idx = line_end if line_end is not None else len(lines)

            # Handle negative indices and out of bounds
            start_idx = max(0, start_idx)
            end_idx = min(len(lines), end_idx) if end_idx >= 0 else len(lines) + end_idx

            content = "\n".join(lines[start_idx:end_idx])

        return content
    except Exception as e:
        return f"Error reading file: {e}"


@tool
def write(
    path: str,
    content: str,
    create_parents: bool = True,
    tool_context: ToolContext | None = None,
) -> str:
    """Create or overwrite files.

    Args:
        path: Absolute path to the file to create/overwrite
        content: Content to write to the file
        create_parents: If True, create parent directories as needed (default: True)

    Returns:
        Success message with file size
    """
    ctx = tool_context or ToolContext()
    file_path, denied = _scope(ctx, path)
    if denied:
        return denied

    if create_parents:
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return f"Error creating parent directories: {e}"

    try:
        file_path.write_text(content, encoding="utf-8")
        size = file_path.stat().st_size
        return f"Successfully wrote {size} bytes to '{path}'."
    except Exception as e:
        return f"Error writing file: {e}"


@tool
def edit(
    path: str,
    find: str,
    replace: str,
    expected_replacements: int | None = None,
    tool_context: ToolContext | None = None,
) -> str:
    """Make targeted edits to an existing file with find-and-replace.

    Args:
        path: Absolute path of the file
        find: Exact text to find
        replace: Replacement text
        expected_replacements: If set, warn when the number of replacements differs

    Returns:
        Number of replacements and a unified diff
    """
    ctx = tool_context or ToolContext()
    file_path, denied = _scope(ctx, path)
    if denied:
        return denied

    if not file_path.exists():
        return f"Error: File '{path}' does not exist."

    if not file_path.is_file():
        if file_path.is_dir():
            return f"Error: '{path}' is a directory, not a file."
        return f"Error: '{path}' does not exist or is not accessible."

    try:
        content = file_path.read_text(encoding="utf-8")

        # Count matches (non-overlapping)
        count = len(re.findall(re.escape(find), content))

        if count == 0:
            return f"No occurrences of '{find}' found in '{path}'."

        new_content = content.replace(find, replace)

        file_path.write_text(new_content, encoding="utf-8")

        # Generate unified diff showing exact changes
        import difflib

        old_lines = content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        # Use relative paths in diff headers for cleaner output
        try:
            import os

            rel_path = os.path.relpath(str(file_path))
        except Exception:
            rel_path = str(file_path)

        diff_generator = difflib.unified_diff(
            old_lines, new_lines, fromfile=rel_path, tofile=rel_path, lineterm=""
        )
        diff_output = "".join(diff_generator)

        result = f"Successfully made {count} replacement(s)."

        if expected_replacements is not None and count != expected_replacements:
            result += f" Warning: Expected {expected_replacements} replacements but found {count}."

        # Include the unified diff for clear visualization of changes
        return f"{result}\n{diff_output}"
    except Exception as e:
        return f"Error editing file: {e}"


@tool
def notebook_edit(
    path: str,
    cell_index: int,
    new_source: str,
    cell_type: str = "code",
    tool_context: ToolContext | None = None,
) -> str:
    """Edit Jupyter notebook cells.

    Args:
        path: Absolute path to the .ipynb file
        cell_index: Index of the cell to edit (0-indexed). Use -1 for last cell.
        new_source: New source code/content for the cell
        cell_type: Type of cell - "code" or "markdown" (default: "code")

    Returns:
        Result message with cell update info
    """
    ctx = tool_context or ToolContext()
    file_path, denied = _scope(ctx, path)
    if denied:
        return denied

    if not file_path.exists():
        return f"Error: Notebook '{path}' does not exist."

    if file_path.suffix.lower() != ".ipynb":
        return f"Error: '{path}' is not a Jupyter notebook (.ipynb)."

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            nb = json.load(f)

        if "cells" not in nb or len(nb["cells"]) == 0:
            return "Error: Notebook has no cells."

        # Handle negative indices
        actual_index = cell_index
        if actual_index < 0:
            actual_index = len(nb["cells"]) + actual_index

        if actual_index < 0 or actual_index >= len(nb["cells"]):
            return f"Error: Cell index {cell_index} is out of range (notebook has {len(nb['cells'])} cells)."

        cell = nb["cells"][actual_index]

        # Update the cell
        cell["source"] = new_source if isinstance(new_source, list) else [new_source]
        cell["cell_type"] = cell_type

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(nb, f, indent=2, ensure_ascii=False)

        return f"Successfully updated cell {cell_index} ({cell_type}) in '{path}'."
    except Exception as e:
        return f"Error editing notebook: {e}"


@tool
def glob(pattern: str, path: str | None = None, max_results: int = 100, tool_context: ToolContext | None = None) -> str:
    """Search for files by pattern (replaces find/ls).

    Args:
        pattern: Glob pattern to search for (e.g., "*.py", "**/*.md")
        path: Base directory to search in. If None, searches the session's working directory.
              For security, this should be within the project folder.
        max_results: Maximum number of results to return (default: 100)

    Returns:
        Newline-separated list of matching file paths
    """
    ctx = tool_context or ToolContext()
    base, denied = _scope(ctx, path)
    if denied:
        return denied

    if not base.exists():
        return f"Error: Directory '{path}' does not exist."

    if not base.is_dir():
        return f"Error: '{path}' is not a directory."

    try:
        results = [
            str(match.resolve()) for match in base.glob(pattern) if match.is_file()
        ]

        # Sort and limit results
        results.sort()
        results = results[:max_results]

        if not results:
            return f"No files found matching pattern '{pattern}' in '{base}'."

        return "\n".join(results) + f"\n\nFound {len(results)} file(s)."
    except Exception as e:
        return f"Error searching for files: {e}"


@tool
def grep(
    pattern: str,
    path: str | None = None,
    regex: bool = True,
    max_results: int = 50,
    context_lines: int = 2,
    tool_context: ToolContext | None = None,
) -> str:
    """Search file contents by keyword/regex (replaces grep/rg).

    Args:
        pattern: Text or regex to search for
        path: File or directory to search, inside the project folder (default: working directory)
        regex: Treat pattern as a regular expression
        max_results: Maximum matches to return
        context_lines: Lines of context before and after each match

    Returns:
        Matches with file paths, line numbers and context
    """
    ctx = tool_context or ToolContext()
    base, denied = _scope(ctx, path)
    if denied:
        return denied

    if not base.exists():
        return f"Error: Path '{path}' does not exist."

    try:
        # Compile pattern (escape if not regex)
        compiled_pattern = re.compile(pattern if regex else re.escape(pattern))

        results = []
        files_searched = 0

        # Collect all files to search
        files_to_search = []
        if base.is_file():
            files_to_search = [base]
        else:
            for p in base.rglob("*"):
                if (
                    p.is_file()
                    and not p.name.startswith(".")
                    and p.suffix not in {".pyc", ".class", ".bin"}
                ):
                    files_to_search.append(p)

        for file_path in files_to_search:
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                lines = content.splitlines()

                for i, line in enumerate(lines):
                    if compiled_pattern.search(line):
                        # Get context lines
                        start = max(0, i - context_lines)
                        end = min(len(lines), i + context_lines + 1)
                        context = lines[start:end]

                        results.append(
                            {
                                "file": str(file_path.resolve()),
                                "line": i + 1,
                                "content": line.strip(),
                                "context": context,
                            }
                        )

                        if len(results) >= max_results:
                            return format_grep_results(results)

                files_searched += 1
            except Exception:
                continue

        if not results:
            return f"No matches found for '{pattern}' in {files_searched} file(s)."

        return format_grep_results(results)
    except re.error as e:
        return f"Invalid regex pattern: {e}"
    except Exception as e:
        return f"Error searching files: {e}"


def format_grep_results(results: list[dict]) -> str:
    """Format grep results for display."""
    output = []

    # Group by file
    by_file: dict[str, list[dict]] = {}
    for r in results:
        if r["file"] not in by_file:
            by_file[r["file"]] = []
        by_file[r["file"]].append(r)

    for file_path, matches in by_file.items():
        output.append(f"\n{file_path}:")
        for m in matches:
            output.append(f"  Line {m['line']}: {m['content']}")

    return (
        "\n".join(output)
        + f"\n\nFound {len(results)} match(es) across {len(by_file)} file(s)."
    )


# ============================================================================
# Shell & Process Management Tools
# ============================================================================


@tool
def bash(
    command: str,
    timeout: int | None = None,
    background: bool = False,
    working_dir: str | None = None,
    tool_context: ToolContext | None = None,
) -> str:
    """Run a shell command.

    Args:
        command: Shell command to run
        timeout: Seconds before the process is terminated
        background: Return immediately with a session ID; read the output later with bash_output
        working_dir: Directory to run in (default: the project directory, not the process directory)

    Returns:
        Command output (stdout), or session ID for background processes
    """
    # For background execution, we'll use a shared dictionary managed by the agent
    if background:
        return f"Error: Background execution requires special handling. Use bash_background instead."

    ctx = tool_context or ToolContext()
    try:
        # Set up working directory
        cwd, denied = _scope(ctx, working_dir)
        if denied:
            return denied
        if not cwd.exists():
            return f"Error: Working directory '{working_dir or cwd}' does not exist."

        # `cwd` is the starting directory, not a boundary: no OS sandbox is
        # installed yet, so an absolute path or `cd /` can still escape.
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            text=True,
        )

        try:
            output, _ = process.communicate(timeout=timeout)
            return output if output else "(no output)"
        except subprocess.TimeoutExpired:
            process.kill()
            return f"Error: Command timed out after {timeout} seconds."
    except Exception as e:
        return f"Error executing command: {e}"


@tool
def awk(
    pattern: str,
    input_text: str,
    field: int | None = None,
) -> str:
    """Process text with awk-style field extraction and filtering.

    Args:
        pattern: AWK-compatible expression (e.g., '{print $1,$2}', '$1 > 10 {print $0}')
        input_text: Text to process
        field: If set, extract a specific field number (1-indexed, default field separator is whitespace)
               Overrides pattern when provided.

    Returns:
        Processed text output
    """
    try:
        lines = input_text.splitlines()
        results: list[str] = []

        if field is not None:
            # Simple field extraction
            for line in lines:
                fields = line.split()
                if 1 <= field <= len(fields):
                    results.append(fields[field - 1])
        else:
            # AWK-style pattern evaluation
            # Support: "{print $0}", "{print $1,$2}", "$1 > 10 {print $0}"
            pattern = pattern.strip()
            condition = ""
            action = ""

            if "{" in pattern and "}" in pattern:
                brace_start = pattern.index("{")
                condition = pattern[:brace_start].strip()
                action = pattern[brace_start:]
            elif "{print" in pattern:
                # Pure action like "{print $1,$2}" with no condition
                action = pattern

            if not condition:
                # No condition — print all lines, just apply action
                for line in lines:
                    fields = line.split()
                    inner = action[action.index("{") + 7 : action.index("}")]
                    inner = inner.strip()
                    if inner == "$0" or inner == "*":
                        results.append(line)
                    else:
                        selected = []
                        for part in inner.split(","):
                            part = part.strip()
                            if part.startswith("$"):
                                try:
                                    idx = int(part[1:])
                                    if 0 < idx <= len(fields):
                                        selected.append(fields[idx - 1])
                                except ValueError:
                                    pass
                        if selected:
                            results.append(" ".join(selected))
            else:
                # Condition + action: evaluate condition, apply action on match
                for line in lines:
                    fields = line.split()
                    try:
                        ns: dict[str, int | str] = {}
                        for i, f in enumerate(fields):
                            try:
                                ns[f"f{i + 1}"] = int(f)
                            except ValueError:
                                ns[f"f{i + 1}"] = f
                        safe = condition
                        for i in range(1, len(fields) + 1):
                            safe = safe.replace(f"${i}", f"f{i}")
                        if eval(safe, {"__builtins__": {}}, ns):  # noqa: S307
                            # Apply action
                            inner = action[action.index("{") + 7 : action.index("}")]
                            inner = inner.strip()
                            if inner == "$0" or inner == "*":
                                results.append(line)
                            else:
                                selected = []
                                for part in inner.split(","):
                                    part = part.strip()
                                    if part.startswith("$"):
                                        try:
                                            idx = int(part[1:])
                                            if 0 < idx <= len(fields):
                                                selected.append(fields[idx - 1])
                                        except ValueError:
                                            pass
                                if selected:
                                    results.append(" ".join(selected))
                    except Exception:
                        pass

        return "\n".join(results) if results else "(no output)"
    except Exception as e:
        return f"Error processing text with awk: {e}"


@tool
def sed(
    expression: str,
    input_text: str,
) -> str:
    """Process text with sed-style find-and-replace.

    Args:
        expression: sed-compatible expression in the form 's/pattern/replacement/flags'
                    Supports: 's/old/new/' (replace first), 's/old/new/g' (replace all),
                             's/old/new/i' (case-insensitive), 's/old/new/gi' (global + case-insensitive)
        input_text: Text to process

    Returns:
        Processed text with substitutions applied
    """
    try:
        if not expression.startswith("s/") or expression.count("/") < 3:
            return f"Error: Invalid sed expression '{expression}'. Use format: s/pattern/replacement/[flags]"

        # Parse: s/pattern/replacement/flags
        parts = expression[2:].rsplit("/", 2)  # Split from right to handle / in replacement
        if len(parts) < 2:
            return f"Error: Invalid sed expression '{expression}'. Use format: s/pattern/replacement/[flags]"

        import re

        pattern = parts[0]
        replacement = parts[1]
        flags = parts[2] if len(parts) > 2 else ""

        flags = flags.lower()
        case_insensitive = "i" in flags
        global_replace = "g" in flags
        compile_flags = re.IGNORECASE if case_insensitive else 0

        def _replace(m: re.Match) -> str:
            result = replacement
            # Handle & for matched text in replacement
            result = result.replace("&", m.group(0))
            # Handle backreferences \1, \2, etc.
            for i in range(9, 0, -1):
                result = result.replace(
                    f"\\{i}",
                    m.group(i) if m.lastindex and i <= m.lastindex else "",
                )
            return result

        compiled = re.compile(pattern, flags=compile_flags)

        if global_replace:
            result_text = compiled.sub(_replace, input_text)
        else:
            result_text = compiled.subn(_replace, input_text, count=1)[0]

        return result_text
    except Exception as e:
        return f"Error processing text with sed: {e}"


@tool
def bash_background(
    command: str,
    working_dir: str | None = None,
    tool_context: ToolContext | None = None,
) -> str:
    """Start a background shell session and execute commands.

    Args:
        command: Shell command to execute in the background session
        working_dir: Working directory for the session. If None, uses the session's
                   working directory.

    Returns:
        Session ID for tracking the background process
    """
    from ..core.bash_manager import get_bash_manager

    ctx = tool_context or ToolContext()
    try:
        manager = get_bash_manager()
        session_dir, denied = _scope(ctx, working_dir)
        if denied:
            return denied
        # Run the async function in a new event loop if needed
        session_id = asyncio.run(
            manager.start_session(
                command=command,
                working_dir=str(session_dir),
            )
        )
        return f"Started bash session '{session_id}' in '{session_dir or '.'}'. Use bash_output to retrieve output."
    except Exception as e:
        return f"Error starting bash session: {e}"


@tool
def bash_output(
    session_id: str,
    tail_lines: int | None = None,
) -> str:
    """Retrieve output from a running or completed background bash shell.

    Args:
        session_id: Session ID returned by bash_background
        tail_lines: If set, only return the last N lines of output

    Returns:
        Command output or error message
    """
    from ..core.bash_manager import get_bash_manager

    try:
        manager = get_bash_manager()
        # Extract session ID from the result message if needed
        actual_session_id = session_id.strip()
        output = manager.get_output(session_id=actual_session_id, tail_lines=tail_lines)
        return output if output else "(no output)"
    except Exception as e:
        return f"Error retrieving bash output: {e}"


@tool
def kill_shell(
    session_id: str,
) -> str:
    """Terminate a running background bash shell.

    Args:
        session_id: Session ID of the shell to terminate

    Returns:
        Result message
    """
    from ..core.bash_manager import get_bash_manager

    try:
        manager = get_bash_manager()
        # Run async method synchronously using asyncio.run
        result = asyncio.run(manager.kill_session(session_id.strip()))
        return result
    except Exception as e:
        return f"Error killing shell: {e}"


# ============================================================================
# Web & Search Tools
# ============================================================================


@tool
def web_search(
    query: str,
    num_results: int = 5,
) -> str:
    """Search the web for current information.

    Automatically uses Tavily (preferred) or DuckDuckGo based on configuration.

    Args:
        query: Search query string
        num_results: Number of results to return (default: 5)

    Returns:
        Formatted search results with titles and snippets
    """
    from ..core.config import get_config

    try:
        config = get_config()
        provider = config.search.provider

        # Try Tavily first
        if provider == "tavily":
            try:
                from ..core.config import SecretManager

                api_key = SecretManager.get_api_key("tavily")

                if api_key:
                    try:
                        from tavily import TavilyClient

                        client = TavilyClient(api_key=api_key)
                        response = client.search(
                            query=query,
                            max_results=num_results,
                            include_images=False,
                        )
                        results = response.get("results", [])

                        if results:
                            output = []
                            for i, result in enumerate(results, 1):
                                output.append(f"{i}. {result.get('title', 'No title')}")
                                output.append(f"   URL: {result.get('url', 'N/A')}")
                                snippet = result.get("content", "")[:200]
                                output.append(f"   {snippet}...")
                                if result.get("score"):
                                    output.append(
                                        f"   Relevance: {result['score']:.2f}"
                                    )
                                if result.get("published_date"):
                                    output.append(
                                        f"   Date: {result['published_date']}"
                                    )
                                output.append("")
                            return "\n".join(output)
                    except ImportError:
                        error_msg = "Error: Tavily not installed. Install with: pip install tavily-python"
                    except Exception as e:
                        error_msg = f"Tavily search failed: {e}"
                else:
                    error_msg = "Error: Tavily API key not configured. Please set TAVILY_API_KEY or save via: openagent config save-key"
            except Exception as e:
                error_msg = f"Tavily initialization failed: {e}"

            # Fallback to DuckDuckGo
            if (
                config.search.fallback_to_duckduck
                and "tavily" not in str(error_msg).lower()
            ):
                try:
                    from duckduckgo_search import DDGS

                    with DDGS() as ddgs:
                        results = list(ddgs.text(query, max_results=num_results))

                    if results:
                        output = []
                        for i, result in enumerate(results, 1):
                            output.append(f"{i}. {result.get('title', 'No title')}")
                            output.append(f"   URL: {result.get('href', 'N/A')}")
                            snippet = result.get("body", "")[:200]
                            output.append(f"   {snippet}...")
                            output.append("")
                        return "\n".join(output)
                    return "No search results found."
                except ImportError:
                    return "Error: Please install duckduckgo-search (pip install duckduckgo-search)"
                except Exception as e:
                    return f"Search failed: {e}"

            return error_msg if provider == "tavily" else f"Search failed: {error_msg}"

        # DuckDuckGo as primary
        else:
            try:
                from duckduckgo_search import DDGS

                with DDGS() as ddgs:
                    results = list(ddgs.text(query, max_results=num_results))

                if not results:
                    return "No search results found."

                output = []
                for i, result in enumerate(results, 1):
                    output.append(f"{i}. {result.get('title', 'No title')}")
                    output.append(f"   URL: {result.get('href', 'N/A')}")
                    snippet = result.get("body", "")[:200]
                    output.append(f"   {snippet}...")
                    output.append("")

                return "\n".join(output)
            except ImportError:
                return "Error: Please install duckduckgo-search (pip install duckduckgo-search)"
            except Exception as e:
                return f"Search failed: {e}"

    except Exception as e:
        return f"Search configuration error: {e}"


@tool
def web_fetch(
    url: str,
) -> str:
    """Fetch and read the contents of a URL.

    Args:
        url: URL to fetch

    Returns:
        Page content as text, or error message if fetch fails
    """
    try:
        import httpx  # noqa: F401
    except ImportError:
        return "Error: Please install httpx (pip install httpx)"

    try:
        response = httpx.get(url, timeout=10.0)
        response.raise_for_status()

        # Try to detect encoding
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            import re as regex_module

            # Remove script and style tags
            cleaned = regex_module.sub(
                r"<script.*?</script>",
                "",
                response.text,
                flags=regex_module.IGNORECASE | regex_module.DOTALL,
            )
            cleaned = regex_module.sub(
                r"<style.*?</style>",
                "",
                cleaned,
                flags=regex_module.IGNORECASE | regex_module.DOTALL,
            )
            # Remove HTML tags
            text = regex_module.sub(r"<[^>]+>", "", cleaned)
            return f"Content from {url}:\n\n{text[:5000]}"  # Limit to 5000 chars
        else:
            return f"Content from {url}:\n\n{response.text[:5000]}"

    except httpx.TimeoutException:
        return "Error: Request timed out."
    except httpx.HTTPStatusError as e:
        return f"Error: HTTP {e.response.status_code} - {e.response.reason_phrase}"
    except Exception as e:
        return f"Fetch failed: {e}"


# ============================================================================
# Workflow Tools
# ============================================================================


@tool
def todo_write(
    tasks: list[dict[str, str]],
) -> str:
    """Manage a task/todo list for tracking multi-step work.

    Args:
        tasks: List of task dictionaries with keys: subject (required), description, activeForm

    Returns:
        Confirmation message with created task IDs
    """
    from ..core.task_manager import get_task_manager

    try:
        manager = get_task_manager()
        task_ids = manager.create_tasks(tasks)

        result = f"Created {len(task_ids)} tasks:\n"
        for tid in task_ids:
            result += f"  - {tid}\n"
        return result
    except Exception as e:
        return f"Error creating tasks: {e}"


@tool
def todo_update(
    task_id: str,
    status: str | None = None,
    subject: str | None = None,
    description: str | None = None,
) -> str:
    """Update an existing task in the todo list.

    Args:
        task_id: ID of the task to update
        status: New status (pending, in_progress, completed, deleted)
        subject: New subject/title for the task
        description: New description for the task

    Returns:
        Confirmation message
    """
    from ..core.task_manager import TaskStatus, get_task_manager

    try:
        manager = get_task_manager()

        status_enum = None
        if status is not None:
            try:
                status_enum = TaskStatus(status)
            except ValueError:
                return f"Error: Invalid status '{status}'. Valid values: pending, in_progress, completed, deleted"

        success = manager.update_task(
            task_id=task_id,
            status=status_enum,
            subject=subject,
            description=description,
        )

        if not success:
            return f"Error: Task '{task_id}' not found."

        return f"Updated task '{task_id}' successfully."
    except Exception as e:
        return f"Error updating task: {e}"


@tool
def todo_list() -> str:
    """Get the current list of tasks.

    Returns:
        Formatted list of all tasks and their statuses
    """
    from ..core.task_manager import get_task_manager

    try:
        manager = get_task_manager()
        return manager.get_summary()
    except Exception as e:
        return f"Error getting task list: {e}"


# ============================================================================
# User Interaction Tools
# ============================================================================


@tool
def ask_user_question(
    question: str,
    options: list[str] | None = None,
    multi_select: bool = False,
) -> str:
    """Prompt the user for input or clarification.

    Args:
        question: The question to ask the user
        options: Optional list of answer choices (for multiple choice)
        multi_select: If True and options provided, allow multiple selections

    Returns:
        User's response(s) as a formatted string
    """
    result = f"Question to user:\n\n  {question}"

    if options:
        result += "\n\nOptions:"
        for i, opt in enumerate(options, 1):
            result += f"\n  {i}. {opt}"
        result += "\n\nPlease respond with the number(s) or text answer."
    else:
        result += "\n\nPlease provide your response."

    if multi_select and options:
        result += "\n(Multiple selections allowed)"

    return result


# ============================================================================
# Memory & Learning Tools
# ============================================================================


@tool(name="recall", description="Retrieve relevant past experiences, patterns, and knowledge from memory")
def recall(
    context: str = "",
    include_patterns: bool = True,
    include_preferences: bool = True,
    include_facts: bool = True,
    project_id: str | None = None,
) -> str:
    """Recall relevant information from past sessions.

    This tool queries the memory store for patterns, preferences, and facts
    that may be relevant to the current task or context.

    Args:
        context: Description of current task/situation (used for pattern matching)
        include_patterns: Include successful solution patterns (default: True)
        include_preferences: Include user preferences (default: True)
        include_facts: Include project-specific facts (default: True)
        project_id: Filter to specific project (optional, required for facts)

    Returns:
        Formatted summary of relevant memories
    """
    from ..core.memory import get_memory_store

    try:
        store = get_memory_store()
        results = []

        # Recall patterns based on context
        if include_patterns and context:
            patterns = store.query_patterns(context, limit=3)
            if patterns:
                results.append("## Relevant Past Solutions")
                for p in patterns:
                    results.append(f"- **{p.task_description}**")
                    results.append(f"  Solution: {p.solution_summary}")
                    if p.code_snippet:
                        results.append(f"  Code:\n```python\n{p.code_snippet}\n```\n")

        # Recall preferences
        if include_preferences:
            prefs = store.get_preferences()
            if prefs:
                results.append("## User Preferences")
                for pref in prefs[:5]:  # Limit to top 5 by confidence
                    results.append(f"- [{pref.category}] {pref.preference}")

        # Recall project facts (return all facts for the project, let LLM filter relevance)
        if include_facts and project_id:
            facts = store.query_project(project_id, keywords=None)
            if facts:
                results.append("## Project Knowledge")
                for fact in facts[:5]:  # Limit to top 5
                    results.append(f"- [{fact.category}] {fact.fact}")

        if not results:
            return "No relevant memories found. Proceed with the task using your general knowledge."

        return "\n\n".join(results)

    except Exception as e:
        return f"Error recalling memory: {e}"


# ============================================================================
# Extensibility Tools
# ============================================================================


@tool
def skill(
    skill_name: str,
    args: str | None = None,
) -> str:
    """Load and use agent skills (organized folders of instructions/scripts/resources).

    Args:
        skill_name: Name of the skill to load
        args: Optional arguments to pass to the skill

    Returns:
        Result from executing the skill
    """
    from ..core.skill_manager import get_skill_manager

    try:
        manager = get_skill_manager()
        return manager.execute_skill(skill_name, args)
    except Exception as e:
        return f"Error executing skill: {e}"


@tool
def slash_command(
    command: str,
    args: list[str] | None = None,
) -> str:
    """Execute custom slash commands.

    Args:
        command: Slash command to execute (e.g., "commit", "review-pr")
        args: Optional arguments for the command

    Returns:
        Result from executing the slash command
    """
    from ..core.skill_manager import get_command_registry

    try:
        registry = get_command_registry()
        return registry.execute(command, args)
    except Exception as e:
        return f"Error executing command: {e}"


# ============================================================================
# Export all tools
# ============================================================================

__all__ = [
    # File operations
    "read",
    "write",
    "edit",
    "notebook_edit",
    "glob",
    "grep",
    # Shell & process management
    "bash",
    "awk",
    "sed",
    "bash_background",
    "bash_output",
    "kill_shell",
    # Web & search
    "web_search",
    "web_fetch",
    # Workflow
    "todo_write",
    "todo_update",
    "todo_list",
    # User interaction
    "ask_user_question",
    # Memory & learning
    "recall",
    # Extensibility
    "skill",
    "slash_command",
]
