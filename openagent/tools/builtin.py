"""Built-in tools for OpenAgent framework."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from ..core.tool import tool


def _is_path_safe(file_path: Path, project_root: str) -> bool:
    """Check if path is within project root, safe against prefix bypass."""
    try:
        file_path.resolve().is_relative_to(Path(project_root).resolve())
        return True
    except ValueError:
        return False


# ============================================================================
# File Operations Tools
# ============================================================================

@tool(retry=True, max_retries=3, base_delay=0.5)
def read(
    path: str,
    line_start: int | None = None,
    line_end: int | None = None,
) -> str:
    """Read files (including images) by absolute path.

    Args:
        path: Absolute path to the file
        line_start: Optional starting line number (1-indexed). If None, starts from beginning.
        line_end: Optional ending line number (1-indexed). If None, reads to end of file.

    Returns:
        File contents as string, or base64-encoded image data for image files
    """
    file_path = Path(path).resolve()

    # Security check: ensure path is within allowed directory if set
    project_root = os.environ.get('AGENT_PROJECT_ROOT')
    if project_root and not _is_path_safe(file_path, project_root):
        return f"Error: Access denied. File '{file_path}' is outside the project root '{project_root}'."

    if not file_path.exists():
        return f"Error: File '{path}' does not exist."

    if not file_path.is_file():
        if file_path.is_dir():
            return f"Error: '{path}' is a directory, not a file."
        return f"Error: '{path}' does not exist or is not accessible."

    # Check for image files
    image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg'}
    if file_path.suffix.lower() in image_extensions:
        import base64
        with open(file_path, 'rb') as f:
            encoded = base64.b64encode(f.read()).decode('utf-8')
        return f"[Image file ({file_path.suffix}): {len(encoded)} bytes of base64 data]"

    try:
        content = file_path.read_text(encoding='utf-8')

        # Apply line range if specified
        if line_start is not None or line_end is not None:
            lines = content.splitlines()
            start_idx = (line_start - 1) if line_start is not None else 0
            end_idx = line_end if line_end is not None else len(lines)

            # Handle negative indices and out of bounds
            start_idx = max(0, start_idx)
            end_idx = min(len(lines), end_idx) if end_idx >= 0 else len(lines) + end_idx

            content = '\n'.join(lines[start_idx:end_idx])

        return content
    except Exception as e:
        return f"Error reading file: {e}"


@tool(retry=True, max_retries=3, base_delay=0.5)
def write(
    path: str,
    content: str,
    create_parents: bool = True,
) -> str:
    """Create or overwrite files.

    Args:
        path: Absolute path to the file to create/overwrite
        content: Content to write to the file
        create_parents: If True, create parent directories as needed (default: True)

    Returns:
        Success message with file size
    """
    file_path = Path(path).resolve()

    # Security check: ensure path is within allowed directory if set
    project_root = os.environ.get('AGENT_PROJECT_ROOT')
    if project_root and not _is_path_safe(file_path, project_root):
        return f"Error: Access denied. File '{file_path}' is outside the project root '{project_root}'."

    if create_parents:
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return f"Error creating parent directories: {e}"

    try:
        file_path.write_text(content, encoding='utf-8')
        size = file_path.stat().st_size
        return f"Successfully wrote {size} bytes to '{path}'."
    except Exception as e:
        return f"Error writing file: {e}"


@tool(retry=True, max_retries=3, base_delay=0.5)
def edit(
    path: str,
    find: str,
    replace: str,
    expected_replacements: int | None = None,
) -> str:
    """Make targeted edits to existing files using find-and-replace.

    Args:
        path: Absolute path to the file to edit
        find: String to search for in the file
        replace: Replacement string
        expected_replacements: Optional. If set, validates that exactly this many replacements are made.

    Returns:
        Result message with number of replacements and unified diff showing changes
    """
    file_path = Path(path).resolve()

    # Security check: ensure path is within allowed directory if set
    project_root = os.environ.get('AGENT_PROJECT_ROOT')
    if project_root and not _is_path_safe(file_path, project_root):
        return f"Error: Access denied. File '{file_path}' is outside the project root '{project_root}'."

    if not file_path.exists():
        return f"Error: File '{path}' does not exist."

    if not file_path.is_file():
        if file_path.is_dir():
            return f"Error: '{path}' is a directory, not a file."
        return f"Error: '{path}' does not exist or is not accessible."

    try:
        content = file_path.read_text(encoding='utf-8')

        # Count matches (non-overlapping)
        count = len(re.findall(re.escape(find), content))

        if count == 0:
            return f"No occurrences of '{find}' found in '{path}'."

        new_content = content.replace(find, replace)

        file_path.write_text(new_content, encoding='utf-8')

        # Generate unified diff showing exact changes
        import difflib
        old_lines = content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        # Use relative paths in diff headers for cleaner output
        try:
            rel_path = os.path.relpath(str(file_path))
        except Exception:
            rel_path = str(file_path)

        diff_generator = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=rel_path,
            tofile=rel_path,
            lineterm=''
        )
        diff_output = ''.join(diff_generator)

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
    file_path = Path(path).resolve()

    # Security check: ensure path is within allowed directory if set
    project_root = os.environ.get('AGENT_PROJECT_ROOT')
    if project_root and not _is_path_safe(file_path, project_root):
        return f"Error: Access denied. File '{file_path}' is outside the project root '{project_root}'."

    if not file_path.exists():
        return f"Error: Notebook '{path}' does not exist."

    if file_path.suffix.lower() != '.ipynb':
        return f"Error: '{path}' is not a Jupyter notebook (.ipynb)."

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            nb = json.load(f)

        if 'cells' not in nb or len(nb['cells']) == 0:
            return "Error: Notebook has no cells."

        # Handle negative indices
        actual_index = cell_index
        if actual_index < 0:
            actual_index = len(nb['cells']) + actual_index

        if actual_index < 0 or actual_index >= len(nb['cells']):
            return f"Error: Cell index {cell_index} is out of range (notebook has {len(nb['cells'])} cells)."

        cell = nb['cells'][actual_index]

        # Update the cell
        cell['source'] = new_source if isinstance(new_source, list) else [new_source]
        cell['cell_type'] = cell_type

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(nb, f, indent=2, ensure_ascii=False)

        return f"Successfully updated cell {cell_index} ({cell_type}) in '{path}'."
    except Exception as e:
        return f"Error editing notebook: {e}"


@tool
def glob(pattern: str, path: str | None = None, max_results: int = 100) -> str:
    """Search for files by pattern (replaces find/ls).

    Args:
        pattern: Glob pattern to search for (e.g., "*.py", "**/*.md")
        path: Base directory to search in. If None, searches current working directory.
              For security, this should be within the project folder.
        max_results: Maximum number of results to return (default: 100)

    Returns:
        Newline-separated list of matching file paths
    """
    base = Path(path).resolve() if path else Path.cwd()

    # Security check: ensure path is within allowed directory if set
    project_root = os.environ.get('AGENT_PROJECT_ROOT')
    if project_root and not _is_path_safe(Path(base), project_root):
        return f"Error: Access denied. Path '{base}' is outside the project root '{project_root}'."

    if not base.exists():
        return f"Error: Directory '{path}' does not exist."

    if not base.is_dir():
        return f"Error: '{path}' is not a directory."

    try:
        results = []
        # Handle ** patterns for recursive search
        if '**' in pattern:
            # Recursive search - split on **
            parts = pattern.split('**')
            prefix_pattern = parts[0].rstrip('/')
            suffix_pattern = parts[-1].lstrip('/')

            if prefix_pattern:
                prefix_base = base / Path(prefix_pattern)
                if prefix_base.exists():
                    for root, dirs, files in prefix_base.rglob(suffix_pattern.lstrip('/')):
                        for file in files:
                            match_path = root / file
                            results.append(str(match_path.resolve()))
            else:
                # Search from base directory
                search_pattern = suffix_pattern.lstrip('/')
                for root, dirs, files in base.rglob(search_pattern):
                    for file in files:
                        results.append(str((root / file).resolve()))
        else:
            # Simple pattern search
            results = [str(p.resolve()) for p in base.glob(pattern)]

        # Sort and limit results
        results.sort()
        results = results[:max_results]

        if not results:
            return f"No files found matching pattern '{pattern}' in '{base}'."

        return '\n'.join(results) + f"\n\nFound {len(results)} file(s)."
    except Exception as e:
        return f"Error searching for files: {e}"


@tool
def grep(
    pattern: str,
    path: str | None = None,
    regex: bool = True,
    max_results: int = 50,
    context_lines: int = 2,
) -> str:
    """Search file contents by keyword/regex (replaces grep/rg).

    Args:
        pattern: Pattern to search for
        path: Base directory or file to search in. If None, searches current working directory.
              For security, this should be within the project folder.
        regex: If True, treat pattern as a regular expression (default: True)
        max_results: Maximum number of matches to return (default: 50)
        context_lines: Number of lines of context before and after each match (default: 2)

    Returns:
        Formatted search results with file paths, line numbers, and matching content
    """
    base = Path(path).resolve() if path else Path.cwd()

    # Security check: ensure path is within allowed directory if set
    project_root = os.environ.get('AGENT_PROJECT_ROOT')
    if project_root and not _is_path_safe(Path(base), project_root):
        return f"Error: Access denied. Path '{base}' is outside the project root '{project_root}'."

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
            for p in base.rglob('*'):
                if p.is_file() and not p.name.startswith('.') and p.suffix not in {'.pyc', '.class', '.bin'}:
                    files_to_search.append(p)

        for file_path in files_to_search:
            try:
                content = file_path.read_text(encoding='utf-8', errors='ignore')
                lines = content.splitlines()

                for i, line in enumerate(lines):
                    if compiled_pattern.search(line):
                        # Get context lines
                        start = max(0, i - context_lines)
                        end = min(len(lines), i + context_lines + 1)
                        context = lines[start:end]

                        results.append({
                            'file': str(file_path.resolve()),
                            'line': i + 1,
                            'content': line.strip(),
                            'context': context,
                        })

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
        if r['file'] not in by_file:
            by_file[r['file']] = []
        by_file[r['file']].append(r)

    for file_path, matches in by_file.items():
        output.append(f"\n{file_path}:")
        for m in matches:
            output.append(f"  Line {m['line']}: {m['content']}")

    return '\n'.join(output) + f"\n\nFound {len(results)} match(es) across {len(by_file)} file(s)."


# ============================================================================
# Shell & Process Management Tools
# ============================================================================

@tool(retry=True, max_retries=3, base_delay=0.5)
def bash(
    command: str,
    timeout: int | None = None,
    background: bool = False,
    working_dir: str | None = None,
) -> str:
    """Execute shell commands with optional timeout and background execution.

    Args:
        command: Shell command to execute
        timeout: Optional timeout in seconds. If exceeded, process is terminated.
        background: If True, run asynchronously and return immediately with session ID.
                   Use bash_output to retrieve results later.
        working_dir: Working directory for the command. If None, uses current directory.

    Returns:
        Command output (stdout), or session ID for background processes
    """
    # For background execution, we'll use a shared dictionary managed by the agent
    if background:
        return "Error: Background execution requires special handling. Use bash_background instead."

    try:
        # Set up working directory
        cwd = Path(working_dir).resolve() if working_dir else None
        if cwd and not cwd.exists():
            return f"Error: Working directory '{working_dir}' does not exist."

        # Security check: ensure working_dir is within allowed directory if set
        project_root = os.environ.get('AGENT_PROJECT_ROOT')
        if cwd and project_root and not _is_path_safe(Path(cwd), project_root):
            return f"Error: Access denied. Working directory '{cwd}' is outside the project root '{project_root}'."

        # Execute command
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
async def bash_background(
    command: str,
    working_dir: str | None = None,
) -> str:
    """Start a background shell session and execute commands.

    Args:
        command: Shell command to execute in the background session
        working_dir: Working directory for the session. If None, uses current directory.

    Returns:
        Session ID for tracking the background process
    """
    from ..core.bash_manager import get_bash_manager

    try:
        manager = get_bash_manager()
        session_id = await manager.start_session(command=command, working_dir=working_dir)
        return f"Started bash session '{session_id}' in '{working_dir or '.'}'. Use bash_output to retrieve output."
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
        actual_session_id = session_id.strip()
        output = manager.get_output(session_id=actual_session_id, tail_lines=tail_lines)
        return output if output else "(no output)"
    except Exception as e:
        return f"Error retrieving bash output: {e}"


@tool
async def kill_shell(
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
        result = await manager.kill_session(session_id.strip())
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

    Args:
        query: Search query string
        num_results: Number of results to return (default: 5)

    Returns:
        Formatted search results with titles and snippets
    """
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
            snippet = result.get('body', '')[:200]
            output.append(f"   {snippet}...")
            output.append("")

        return '\n'.join(output)
    except ImportError:
        return "Error: Please install duckduckgo_search (pip install duckduckgo-search)"
    except Exception as e:
        return f"Search failed: {e}"


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
        content_type = response.headers.get('content-type', '')
        if 'text/html' in content_type:
            # Remove script and style tags
            cleaned = re.sub(r'<script.*?</script>', '', response.text, flags=re.IGNORECASE | re.DOTALL)
            cleaned = re.sub(r'<style.*?</style>', '', cleaned, flags=re.IGNORECASE | re.DOTALL)
            # Remove HTML tags
            text = re.sub(r'<[^>]+>', '', cleaned)
            return f"Content from {url}:\n\n{text[:5000]}"  # Limit to 5000 chars
        else:
            return f"Content from {url}:\n\n{response.text[:5000]}"

    except httpx.TimeoutException:
        return "Error: Request timed out."
    except httpx.HTTPStatusError as e:
        return f"Error: HTTP {e.response.status_code} - {e.response.reason_phrase}"
    except Exception as e:
        return f"Fetch failed: {e}"


@tool(retry=True, max_retries=2, base_delay=0.5)
def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    timeout: float = 30.0,
) -> str:
    """Send an HTTP request and return the response.

    Supports GET, POST, PUT, PATCH, DELETE methods. Body is sent as JSON
    for POST/PUT/PATCH requests unless a Content-Type header is explicitly set.

    Args:
        url: The URL to send the request to (http:// or https:// only)
        method: HTTP method (default: GET)
        headers: Optional dict of headers to include
        body: Optional request body (sent as JSON for POST/PUT/PATCH by default)
        timeout: Request timeout in seconds (default: 30)

    Returns:
        Response body text with status code and headers summary
    """
    try:
        import httpx
    except ImportError:
        return "Error: Please install httpx (pip install httpx)"

    method = method.upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
        return f"Error: Unsupported method '{method}'. Use GET, POST, PUT, PATCH, DELETE, or HEAD."

    parsed = _parse_url(url)
    if parsed is None:
        return "Error: URL must use http:// or https:// scheme."

    try:
        _validate_url_for_ssrf(url)
        follow_redirects = method in ("GET", "HEAD")
        response = httpx.request(
            method=method,
            url=url,
            headers=headers,
            content=body if method in ("POST", "PUT", "PATCH") else None,
            json=None if method not in ("POST", "PUT", "PATCH") or body is None else None,
            timeout=timeout,
            follow_redirects=follow_redirects,
        )

        # Truncate large responses
        resp_text = response.text[:10000]
        if len(response.text) > 10000:
            resp_text += "\n\n... (response truncated, 10000 char limit)"

        headers_summary = "\n".join(
            f"  {k}: {v}" for k, v in response.headers.items() if k.lower() not in ("set-cookie",)
        )

        return (
            f"Status: {response.status_code} {response.reason_phrase}\n"
            f"Headers:\n{headers_summary}\n\n"
            f"{resp_text}"
        )

    except httpx.TimeoutException:
        return f"Error: Request to {url} timed out after {timeout}s."
    except httpx.ConnectError as e:
        return f"Error: Could not connect to {url}: {e}"
    except httpx.HTTPStatusError as e:
        return f"Error: HTTP {e.response.status_code} - {e.response.reason_phrase}\n{e.response.text[:2000]}"
    except Exception as e:
        return f"Request failed: {e}"


def _parse_url(url: str) -> str | None:
    """Return the URL if scheme is http/https, else None."""

    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc:
        return None
    return url


def _validate_url_for_ssrf(url: str) -> None:
    """Block requests to internal/private IP ranges to prevent SSRF."""
    import socket

    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return

    # Block obvious internal hostnames
    internal_hostnames = {"localhost", "metadata", "metadata.google.internal", "instance-data"}
    if hostname.lower() in internal_hostnames:
        raise SecurityError(f"Request to internal hostname '{hostname}' is blocked.")

    # Resolve and check IP
    try:
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for info in addr_info:
            ip = info[4][0]
            if _is_private_ip(ip):
                raise SecurityError(f"Request to internal IP '{ip}' (resolved from '{hostname}') is blocked.")
    except SecurityError:
        raise
    except Exception:
        pass  # DNS resolution failure will be caught by httpx


def _is_private_ip(ip: str) -> bool:
    """Check if an IPv4 address is in a private/reserved range."""
    parts = ip.split(".")
    if len(parts) != 4:
        return True  # Non-standard, block it

    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return True

    a, b = octets[0], octets[1]

    # 10.0.0.0/8
    if a == 10:
        return True
    # 172.16.0.0/12
    if a == 172 and 16 <= b <= 31:
        return True
    # 192.168.0.0/16
    if a == 192 and b == 168:
        return True
    # 127.0.0.0/8 (loopback)
    if a == 127:
        return True
    # 169.254.0.0/16 (link-local, cloud metadata)
    if a == 169 and b == 254:
        return True
    # 0.0.0.0/8
    if a == 0:
        return True
    # 224.0.0.0/4 (multicast) and 240.0.0.0/4 (reserved)
    if a >= 224:
        return True

    return False


class SecurityError(Exception):
    """Raised when a request violates security constraints."""
    pass


# ============================================================================
# Planning & Workflow Tools
# ============================================================================

@tool
def enter_plan_mode(
    reason: str | None = None,
) -> str:
    """Switch into planning mode to design implementation before coding.

    Args:
        reason: Optional reason for entering plan mode

    Returns:
        Confirmation message
    """
    return f"Entering plan mode. Reason: {reason or 'Not specified'}. Use exit_plan_mode when ready to implement."


@tool
def exit_plan_mode(
    approved_plan: str | None = None,
) -> str:
    """Exit planning mode and begin execution of the approved plan.

    Args:
        approved_plan: The plan that was approved for implementation

    Returns:
        Confirmation message
    """
    return f"Exiting plan mode. Approved plan: {approved_plan or 'Not specified'}. Starting implementation."


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
def todo_list(
) -> str:
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


@tool
def submit_feedback(
    rating: int,
    comment: str = "",
) -> str:
    """Submit feedback about the agent's performance.

    This helps the agent learn from user feedback and improve over time.
    Feedback is only collected if learning is enabled in the agent.

    Args:
        rating: Rating from 1-5 (1=very poor, 2=poor, 3=average, 4=good, 5=excellent)
        comment: Optional detailed feedback about what went well or poorly

    Returns:
        Confirmation message
    """
    if not 1 <= rating <= 5:
        return "Error: Rating must be between 1 and 5."

    # Try to get the current agent and submit feedback
    try:

        # Check if we're running in an interactive session with learning enabled
        frame = sys._getframe(1)
        while frame:
            if hasattr(frame.f_locals.get('self'), '_feedback_manager'):
                agent = frame.f_locals['self']
                if hasattr(agent, 'add_feedback'):
                    agent.add_feedback(rating=rating, comment=comment)
                    return f"Thank you for your feedback! Rating: {rating}/5{' | Comment: ' + comment if comment else ''}"
            frame = frame.f_back

        # Fallback: learning may not be enabled or we're not in an agent context
        return f"Feedback recorded (learning disabled): Rating: {rating}/5{' | Comment: ' + comment if comment else ''}. Enable learning to help the agent improve."
    except Exception as e:
        return f"Could not submit feedback: {e}"


@tool
def get_learning_stats() -> str:
    """Get statistics about what the agent has learned.

    Shows tool usage patterns, success rates, and feedback summary if learning is enabled.

    Returns:
        Formatted learning statistics or message if learning is disabled
    """
    try:

        # Check if we're running in an interactive session with learning enabled
        frame = sys._getframe(1)
        while frame:
            if hasattr(frame.f_locals.get('self'), '_tool_tracker'):
                agent = frame.f_locals['self']
                if hasattr(agent, 'get_learning_stats'):
                    stats = agent.get_learning_stats()
                    return format_learning_stats(stats)
            frame = frame.f_back

        return "Learning is not enabled. Enable it with enable_learning=True when creating the agent."
    except Exception as e:
        return f"Could not get learning stats: {e}"


def format_learning_stats(stats: dict) -> str:
    """Format learning statistics for display."""
    if not stats.get("learning_enabled"):
        return "Learning is not enabled."

    output = ["### Learning Statistics"]

    # Tool usage stats
    tool_usage = stats.get("tool_usage", {})
    if tool_usage:
        output.append("\n**Tool Usage:**")
        for tool_name, tool_stats in sorted(tool_usage.items(), key=lambda x: -x[1].get("total_uses", 0)):
            success_rate = tool_stats.get("success_rate", 0)
            total_uses = tool_stats.get("total_uses", 0)
            bar = "█" * int(success_rate * 10) + "░" * (10 - int(success_rate * 10))
            output.append(f"  {tool_name}: [{bar}] {success_rate:.0%} ({total_uses} uses)")

    # Feedback summary
    feedback = stats.get("feedback_summary")
    if feedback:
        output.append("\n**Feedback:**")
        output.append(f"  Total ratings: {feedback.get('total', 0)}")
        output.append(f"  Average rating: {feedback.get('average', 0):.1f}/5")

    return "\n".join(output)


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
# Git Integration Tools
# ============================================================================

@tool
def git_status() -> str:
    """Get the current git status of the repository.

    Returns:
        Formatted output showing modified, staged, and untracked files
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return "Error: Not a git repository or git command failed."

        lines = result.stdout.strip().split('\n')
        if not lines or (len(lines) == 1 and not lines[0]):
            return "Working tree is clean. No changes staged for commit."

        modified = []
        staged = []
        untracked = []

        for line in lines:
            if not line.strip():
                continue
            # Format: XX YYY file_path
            # XX = status (modified, added, deleted, etc.)
            # YYY = secondary status
            status = line[:3]
            filepath = line[3:].strip()

            if status[0] == '?':
                untracked.append(filepath)
            elif status[0] in ('M', 'A', 'D', 'R', 'C'):
                staged.append(filepath)
            else:
                modified.append(filepath)

        output = []
        if staged:
            output.append("\nStaged changes:")
            for f in staged:
                output.append(f"  M {f}")
        if modified:
            output.append("\nModified (unstaged):")
            for f in modified:
                output.append(f"  M {f}")
        if untracked:
            output.append("\nUntracked files:")
            for f in untracked:
                output.append(f"  ? {f}")

        return '\n'.join(output) + "\n"
    except subprocess.TimeoutExpired:
        return "Error: git status command timed out."
    except Exception as e:
        return f"Error getting git status: {e}"


@tool
def git_diff(file_path: str | None = None, staged: bool = False) -> str:
    """Show diff for files in the repository.

    Args:
        file_path: Optional specific file to show diff for. If None, shows all changes.
        staged: If True, show diff of staged changes only.

    Returns:
        Git diff output with color-coded additions/deletions
    """
    try:
        cmd = ["git", "diff"]
        if staged:
            cmd = ["git", "diff", "--staged"]
        if file_path:
            cmd.extend(["--", file_path])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return "Error: git diff command failed."

        output = result.stdout.strip()
        if not output:
            file_msg = f" in '{file_path}'" if file_path else ""
            scope = "staged changes" if staged else "changes"
            return f"No {scope}{file_msg} to display."

        # Format diff with line numbers and color hints
        lines = output.split('\n')
        formatted = []
        current_line_num = None

        for line in lines:
            if not line:
                continue
            if line.startswith('@@'):
                # Hunk header - bold cyan
                formatted.append(f"\033[1;36m{line}\033[0m")
                # Extract starting line number
                match = re.search(r'-\d+,', line)
                if match:
                    current_line_num = int(match.group()[1:])
            elif line.startswith('+') and not line.startswith('+++'):
                # Addition - green with line number
                num_str = str(current_line_num) if current_line_num else ""
                formatted.append(f"\033[32m{num_str} {line[1:]}\033[0m")
                if current_line_num is not None:
                    current_line_num += 1
            elif line.startswith('-') and not line.startswith('---'):
                # Deletion - red with line number
                num_str = str(current_line_num) if current_line_num else ""
                formatted.append(f"\033[31m{num_str} {line[1:]}\033[0m")
                if current_line_num is not None:
                    current_line_num += 1
            elif line.startswith('diff') or line.startswith('index') or '+++' in line or '---' in line:
                # File headers - dim white
                formatted.append(f"\033[2;37m{line}\033[0m")
            else:
                # Context - dim with line number
                num_str = str(current_line_num) if current_line_num else ""
                formatted.append(f"\033[2m{num_str} {line}\033[0m")
                if current_line_num is not None:
                    current_line_num += 1

        return '\n'.join(formatted) + "\n"
    except subprocess.TimeoutExpired:
        return "Error: git diff command timed out."
    except Exception as e:
        return f"Error getting git diff: {e}"


@tool
def git_commit(
    message: str,
    amend: bool = False,
    allow_empty: bool = False,
) -> str:
    """Create a new commit with the given message.

    Args:
        message: Commit message
        amend: If True, amend the last commit instead of creating a new one
        allow_empty: If True, allow empty commits (useful for hooks)

    Returns:
        Result message with commit hash and summary
    """
    try:

        cmd = ["git", "commit", "-m", message]
        if amend:
            cmd.append("--amend")
        if allow_empty:
            cmd.append("--allow-empty")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or "git commit failed."
            # Check for common cases
            if "nothing to commit" in error_msg.lower():
                return "Error: No changes to commit. Use git status to see uncommitted changes."
            elif "amend" in error_msg.lower() and "no previous commit" in error_msg.lower():
                return "Error: Cannot amend - no previous commit. Use --no-amend flag or create initial commit first."
            return f"Error: {error_msg}"

        # Parse output for commit hash
        lines = result.stdout.strip().split('\n')
        summary_line = next((line for line in lines if '[' in line and ']' in line), None)

        if summary_line:
            return f"Committed:\n  {summary_line}"
        else:
            return f"Successfully created commit.\n{result.stdout.strip()}"
    except subprocess.TimeoutExpired:
        return "Error: git commit command timed out."
    except Exception as e:
        return f"Error creating commit: {e}"


@tool
def git_log(
    n: int = 10,
    oneline: bool = True,
) -> str:
    """Show recent commit history.

    Args:
        n: Number of commits to show (default: 10)
        oneline: If True, show one line per commit; otherwise full format

    Returns:
        Formatted commit log
    """
    try:

        cmd = ["git", "log"]
        if oneline:
            cmd.extend(["-n", str(n), "--oneline"])
        else:
            cmd.extend(["-n", str(n)])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return "Error: git log command failed."

        output = result.stdout.strip()
        if not output:
            return "No commits found in this repository."

        # Add line numbers to each commit for easy reference
        lines = output.split('\n')
        numbered = []
        for i, line in enumerate(lines, 1):
            numbered.append(f"  {i}. {line}")

        return '\n'.join(numbered) + f"\n\nShowing last {min(n, len(lines))} commit(s)."
    except subprocess.TimeoutExpired:
        return "Error: git log command timed out."
    except Exception as e:
        return f"Error getting git log: {e}"


# ============================================================================
# Text Processing Tools
# ============================================================================

@tool
def awk(
    input_text: str,
    delimiter: str = "\t",
    select_columns: list[int] | None = None,
    filter_column: int | None = None,
    filter_operator: str | None = None,
    filter_value: str | None = None,
    sort_column: int | None = None,
    sort_reverse: bool = False,
    unique: bool = False,
    unique_column: int | None = None,
    header: bool = True,
    aggregate_column: int | None = None,
    aggregate_function: str | None = None,
    group_by_column: int | None = None,
) -> str:
    """Process structured text — select columns, filter rows, sort, deduplicate, and aggregate (awk-like).

    Args:
        input_text: The tabular text to process (one row per line)
        delimiter: Field separator (default: tab; use ',' for CSV, '|' for pipe-delimited)
        select_columns: List of 1-based column indices to keep (e.g., [1, 3])
        filter_column: 1-based column index to filter on
        filter_operator: Comparison operator: eq, neq, gt, lt, gte, lte, contains, not_contains
        filter_value: Value to compare against when filtering
        sort_column: 1-based column index to sort by
        sort_reverse: If True, sort descending (default: False = ascending)
        unique: If True, deduplicate rows (or deduplicate by unique_column if specified)
        unique_column: 1-based column to deduplicate by (when unique=True)
        header: If True, treat first line as header and preserve it (default: True)
        aggregate_column: 1-based column index for numeric aggregation
        aggregate_function: One of sum, mean, min, max, count
        group_by_column: 1-based column to group by (used with aggregate_column + aggregate_function)

    Returns:
        Processed text output
    """
    try:
        lines = [line.rstrip("\n") for line in input_text.split("\n") if line.strip()]
        if not lines:
            return "(empty input)"

        header_line = lines[0] if header else None
        data_lines = lines[1:] if header else lines

        # Parse into rows
        def split_line(line: str) -> list[str]:
            return line.split(delimiter)

        header_fields = split_line(header_line) if header_line else None
        rows = [split_line(line) for line in data_lines]

        # Filter rows
        if filter_column is not None and filter_operator and filter_value is not None:
            col_idx = filter_column - 1
            rows = _filter_rows(rows, col_idx, filter_operator, filter_value)

        # Select columns
        if select_columns is not None:
            header_fields = [header_fields[i - 1] for i in select_columns] if header_fields else None
            rows = [[row[i - 1] for i in select_columns if i - 1 < len(row)] for row in rows]

        # Sort
        if sort_column is not None:
            col_idx = sort_column - 1
            rows = _sort_rows(rows, col_idx, sort_reverse)

        # Deduplicate
        if unique:
            col_idx = unique_column - 1 if unique_column is not None else None
            rows = _unique_rows(rows, col_idx)

        # Aggregate
        if aggregate_column is not None and aggregate_function:
            agg_idx = aggregate_column - 1
            if group_by_column is not None:
                group_idx = group_by_column - 1
                result_lines = _aggregate_grouped(rows, group_idx, agg_idx, aggregate_function, header_fields)
            else:
                result_str = _aggregate_all(rows, agg_idx, aggregate_function)
                header_str = header_fields[0] if header_fields else ""
                result_lines = [[header_str, aggregate_function.upper()], [result_str, ""]]

            return _build_output(result_lines, include_header=True)

        return _build_output(
            (rows if not (aggregate_column and aggregate_function) else rows),
            header is True and aggregate_column is None,
            header_fields,
        )

    except Exception as e:
        return f"Error processing text: {e}"


def _filter_rows(rows: list[list[str]], col_idx: int, operator: str, value: str) -> list[list[str]]:
    """Filter rows by comparing a column value."""
    filtered = []
    for row in rows:
        if col_idx >= len(row):
            continue
        cell = row[col_idx]
        try:
            cell_num = float(cell)
            val_num = float(value)
            compare_nums = True
        except (ValueError, TypeError):
            cell_num = cell
            val_num = value
            compare_nums = False

        match = False
        if compare_nums:
            if operator == "eq":
                match = cell_num == val_num
            elif operator == "neq":
                match = cell_num != val_num
            elif operator == "gt":
                match = cell_num > val_num
            elif operator == "lt":
                match = cell_num < val_num
            elif operator == "gte":
                match = cell_num >= val_num
            elif operator == "lte":
                match = cell_num <= val_num
        else:
            if operator == "eq":
                match = cell == value
            elif operator == "neq":
                match = cell != value
            elif operator == "contains":
                match = value in cell
            elif operator == "not_contains":
                match = value not in cell

        if match:
            filtered.append(row)

    return filtered


def _sort_rows(rows: list[list[str]], col_idx: int, reverse: bool) -> list[list[str]]:
    """Sort rows by a column, numeric when possible."""
    def sort_key(row):
        if col_idx >= len(row):
            return (1, "")  # push short rows to the end
        val = row[col_idx]
        try:
            return (0, float(val))
        except (ValueError, TypeError):
            return (1, val)

    return sorted(rows, key=sort_key, reverse=reverse)


def _unique_rows(rows: list[list[str]], col_idx: int | None) -> list[list[str]]:
    """Remove duplicate rows (optionally by a single column)."""
    seen: set[str] = set()
    unique = []
    for row in rows:
        key = row[col_idx] if col_idx is not None and col_idx < len(row) else "\t".join(row)
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def _aggregate_all(rows: list[list[str]], col_idx: int, func: str) -> str:
    """Compute an aggregation over all values in a column."""
    values: list[float] = []
    count = 0
    for row in rows:
        if col_idx >= len(row):
            continue
        try:
            values.append(float(row[col_idx]))
        except (ValueError, TypeError):
            pass
        count += 1

    if func == "count":
        return str(count)
    if not values:
        return "0"
    if func == "sum":
        return str(sum(values))
    if func == "mean":
        return str(sum(values) / len(values))
    if func == "min":
        return str(min(values))
    if func == "max":
        return str(max(values))
    return "0"


def _aggregate_grouped(
    rows: list[list[str]], group_idx: int, agg_idx: int, func: str,
    header_fields: list[str] | None,
) -> list[list[str]]:
    """Group rows and aggregate, returning result rows."""
    groups: dict[str, list[float]] = {}
    group_order: list[str] = []
    for row in rows:
        if group_idx >= len(row) or agg_idx >= len(row):
            continue
        key = row[group_idx]
        try:
            val = float(row[agg_idx])
        except (ValueError, TypeError):
            continue
        if key not in groups:
            groups[key] = []
            group_order.append(key)
        groups[key].append(val)

    result = []
    for key in group_order:
        vals = groups[key]
        if func == "count":
            agg_val = str(len(vals))
        elif func == "sum":
            agg_val = str(sum(vals))
        elif func == "mean":
            agg_val = str(sum(vals) / len(vals))
        elif func == "min":
            agg_val = str(min(vals))
        elif func == "max":
            agg_val = str(max(vals))
        else:
            agg_val = "0"
        result.append([key, agg_val])

    return result


def _build_output(
    rows: list[list[str]],
    include_header: bool,
    header_fields: list[str] | None = None,
) -> str:
    """Build the final output string from rows and optional header."""
    lines = []
    if include_header and header_fields:
        lines.append("\t".join(header_fields))
    for row in rows:
        lines.append("\t".join(str(c) for c in row))
    return "\n".join(lines)


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
    "bash_background",
    "bash_output",
    "kill_shell",
    # Web & search
    "web_search",
    "web_fetch",
    "http_request",
    # Git integration
    "git_status",
    "git_diff",
    "git_commit",
    "git_log",
    # Planning & workflow
    "enter_plan_mode",
    "exit_plan_mode",
    "todo_write",
    "todo_update",
    "todo_list",
    # User interaction
    "ask_user_question",
    "submit_feedback",
    # Learning
    "get_learning_stats",
    # Extensibility
    "skill",
    "slash_command",
    # Text processing
    "awk",
]
