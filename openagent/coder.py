"""Coder Agent - A chat-based coding assistant powered by OpenAgent.

This module provides a specialized Agent designed for code editing and development tasks,
inspired by Claude Code's functionality. It uses built-in tools to read, write, edit files,
execute commands, and search through projects.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .core.agent import Agent as BaseAgent
from .core.bash_manager import BashManager, get_bash_manager
from .core.skill_manager import SkillManager, get_skill_manager
from .core.task_manager import TaskManager, get_task_manager
from .provider.openai import OpenAIProvider


class CoderAgent(BaseAgent):
    """A coding-focused agent with specialized tools for development tasks.

    This agent is designed to help users write and edit code through a chat interface,
    similar to Claude Code. It has access to file operations, shell commands, and search tools.

    Args:
        provider: LLM provider instance (defaults to OpenAI if not provided)
        system_prompt: Custom system prompt for the coder agent
        max_turns: Maximum conversation turns before stopping
        working_dir: Working directory for file operations and shell commands
        bash_manager: Optional custom BashManager instance
        task_manager: Optional custom TaskManager instance
        skill_manager: Optional custom SkillManager instance

    Example:
        >>> async def main():
        ...     coder = CoderAgent()
        ...     result = await coder.run("Create a new Python file with a hello world function")
        ...     print(result)
    """

    DEFAULT_SYSTEM_PROMPT = """You are an expert coding assistant named "Coder" that helps users write and edit code through a chat interface. You have the ability to:

1. **Read files** - Understand existing code, project structure, and dependencies
2. **Write/Edit files** - Create new files or modify existing ones
3. **Search codebase** - Find patterns, functions, or usages across files using glob/grep
4. **Execute commands** - Run shell commands for testing, building, or exploring the system
5. **Track tasks** - Break down complex requests into manageable steps with todo list
6. **Web search** - Search the web for current information when needed

When working on a task:
- First understand the context by reading relevant files
- Plan your approach and explain what you'll do
- Make changes incrementally, explaining each step
- Test your work using shell commands when appropriate
- Ask for clarification if needed

Be thorough but efficient. Prefer minimal, clean solutions. Always verify your changes work correctly."""

    def __init__(
        self,
        provider: OpenAIProvider | None = None,
        system_prompt: str | None = None,
        max_turns: int = 20,
        working_dir: str | None = None,
        bash_manager: BashManager | None = None,
        task_manager: TaskManager | None = None,
        skill_manager: SkillManager | None = None,
        enable_learning: bool = False,
        learning_storage_path: str | None = None,
        auto_save: str | None = None,
    ) -> None:
        """Initialize the CoderAgent with all built-in tools.

        Args:
            provider: LLM provider instance (defaults to OpenAI if not provided)
            system_prompt: Custom system prompt for the coder agent
            max_turns: Maximum conversation turns before stopping
            working_dir: Working directory for file operations and shell commands
            bash_manager: Optional custom BashManager instance
            task_manager: Optional custom TaskManager instance
            skill_manager: Optional custom SkillManager instance
            enable_learning: If True, enables online learning features (tool usage tracking, feedback collection)
            learning_storage_path: Path to store learning data (JSON files). If None, learning is in-memory only.
            auto_save: Path to auto-save session JSON after each turn. Enables crash recovery.
        """
        from .tools import (
            ask_user_question,
            awk,
            bash,
            bash_background,
            bash_output,
            edit,
            enter_plan_mode,
            exit_plan_mode,
            get_learning_stats,
            git_commit,
            git_diff,
            git_log,
            git_status,
            glob,
            grep,
            http_request,
            kill_shell,
            notebook_edit,
            read,
            submit_feedback,
            todo_list,
            todo_update,
            todo_write,
            web_fetch,
            web_search,
            write,
        )

        # Use default provider if not provided
        if provider is None:
            provider = OpenAIProvider(model="gpt-4o")

        # Set system prompt
        if system_prompt is None:
            system_prompt = self.DEFAULT_SYSTEM_PROMPT

        # Add learning tools to the list if learning is enabled
        learning_tools = [submit_feedback, get_learning_stats] if enable_learning else []

        # Initialize with all built-in tools
        super().__init__(
            provider=provider,
            system_prompt=system_prompt,
            tools=[
                read, write, edit, glob, grep, notebook_edit,  # File operations
                bash, bash_background, bash_output, kill_shell,  # Shell commands
                todo_write, todo_update, todo_list,  # Task management
                enter_plan_mode, exit_plan_mode,  # Planning mode
                ask_user_question,  # User interaction
                web_search, web_fetch, http_request,  # Web tools
                git_status, git_diff, git_commit, git_log,  # Git integration
                awk,  # Text processing
            ] + learning_tools,
            max_turns=max_turns,
            bash_manager=bash_manager or get_bash_manager(),
            task_manager=task_manager or get_task_manager(),
            skill_manager=skill_manager or get_skill_manager(),
            enable_learning=enable_learning,
            learning_storage_path=learning_storage_path,
            auto_save=auto_save,
        )

        self._working_dir = working_dir or str(Path.cwd())

    @property
    def working_dir(self) -> str:
        """Get the current working directory."""
        return self._working_dir

    async def run(
        self,
        user_input: str,
        working_dir: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Run the agent with an optional working directory override.

        Args:
            user_input: The user's request or question
            working_dir: Optional working directory for this session
            **kwargs: Additional arguments passed to the provider

        Returns:
            The agent's response
        """
        if working_dir:
            self._working_dir = working_dir
        return await super().run(user_input, **kwargs)


async def create_coder(
    model: str = "gpt-4o",
    api_key: str | None = None,
    max_turns: int = 20,
    working_dir: str | None = None,
) -> CoderAgent:
    """Factory function to create a configured CoderAgent.

    Args:
        model: LLM model to use (default: gpt-4o)
        api_key: Optional API key override
        max_turns: Maximum conversation turns
        working_dir: Working directory for file operations

    Returns:
        Configured CoderAgent instance

    Example:
        >>> coder = await create_coder(model="gpt-4-turbo", working_dir="/path/to/project")
        >>> result = await coder.run("Add a new endpoint to the API")
    """
    provider = OpenAIProvider(model=model, api_key=api_key)
    return CoderAgent(
        provider=provider,
        max_turns=max_turns,
        working_dir=working_dir,
    )


if __name__ == "__main__":
    import argparse

    async def main():
        parser = argparse.ArgumentParser(description="Coder Agent - A chat-based coding assistant")
        parser.add_argument(
            "--model", "-m",
            default="gpt-4o",
            help="LLM model to use (default: gpt-4o)"
        )
        parser.add_argument(
            "--working-dir", "-w",
            default=None,
            help="Working directory for file operations"
        )
        parser.add_argument(
            "--max-turns", "-t",
            type=int,
            default=20,
            help="Maximum conversation turns (default: 20)"
        )

        args = parser.parse_args()

        coder = await create_coder(
            model=args.model,
            max_turns=args.max_turns,
            working_dir=args.working_dir,
        )

        print("Coder Agent started. Type 'quit' or 'exit' to stop.")
        print("=" * 50)

        try:
            while True:
                user_input = input("\nYou: ").strip()
                if user_input.lower() in ("quit", "exit"):
                    print("Goodbye!")
                    break
                if not user_input:
                    continue

                result = await coder.run(user_input)
                print(f"\nCoder: {result}")
        except KeyboardInterrupt:
            print("\nGoodbye!")

    asyncio.run(main())
