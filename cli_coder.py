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
import os
import sys
from pathlib import Path
from typing import Any

# Add the project root to the path for imports
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from openagent import (
    bold, dim, blue, green, yellow, red, cyan, magenta, white,
    code, diff_addition, diff_deletion, format_diff_output,
    display_code_block, display_diff_claude_style,
    display_tool_call_claude_style, display_tool_result_claude_style,
    truncate_text, format_file_list, format_grep_results_claude_style,
    display_claude_code_block
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
        """
    )

    parser.add_argument(
        "--model", "-m",
        default="gpt-4o",
        help="LLM model to use (default: gpt-4o, try claude-sonnet-4 for Anthropic)"
    )

    parser.add_argument(
        "--working-dir", "-w",
        default=None,
        type=str,
        help="Working directory for file operations and shell commands"
    )

    parser.add_argument(
        "--max-turns", "-t",
        type=int,
        default=20,
        help="Maximum conversation turns before stopping (default: 20)"
    )

    parser.add_argument(
        "--api-key", "-k",
        default=None,
        help="API key for the provider (or set environment variable). Not required when using --base-url with local servers."
    )

    parser.add_argument(
        "--base-url",
        default=None,
        help="Base URL for OpenAI-compatible API (e.g., http://localhost:1234/v1). When set, API key is optional for local servers."
    )

    return parser.parse_args()


def detect_provider(model: str, base_url: str | None = None) -> tuple[str, type]:
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
    print(dim("  /clear    - Clear conversation history"))
    print("\nUse '!' prefix for direct terminal commands:")
    print(dim("  ! ls      - List files (direct bash execution)"))
    print(dim("  ! pwd     - Print working directory"))

    try:
        turn_counter = 0  # Track turns for this session

        while True:
            # Calculate remaining turns budget (simplified display)
            prompt_text = f"\n{bold('You')} "
            if turn_counter > 0:
                prompt_text += dim(f"[{turn_counter}/{coder.max_turns}] ")
            user_input = input(prompt_text).strip()

            if not user_input:
                continue

            # Handle quick commands with "/" prefix
            if user_input.startswith("/"):
                parts = user_input.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""

                if cmd in ("/list", "/ls"):
                    from openagent.tools import bash
                    result = await coder.bash_manager.execute_command(
                        (await coder.bash_manager.start_session()).split("-")[0],
                        f"ls -la {arg}" if arg else "ls -la"
                    )
                    print(f"\n{bold('Coder: ')}{result}")

                elif cmd == "/read":
                    """Preview file contents using the read tool."""
                    if not arg:
                        print("\n" + bold("Coder: ") + "Usage: /read <filepath>")
                        continue

                    from openagent.tools import read
                    try:
                        result = read(arg)  # read() is synchronous, no await needed
                        display_code_block(f"File: {arg}", result)
                    except Exception as e:
                        print(f"\n{red('Coder: ')}Error reading file: {e}")

                elif cmd == "/todo":
                    from openagent.tools import todo_list
                    result = todo_list()
                    print(f"\n{result}")

                elif cmd == "/model":
                    """Change LLM model mid-session."""
                    if not arg:
                        # Show current model info
                        provider = coder.provider
                        print(f"\n{bold('Coder: ')}Current model: {provider.model}")
                        print(f"Provider: {type(provider).__name__}")
                        continue

                    new_model = arg.strip()
                    base_url = getattr(coder.provider, 'base_url', None)

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
                        print(f"\n{red('Coder: ')}Unknown provider type for model: {new_model}")
                        continue

                    provider_name, module_name = class_mapping[provider_class_name]

                    # Import and create new provider
                    try:
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

                        # Get API key if needed
                        api_key_env = get_api_key_env_var(provider_name)
                        api_key = os.environ.get(api_key_env) if api_key_env else None

                        new_provider_kwargs = {"model": new_model, "api_key": api_key}
                        if base_url:
                            new_provider_kwargs["base_url"] = base_url

                        coder.provider = ProviderClass(**new_provider_kwargs)
                        print(f"\n{green('Coder: ')}Switched to {provider_name} model: {new_model}")
                    except Exception as e:
                        print(f"\n{red('Coder: ')}Error switching model: {e}")

                elif cmd in ("/clear", "/reset"):
                    coder.session.clear()
                    turn_counter = 0
                    print("\n" + bold("Coder: ") + "Conversation history cleared.")

                elif cmd in ("/quit", "/exit"):
                    print("Goodbye!")
                    break

                else:
                    print(f"Unknown command: {cmd}. Use /list, /read, /todo, /model, or /clear")
                    continue

            # Handle terminal commands with "!" prefix (direct bash execution)
            if user_input.startswith("!"):
                cmd = user_input[1:].strip()
                if not cmd:
                    print("\n" + bold("Coder: ") + "Usage: ! <command>")
                    continue

                from openagent.tools import bash
                try:
                    result = await coder.bash_manager.execute_command(
                        (await coder.bash_manager.start_session()).split("-")[0],
                        cmd
                    )
                    print(f"\n{bold('Coder: ')}{result}")
                except Exception as e:
                    print(f"\n{red('Coder: ')}Error executing command: {e}")
                continue

            # Handle quit/exit commands
            if user_input.lower() in ("quit", "exit"):
                print("Goodbye!")
                break

            # Run the agent and increment turn counter
            result = await coder.run(user_input)
            turn_counter += 1
            print(f"\n{bold('Coder: ')}{result}")

    except KeyboardInterrupt:
        print("\n\nGoodbye!")


async def main():
    """Main entry point for the CLI."""
    args = setup_argparse()

    # Set project root environment variable for security confinement
    import os
    working_dir = Path(args.working_dir).resolve() if args.working_dir else Path.cwd()
    os.environ['AGENT_PROJECT_ROOT'] = str(working_dir)

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

    # Create the coder agent
    coder = CoderAgent(
        provider=provider,
        max_turns=args.max_turns,
        working_dir=args.working_dir,
    )

    # Run interactive session
    await run_interactive_session(coder)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nGoodbye!")
