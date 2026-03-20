#!/usr/bin/env python3
"""CLI interface for the Coder Agent.

A chat-based coding assistant that allows you to write and edit code through natural language,
inspired by Claude Code's functionality.

Usage:
    python cli_coder.py [--model MODEL] [--working-dir DIR] [--max-turns N]

Examples:
    python cli_coder.py                          # Start with defaults (gpt-4o)
    python cli_coder.py --model gpt-4-turbo     # Use a different model
    python cli_coder.py -w /path/to/project     # Set working directory
    python cli_coder.py --base-url http://localhost:1234/v1  # OpenAI-compatible API
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Add the project root to the path for imports
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from openagent import (
    bold,
    dim,
    blue,
    green,
    yellow,
    red,
    cyan,
    magenta,
    white,
    user_input,
    code,
    diff_addition,
    diff_deletion,
    format_diff_output,
    display_code_block,
    display_diff_claude_style,
    display_tool_call_claude_style,
    display_tool_result_claude_style,
    truncate_text,
    format_file_list,
    format_grep_results_claude_style,
    display_claude_code_block,
)


def setup_argparse() -> argparse.Namespace:
    """Set up command line argument parser."""
    parser = argparse.ArgumentParser(
        description="Coder Agent - A chat-based coding assistant powered by OpenAgent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli_coder.py                          # Start with defaults (gpt-4o)
  python cli_coder.py --model gpt-4-turbo     # Use a different model
  python cli_coder.py -m claude-sonnet-4      # Use Anthropic Claude
  python cli_coder.py -w /path/to/project     # Set working directory
  python cli_coder.py --base-url http://localhost:1234/v1  # OpenAI-compatible API

Features:
  - Read, write, and edit files
  - Search codebase with grep
  - Execute shell commands
  - Track tasks for complex operations
  - Interactive chat interface

Type 'quit' or 'exit' to stop the agent.
        """,
    )

    parser.add_argument(
        "--model",
        "-m",
        default="gpt-4o",
        help="LLM model to use (default: gpt-4o, try claude-sonnet-4 for Anthropic)",
    )

    parser.add_argument(
        "--working-dir",
        "-w",
        default=None,
        type=str,
        help="Working directory for file operations and shell commands",
    )

    parser.add_argument(
        "--max-turns",
        "-t",
        type=int,
        default=100,
        help="Maximum conversation turns before stopping (default: 100)",
    )

    parser.add_argument(
        "--api-key",
        "-k",
        default=None,
        help="API key for the provider (or set environment variable). Not required when using --base-url with local servers.",
    )

    parser.add_argument(
        "--base-url",
        default=None,
        help="Base URL for OpenAI-compatible API (e.g., http://localhost:1234/v1). When set, API key is optional for local servers.",
    )

    # Context compaction settings
    parser.add_argument(
        "--max-context-tokens",
        type=int,
        default=128000,
        help="Maximum context window size in tokens (default: 128000)",
    )

    parser.add_argument(
        "--compact-threshold",
        type=float,
        default=0.8,
        help="Trigger compaction at this percentage of max_context_tokens (default: 0.8 = 80%%)",
    )

    parser.add_argument(
        "--disable-compaction",
        action="store_true",
        help="Disable automatic context compaction",
    )

    parser.add_argument(
        "--log-level",
        default="warning",
        choices=["debug", "info", "warning", "error", "critical"],
        help="OpenAgent log level (default: warning)",
    )

    parser.add_argument(
        "--debug-llm",
        action="store_true",
        help="Shortcut for --log-level debug to inspect raw LM Studio/OpenAI-compatible responses",
    )

    return parser.parse_args()


def detect_provider(model: str, base_url: str | None = None) -> tuple[str, str]:
    """Detect which provider to use based on model name and base URL.

    Returns:
        Tuple of (provider_name, provider_class)
    """
    model_lower = model.lower()

    # LMStudio - uses OpenAI-compatible API with custom base_url
    if "lmstudio" in (base_url or "").lower():
        return ("LMStudio", "OpenAIProvider")

    # Anthropic models start with "claude" or contain "anthropic"
    if model_lower.startswith("claude") or "anthropic" in model_lower:
        return ("Anthropic", "AnthropicProvider")

    # Google models often contain "gemini"
    if "gemini" in model_lower:
        return ("Google", "GoogleProvider")

    # Ollama models often start with "ollama" or are local
    if model_lower.startswith("ollama"):
        return ("Ollama", "OllamaProvider")

    # Default to OpenAI (gpt-*, etc.)
    return ("OpenAI", "OpenAIProvider")


def get_api_key_env_var(provider_name: str) -> str:
    """Get the environment variable name for a provider's API key."""
    env_vars = {
        "OpenAI": "OPENAI_API_KEY",
        "Anthropic": "ANTHROPIC_API_KEY",
        "Google": "GOOGLE_API_KEY",
        "Ollama": "",  # Ollama doesn't require an API key
    }
    return env_vars.get(provider_name, "")


def display_tool_call(name: str, arguments: dict) -> None:
    """Display a tool call in Claude Code style."""
    display_tool_call_claude_style(name, arguments)


def display_tool_result(is_error: bool, content: str) -> None:
    """Display a tool result with appropriate styling (Claude Code style)."""
    display_tool_result_claude_style(is_error, content)


def display_tool_result_full(is_error: bool, content: str) -> None:
    """Display a full tool result with appropriate styling."""
    icon = "❌" if is_error else "✅"
    color = red if is_error else green
    print(f"\n{color(icon)} {bold('Result:')}\n")
    print(content)


async def run_interactive_session(coder) -> None:
    """Run the interactive chat session with Claude Code style display."""
    print(f"\n{bold('Coder Agent')} - Chat-based coding assistant")
    print(dim("-" * 40))
    print(f"{dim('Working directory:')} {coder.working_dir}")
    print(f"{dim('Max turns per request:')} {coder.max_turns}")
    print("\nType 'quit' or 'exit' to stop.")
    print("Use '/' prefix for quick commands:")
    print(dim("  /list     - List files in current directory"))
    print(dim("  /read     - Preview file contents before editing"))
    print(dim("  /todo     - Show task list"))
    print(dim("  /model    - Change LLM model mid-session"))
    print(dim("  /context  - Show current context token usage"))
    print(dim("  /clear    - Clear conversation history"))
    print(dim("  /compact  - Manually compact context to save tokens"))
    print("\nUse '!' prefix for direct terminal commands:")
    print(dim("  ! ls      - List files (direct bash execution)"))
    print(dim("  ! pwd     - Print working directory"))

    try:
        turn_counter = 0  # Track turns for this session

        while True:
            # Calculate remaining turns budget (simplified display)
            # Use distinctive color combination for user prompts to distinguish from Coder responses
            # Note: Input text itself uses terminal default, so we make the prompt very distinctive
            # Using a visual separator line to clearly mark user input area
            if turn_counter > 0:
                print(f"\n{user_input(cyan('You'))} {dim(f'[{turn_counter}/{coder.max_turns}] ')}")
            else:
                print(f"\n{user_input(cyan('You'))}")

            # Print a separator line to visually mark the user input area
            # This makes it clear where user input begins, even though typed text uses terminal default color
            print(dim("─" * 40))
            typed_input = input().strip()

            if not typed_input:
                continue

            # Handle quick commands with "/" prefix
            if typed_input.startswith("/"):
                parts = typed_input.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""

                if cmd in ("/list", "/ls"):
                    from openagent.tools import bash

                    result = await coder.bash_manager.execute_command(
                        (await coder.bash_manager.start_session()).split("-")[0],
                        f"ls -la {arg}" if arg else "ls -la",
                    )
                    print(f"\n{green('Coder: ')}{result}")
                    continue

                elif cmd == "/read":
                    """Preview file contents using the read tool."""
                    if not arg:
                        print("\n" + green("Coder: ") + "Usage: /read <filepath>")
                        continue

                    from openagent.tools import read

                    try:
                        result = read(arg)  # read() is synchronous, no await needed
                        display_code_block(f"File: {arg}", result)
                    except Exception as e:
                        print(f"\n{red('Coder: ')}Error reading file: {e}")
                    continue

                elif cmd == "/todo":
                    from openagent.tools import todo_list

                    result = todo_list()
                    print(f"\n{result}")
                    continue

                elif cmd == "/model":
                    """Change LLM model mid-session."""
                    if not arg:
                        # Show current model info
                        provider = coder.provider
                        print(f"\n{green('Coder: ')}Current model: {provider.model}")
                        print(f"Provider: {type(provider).__name__}")
                        continue

                    new_model = arg.strip()
                    base_url = getattr(coder.provider, "base_url", None)

                    # Detect provider for new model
                    _, provider_class_name = detect_provider(new_model, base_url)

                    # Map to actual class names
                    class_mapping = {
                        "OpenAIProvider": ("OpenAI", "openai"),
                        "AnthropicProvider": ("Anthropic", "anthropic"),
                        "GoogleProvider": ("Google", "google"),
                        "OllamaProvider": ("Ollama", "ollama"),
                    }

                    if provider_class_name not in class_mapping:
                        print(
                            f"\n{red('Coder: ')}Unknown provider type for model: {new_model}"
                        )
                        continue

                    provider_name, module_name = class_mapping[provider_class_name]

                    # Import and create new provider
                    try:
                        ProviderClass: type[Any] | None = None

                        if module_name == "openai":
                            from openagent.provider.openai import OpenAIProvider

                            ProviderClass = OpenAIProvider
                        elif module_name == "anthropic":
                            from openagent.provider.anthropic import AnthropicProvider

                            ProviderClass = AnthropicProvider
                        elif module_name == "google":
                            from openagent.provider.google import GoogleProvider

                            ProviderClass = GoogleProvider
                        elif module_name == "ollama":
                            from openagent.provider.ollama import OllamaProvider

                            ProviderClass = OllamaProvider

                        if ProviderClass is None:
                            raise RuntimeError(
                                f"Could not load provider class for module: {module_name}"
                            )

                        # Get API key if needed
                        api_key_env = get_api_key_env_var(provider_name)
                        api_key = os.environ.get(api_key_env) if api_key_env else None

                        new_provider_kwargs = {"model": new_model, "api_key": api_key}
                        if base_url:
                            new_provider_kwargs["base_url"] = base_url

                        coder.provider = ProviderClass(**new_provider_kwargs)
                        print(
                            f"\n{green('Coder: ')}Switched to {provider_name} model: {new_model}"
                        )
                    except Exception as e:
                        print(f"\n{red('Coder: ')}Error switching model: {e}")
                    continue

                elif cmd == "/context":
                    """Show current context token usage."""
                    try:
                        current_tokens = coder.session.token_count
                        max_tokens = getattr(coder, "max_context_tokens", 128000)
                        threshold = getattr(coder, "compact_threshold", 0.8)
                        threshold_tokens = int(max_tokens * threshold)

                        print(f"\n{green('Coder: ')}Current context usage")
                        print(f"Tokens: {current_tokens:,} / {max_tokens:,}")
                        print(
                            f"Auto-compact at: {threshold_tokens:,} ({threshold:.0%})"
                        )
                    except Exception as e:
                        print(f"\n{red('Coder: ')}Error calculating context usage: {e}")
                    continue

                elif cmd == "/compact":
                    """Manually compact conversation context."""
                    try:
                        # Get compaction settings from coder if available
                        max_tokens = getattr(coder, "max_context_tokens", 128000)
                        threshold = getattr(coder, "compact_threshold", 0.8)

                        summary = await coder.session.compact_context(
                            provider=coder.provider,
                            keep_recent=5,
                            summary_type="detailed",
                        )
                        print(f"\n{green('Coder: ')}Context compacted!")
                        print(dim("Summary preview:"))
                        print(
                            dim(
                                summary[:300] + "..." if len(summary) > 300 else summary
                            )
                        )
                    except Exception as e:
                        print(f"\n{red('Coder: ')}Error during compaction: {e}")
                    continue

                elif cmd in ("/clear", "/reset"):
                    coder.session.clear()
                    turn_counter = 0
                    print("\n" + green("Coder: ") + "Conversation history cleared.")
                    continue

                elif cmd in ("/quit", "/exit"):
                    print("Goodbye!")
                    break

                else:
                    print(
                        f"Unknown command: {cmd}. Use /list, /read, /todo, /model, or /clear"
                    )
                    continue

            # Handle terminal commands with "!" prefix (direct bash execution)
            if typed_input.startswith("!"):
                cmd = typed_input[1:].strip()
                if not cmd:
                    print("\n" + green("Coder: ") + "Usage: ! <command>")
                    continue

                from openagent.tools import bash

                try:
                    result = await coder.bash_manager.execute_command(
                        (await coder.bash_manager.start_session()).split("-")[0], cmd
                    )
                    print(f"\n{green('Coder: ')}{result}")
                except Exception as e:
                    print(f"\n{red('Coder: ')}Error executing command: {e}")
                continue

            # Handle quit/exit commands
            if typed_input.lower() in ("quit", "exit"):
                print("Goodbye!")
                break

            # Run the agent and increment turn counter
            result = await coder.run(typed_input)
            turn_counter += 1
            print(f"\n{green('Coder: ')}{result}")

    except KeyboardInterrupt:
        print("\n\nGoodbye!")


async def main():
    """Main entry point for the CLI."""
    args = setup_argparse()

    from openagent import configure_logging

    log_level_name = "debug" if args.debug_llm else args.log_level
    configure_logging(level=getattr(logging, log_level_name.upper()))

    # Set project root environment variable for security confinement
    import os

    working_dir = Path(args.working_dir).resolve() if args.working_dir else Path.cwd()
    os.environ["AGENT_PROJECT_ROOT"] = str(working_dir)

    from openagent.coder import CoderAgent

    # Detect provider based on model name and base URL
    provider_name, provider_class_name = detect_provider(args.model, args.base_url)

    # Import the appropriate provider class dynamically
    if provider_class_name == "OpenAIProvider":
        from openagent.provider.openai import OpenAIProvider

        ProviderClass = OpenAIProvider
    elif provider_class_name == "AnthropicProvider":
        from openagent.provider.anthropic import AnthropicProvider

        ProviderClass = AnthropicProvider
    elif provider_class_name == "GoogleProvider":
        from openagent.provider.google import GoogleProvider

        ProviderClass = GoogleProvider
    elif provider_class_name == "OllamaProvider":
        from openagent.provider.ollama import OllamaProvider

        ProviderClass = OllamaProvider
    else:
        # Default to OpenAI
        from openagent.provider.openai import OpenAIProvider

        ProviderClass = OpenAIProvider

    # Get API key - command line takes precedence, then env var
    api_key = args.api_key
    if not api_key:
        env_var = get_api_key_env_var(provider_name)
        if env_var:
            import os

            api_key = os.environ.get(env_var)
            if api_key:
                print(f"Using {provider_name} provider (model: {args.model})")

    # Build provider kwargs
    provider_kwargs: dict[str, Any] = {
        "model": args.model,
        "api_key": api_key or None,  # Will use env var if not provided
    }

    # Add base_url if specified (for OpenAI-compatible APIs)
    if args.base_url:
        provider_kwargs["base_url"] = args.base_url

    # Create provider with optional API key override
    provider = ProviderClass(**provider_kwargs)

    # Create the coder agent with compaction settings
    coder = CoderAgent(
        provider=provider,
        max_turns=args.max_turns,
        working_dir=args.working_dir,
        max_context_tokens=args.max_context_tokens,
        compact_threshold=args.compact_threshold,
        disable_compaction=args.disable_compaction,
    )

    # Run interactive session
    if args.debug_llm:
        print(f"{yellow('Debug logging enabled:')} OpenAgent + provider logs at DEBUG")

    await run_interactive_session(coder)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nGoodbye!")
