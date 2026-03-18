"""Built-in tools for OpenAgent.

This module exports all available built-in tools that can be used with the Agent class.

Tools are organized into categories:
- File Operations: read, write, edit, notebook_edit, glob, grep
- Shell & Process Management: bash (bash_background, bash_output, kill_shell require agent integration)
- Web & Search: web_search, web_fetch
- Planning & Workflow: enter_plan_mode, exit_plan_mode
- User Interaction: ask_user_question (requires agent integration)

Usage:
    from openagent.tools import read, write, edit, glob, grep, web_search, web_search

    agent = Agent(
        provider=provider,
        tools=[read, write, edit, glob, grep, web_search, web_fetch]
    )
"""

from .builtin import (
    ask_user_question,
    bash,
    bash_background,
    bash_output,
    edit,
    enter_plan_mode,
    exit_plan_mode,
    git_commit,
    git_diff,
    git_log,
    git_status,
    glob,
    grep,
    kill_shell,
    notebook_edit,
    read,
    slash_command,
    skill,
    task,
    todo_list,
    todo_update,
    todo_write,
    web_fetch,
    web_search,
    write,
)

__all__ = [
    # File operations (fully implemented)
    "read",
    "write",
    "edit",
    "notebook_edit",
    "glob",
    "grep",
    # Shell & process management
    "bash",
    "bash_background",  # Requires agent integration
    "bash_output",      # Requires agent integration
    "kill_shell",       # Requires agent integration
    # Web & search (fully implemented)
    "web_search",
    "web_fetch",
    # Git integration (fully implemented)
    "git_status",
    "git_diff",
    "git_commit",
    "git_log",
    # Agent orchestration (requires agent integration)
    "task",
    # Planning & workflow with task manager
    "todo_write",
    "todo_update",
    "todo_list",
    "enter_plan_mode",
    "exit_plan_mode",
    # User interaction (requires agent integration)
    "ask_user_question",
    # Extensibility (requires agent integration)
    "skill",
    "slash_command",
]
