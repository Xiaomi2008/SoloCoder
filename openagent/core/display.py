"""Display utilities for CLI output formatting - Claude Code style."""

from __future__ import annotations

import sys


# ANSI color codes for terminal output
class Colors:
    """ANSI color codes for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    WHITE = "\033[97m"
    USER_INPUT = "\033[92m"  # Bright green for user input to distinguish from agent responses


def should_use_colors() -> bool:
    """Check if colors should be used in output."""
    # Check if stdout is a TTY and not running in an environment that disables colors
    return sys.stdout.isatty()


# Use color functions that respect the terminal capability
if should_use_colors():
    def bold(text: str) -> str:
        return f"{Colors.BOLD}{text}{Colors.RESET}"

    def dim(text: str) -> str:
        return f"{Colors.DIM}{text}{Colors.RESET}"

    def blue(text: str) -> str:
        return f"{Colors.BLUE}{text}{Colors.RESET}"

    def green(text: str) -> str:
        return f"{Colors.GREEN}{text}{Colors.RESET}"

    def yellow(text: str) -> str:
        return f"{Colors.YELLOW}{text}{Colors.RESET}"

    def red(text: str) -> str:
        return f"{Colors.RED}{text}{Colors.RESET}"

    def cyan(text: str) -> str:
        return f"{Colors.CYAN}{text}{Colors.RESET}"

    def magenta(text: str) -> str:
        return f"{Colors.MAGENTA}{text}{Colors.RESET}"

    def white(text: str) -> str:
        return f"{Colors.WHITE}{text}{Colors.RESET}"

    def user_input(text: str) -> str:
        """Style text as user input (bright green)."""
        return f"{Colors.USER_INPUT}{text}{Colors.RESET}"
else:
    # Fallback to plain text when colors are not available
    def bold(text: str) -> str:
        return text

    def dim(text: str) -> str:
        return text

    def blue(text: str) -> str:
        return text

    def green(text: str) -> str:
        return text

    def yellow(text: str) -> str:
        return text

    def red(text: str) -> str:
        return text

    def cyan(text: str) -> str:
        return text

    def magenta(text: str) -> str:
        return text

    def white(text: str) -> str:
        return text

    def user_input(text: str) -> str:
        """Style text as user input (bright green)."""
        return text


def code(text: str, language: str = "python") -> str:
    """Apply code block styling with appropriate colors."""
    # Use cyan for code blocks to make them stand out
    return f"{cyan(text)}"


def diff_addition(text: str) -> str:
    """Style text as a diff addition (green)."""
    return f"{green('+ ' + text.lstrip())}"


def diff_deletion(text: str) -> str:
    """Style text as a diff deletion (red)."""
    return f"{red('- ' + text.lstrip())}"


def format_diff_output(diff_text: str) -> str:
    """Format unified diff output with color coding.

    Lines starting with '+' are additions (green),
    lines starting with '-' are deletions (red),
    other lines are context (dimmed).
    """
    lines = diff_text.split('\n')
    colored_lines = []

    for line in lines:
        if line.startswith('+++') or line.startswith('---'):
            # File headers - bold white
            colored_lines.append(f"{bold(white(line))}")
        elif line.startswith('+') and not line.startswith('+++'):
            # Addition - green
            colored_lines.append(diff_addition(line[1:]))
        elif line.startswith('-') and not line.startswith('---'):
            # Deletion - red
            colored_lines.append(diff_deletion(line[1:]))
        else:
            # Context - dimmed
            colored_lines.append(dim(line))

    return '\n'.join(colored_lines)


def display_code_block(title: str, content: str, language: str = "python") -> None:
    """Display a code block with syntax highlighting colors.

    Args:
        title: Title of the code block (e.g., file path or function name)
        content: The code content to display
        language: Programming language for context
    """
    print(f"\n{bold(cyan(title))}")
    print(dim("-" * min(len(title), 50)))

    # Format diff output if detected, otherwise show as regular code
    if any(line.startswith('+') or line.startswith('-') for line in content.split('\n')):
        formatted = format_diff_output(content)
    else:
        formatted = content

    print(f"\n{cyan(formatted)}\n")


def truncate_text(text: str, max_length: int = 60) -> str:
    """Truncate text with ellipsis if too long."""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def display_tool_call_claude_style(name: str, arguments: dict, indent: int = 0) -> None:
    """Display a tool call in Claude Code style.

    Format:
      ● FunctionName(arg1=value1, arg2=...)
        ⎿  Result preview or status

    Args:
        name: The tool/function name being called
        arguments: Dictionary of argument names and values
        indent: Number of spaces for indentation (0 = top level)
    """
    prefix = "  " * indent
    args_str = ", ".join(f"{k}={truncate_text(str(v), 80)}" for k, v in arguments.items())

    # Use bullet point and cyan color like Claude Code
    print(f"{prefix}● {bold(name)}({args_str})")


def display_tool_result_claude_style(
    is_error: bool,
    content: str,
    indent: int = 0,
    max_preview_length: int = 120
) -> None:
    """Display a tool result in Claude Code style.

    Format:
      ⎿  Status message or preview of content

    Args:
        is_error: Whether the result was an error
        content: The result content to display
        indent: Number of spaces for indentation (0 = top level)
        max_preview_length: Maximum length for preview text
    """
    prefix = "  " * indent
    icon = "⎿"

    if is_error:
        color_func = red
        status = f"Error: {content[:max_preview_length]}"
    else:
        color_func = green
        # Show summary instead of raw content for Claude Code style
        lines = content.strip().split('\n')
        if len(lines) == 1:
            status = truncate_text(content, max_preview_length)
        elif len(lines) <= 3:
            status = content[:max_preview_length]
        else:
            # Multi-line result - show first line + count
            status = f"{lines[0]} ({len(lines)} lines total)"

    print(f"{prefix} {icon} {color_func(status)}")


def display_tool_result_full_claude_style(
    is_error: bool,
    content: str,
    indent: int = 0
) -> None:
    """Display a full tool result in Claude Code style.

    Args:
        is_error: Whether the result was an error
        content: The result content to display
        indent: Number of spaces for indentation (0 = top level)
    """
    prefix = "  " * indent
    icon = "⎿"
    color_func = red if is_error else green

    print(f"\n{prefix}{color_func(icon)} {bold('Result:')}\n")
    print(content)


def display_claude_code_block(
    tool_name: str,
    arguments: dict,
    result_content: str,
    is_error: bool = False,
    status_text: str | None = None
) -> None:
    """Display a complete Claude Code style block.

    Format:
      ● FunctionName(arg1=value1, ...)
        ⎿  Status or preview

    Args:
        tool_name: Name of the tool/function
        arguments: Dictionary of argument names and values
        result_content: The result content
        is_error: Whether this was an error
        status_text: Optional custom status text to display instead of auto-generated
    """
    display_tool_call_claude_style(tool_name, arguments)

    if status_text:
        color_func = red if is_error else green
        print(f"  ⎿ {color_func(status_text)}")
    else:
        display_tool_result_claude_style(is_error, result_content)


def format_file_list(files: list[str], max_display: int = 10) -> str:
    """Format a file list in Claude Code style.

    Args:
        files: List of file paths
        max_display: Maximum number of files to show before truncating

    Returns:
        Formatted string with file listing
    """
    if len(files) <= max_display:
        return "\n".join(f"  {f}" for f in files)

    # Show first N files + count
    lines = [f"\n{bold('Files found:')}\n"]
    for f in files[:max_display]:
        lines.append(f"  {f}")
    lines.append(f"\n  ... and {len(files) - max_display} more")
    return "".join(lines)


def format_grep_results_claude_style(results: list[dict]) -> str:
    """Format grep results in Claude Code style.

    Args:
        results: List of match dictionaries with file, line, content keys

    Returns:
        Formatted string for display
    """
    if not results:
        return "No matches found."

    # Group by file
    by_file: dict[str, list[dict]] = {}
    for r in results:
        if r['file'] not in by_file:
            by_file[r['file']] = []
        by_file[r['file']].append(r)

    output = [f"\n{bold('Search results:')}\n"]

    for file_path, matches in list(by_file.items())[:5]:  # Limit files shown
        output.append(f"  {cyan(file_path)}:\n")
        for m in matches[:3]:  # Limit matches per file
            output.append(f"    Line {m['line']}: {dim(m['content'])}\n")

    if len(by_file) > 5:
        output.append(f"\n  ... and {len(by_file) - 5} more files")

    return "".join(output) + f"\n\n{bold('Total:')}"


def display_diff_claude_style(
    file_path: str,
    diff_content: str,
    additions: int = 0,
    deletions: int = 0,
    tool_name: str | None = None
) -> None:
    """Display a code diff in Claude Code style with color coding.

    Format for write operations (shows file content):
      ● write("openagent/core/agent.py")
        ⎿ Added 19 lines

          22  def should_use_colors() -> bool:
          23      Check if colors should be used in output.
          24 -    return sys.stdout.isatty() and Colors.RESET in str(sys.stderr)
          25 +    # Check if stdout is a TTY...

    Format for diff operations (shows git-style diff):
      ● write("openagent/core/agent.py")
        ⎿ Added 19 lines, removed 3 lines

      @@ -124,7 +124,23 @@ class Agent:
            124              ]
            125              results = await asyncio.gather(*tool_tasks)
            126
            127 -            # Log results in Claude Code style
            127 +            # Log results in Claude Code style with diff highlighting...

    Args:
        file_path: Path to the modified file
        diff_content: Content to display (can be git-style diff or regular code)
        additions: Number of lines added (for summary)
        deletions: Number of lines deleted (for summary)
        tool_name: The actual tool name being displayed (defaults to "write")
    """
    # Use provided tool name or default to "write"
    action = tool_name if tool_name else "write"

    # Show tool call style header with the actual tool name
    print(f"  ● {bold(action)}({cyan(f'\"{file_path}\"')})")

    # Summary line with color-coded counts
    if additions > 0 or deletions > 0:
        parts = []
        if additions > 0:
            parts.append(green(f"{additions} lines"))
        if deletions > 0:
            parts.append(red(f"removed {deletions} lines"))
        status = ", ".join(parts)
        print(f"    ⎿ {status}")

    # Determine if content is a git-style diff or regular code
    lines = diff_content.split('\n')
    has_hunk_headers = any(line.startswith('@@') for line in lines)
    has_diff_markers = any(
        (line.startswith('+') and not line.startswith('+++')) or
        (line.startswith('-') and not line.startswith('---'))
        for line in lines
    )

    if has_hunk_headers or has_diff_markers:
        # Git-style diff format - show with +/- prefixes
        print()
        current_line_num: int | None = None

        for line in lines:
            if not line:
                continue
            if line.startswith('+++') or line.startswith('---'):
                continue
            elif line.startswith('@@'):
                print(f"{bold(cyan(line))}")
                parts = line.split()
                for part in parts:
                    if part.startswith('-') and ',' in part:
                        current_line_num = int(part[1:].split(',')[0])

            elif line.startswith('+') and not line.startswith('+++'):
                if current_line_num is not None:
                    print(f"{green(str(current_line_num))} {diff_addition(line[1:])}")
                    current_line_num += 1
                else:
                    print(diff_addition(line[1:]))

            elif line.startswith('-') and not line.startswith('---'):
                if current_line_num is not None:
                    print(f"{red(str(current_line_num))} {diff_deletion(line[1:])}")
                    current_line_num += 1
                else:
                    print(diff_deletion(line[1:]))

            else:
                if current_line_num is not None:
                    print(f"{dim(str(current_line_num))} {dim(line)}")
                    current_line_num += 1
                else:
                    print(dim(line))

        print()
    else:
        # Regular code content - show with line numbers only (no +/-)
        print()
        for i, line in enumerate(lines, start=1):
            if line:
                print(f"{dim(str(i).rjust(4))} {line}")
        print()
