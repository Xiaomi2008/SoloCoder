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
    code,
    diff_addition,
    diff_deletion,
    format_diff_output,
    display_code_block,
    display_diff_claude_style,
    truncate_text,
    format_file_list,
)


def setup_argparse() -> argparse.Namespace:
    """Set up command line argument parser with config subcommand."""
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
  python cli_coder.py config save-key tavily YOUR_API_KEY    # Save Tavily API key
  python cli_coder.py config set-provider duckduck            # Set search provider

Type 'quit' or 'exit' to stop the agent.
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Config subcommand
    config_parser = subparsers.add_parser(
        "config", help="Manage configuration and API keys"
    )
    config_subparsers = config_parser.add_subparsers(
        dest="config_action", help="Config actions"
    )

    # save-key action
    save_key_parser = config_subparsers.add_parser(
        "save-key", help="Save API key securely"
    )
    save_key_parser.add_argument(
        "provider",
        choices=["tavily"],
        help="Provider name (e.g., tavily)",
    )
    save_key_parser.add_argument(
        "api_key",
        help="API key to save",
    )

    # delete-key action
    delete_key_parser = config_subparsers.add_parser(
        "delete-key", help="Delete saved API key"
    )
    delete_key_parser.add_argument(
        "provider",
        choices=["tavily"],
        help="Provider name (e.g., tavily)",
    )

    # set-provider action
    set_provider_parser = config_subparsers.add_parser(
        "set-provider", help="Set default search provider"
    )
    set_provider_parser.add_argument(
        "provider",
        choices=["tavily", "duckduck"],
        help="Default search provider",
    )

    # show-config action
    config_subparsers.add_parser("show-config", help="Show current configuration")

    # reset action
    config_subparsers.add_parser("reset", help="Reset configuration to defaults")

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


def truncate_output(text: str, max_lines: int = 30) -> tuple[str, bool]:
    """Truncate output if it exceeds max_lines, returning truncated text and has_more flag.

    Args:
        text: The output text to potentially truncate
        max_lines: Maximum number of lines to display

    Returns:
        Tuple of (display_text, has_more) where has_more indicates if output was truncated
    """
    lines = text.split("\n")
    if len(lines) <= max_lines:
        return text, False

    # Show truncated output with indicator
    truncated = "\n".join(lines[:max_lines])
    return truncated, True


class ProgressSpinner:
    """Animated progress spinner using ANSI codes."""

    SPINNERS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    @staticmethod
    def print_animated(text: str, duration: float = 2.0, fps: int = 10) -> None:
        """Display animated spinner with text."""
        frames = ProgressSpinner.SPINNERS
        num_frames = len(frames)
        interval = duration / num_frames
        frame_idx = 0

        try:
            # Clear line and position cursor
            print(f"\r\x1b[2K\x1b[1G", end="", flush=True)

            start_time = time.time()
            while time.time() - start_time < duration:
                frame = frames[frame_idx % num_frames]
                print(f"\r\x1b[2K\x1b[1G{cyan(frame)} {dim(text)}", end="", flush=True)
                time.sleep(interval)
                frame_idx += 1

            # Clear at the end
            print(f"\x1b[2K\x1b[1G", end="", flush=True)
        except KeyboardInterrupt:
            print(f"\x1b[2K\x1b[1G", end="", flush=True)


def display_tool_progress_start(task_desc: str) -> None:
    """Start displaying tool execution progress."""
    progress_print(f"{cyan('●')} {bold(task_desc)}...")


def display_tool_progress_end() -> None:
    """Clear and finalize tool progress display."""
    progress_clear(f"{green('✓')} {bold('Complete')}")


def progress_print(text: str) -> None:
    """Print progress line."""
    print(text, flush=True)


def progress_clear(text: str = "") -> None:
    """Clear current line."""
    print(f"\x1b[2K\x1b[1G{text}", end="", flush=True)


def display_error_box(message: str) -> None:
    """Display error in a styled red box."""
    border = "━" * 60
    warning = f"⚠️ {message}"
    print(f"\n{red(border)}")
    print(f"{red('❌')} {red(warning)}")
    print(f"{red(border)}")
    print()


def colorize_code_syntax(code: str, language: str = "python") -> str:
    """Add basic syntax highlighting colors to code."""
    if not code:
        return code

    lines = code.split("\n")
    highlighted_lines = []

    patterns = [
        # Strings
        (r'["\'].*?["\']', lambda m: cyan(m.group())),
        # Comments
        (r"#.*$", lambda m: dim(m.group())),
        # Keywords
        (
            r"\b(def|class|import|from|return|if|else|elif|for|while|try|except|finally|with|async|await|yield|lambda|pass|break|continue|and|or|not|in|is|True|False)\b",
            lambda m: bold(red(m.group())),
        ),
        # Functions
        (r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", lambda m: blue(m.group())),
        # Decorators
        (r"@\w+", lambda m: yellow(m.group())),
        # Numbers
        (r"\b\d+\.\d+\b|\b\d+\b", lambda m: magenta(m.group())),
    ]

    for line in lines:
        highlighted_line = line
        for pattern, color_func in patterns:
            highlighted_line = re.sub(pattern, color_func, highlighted_line)
        highlighted_lines.append(highlighted_line)

    return "\n".join(highlighted_lines)


def display_file_tree_with_icons(result: str) -> None:
    """Display file listing with icons based on file types."""
    lines = result.strip().split("\n")
    icons = {
        ".py": "🐍",
        ".js": "🟨",
        ".ts": "🔵",
        ".tsx": "🔵",
        ".json": "📋",
        ".md": "📝",
        ".txt": "📄",
        ".yaml": "⚙️",
        ".yml": "⚙️",
        ".toml": "⚙️",
        ".html": "🌐",
        ".css": "🎨",
        ".scss": "🎨",
        ".sh": "🚀",
        ".env": "🔐",
        ".ipynb": "📘",
        ".c": "🔧",
        ".cpp": "🔧",
        ".h": "🔧",
        ".go": "🐹",
        ".rs": "🦀",
        ".rb": "💎",
    }

    output_lines = []
    for line in lines:
        if not line:
            continue
        if line.startswith("total ") and line.count(":") == 0:
            output_lines.append(dim(line))
            continue

        parts = line.rsplit(" ", 1)
        if len(parts) != 2:
            output_lines.append("  " + line)
            continue

        _, filename = parts
        filename = filename.strip()
        ext = os.path.splitext(filename)[1].lower()
        icon = "📁" if filename.endswith("/") else icons.get(ext, "📄")
        output_lines.append(f"  {cyan(icon)} {line}")

    display_truncated_output("\n".join(output_lines))


def display_truncated_output(text: str) -> None:
    """Display output with automatic truncation for long results."""
    display_text, has_more = truncate_output(text)
    print(display_text)
    if has_more:
        print(yellow("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"))
        print(yellow("…"))
        print(yellow("[Showing 30 of many lines. Use !<command> for direct execution]"))
        print(yellow("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"))


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
    icon = "●"
    print(f"\n{cyan(icon)} {bold(name)}")
    if arguments:
        for key, value in arguments.items():
            print(f"  {dim(key)}: {value}")


def display_tool_result(is_error: bool, content: str) -> None:
    """Display a tool result with appropriate styling (Claude Code style)."""
    icon = "⎿"
    color = red if is_error else green
    truncated = truncate_text(content, 500)
    print(
        f"\n{color(icon)} {dim(content[:100])}..."
        if len(content) > 100
        else f"\n{color(icon)} {truncated}"
    )


def display_tool_result_full(is_error: bool, content: str) -> None:
    """Display a full tool result with appropriate styling."""
    icon = "❌" if is_error else "✅"
    color = red if is_error else green
    print(f"\n{color(icon)} {bold('Result:')}\n")
    print(content)


def display_context_status_bar(coder, turn_counter: int = 0) -> None:
    """Display a status bar showing context token usage.

    Args:
        coder: The CoderAgent instance
        turn_counter: Current turn number for session tracking
    """
    try:
        current_tokens = coder.session.token_count
        max_tokens = getattr(coder, "max_context_tokens", 128000)
        threshold = getattr(coder, "compact_threshold", 0.8)
        threshold_tokens = int(max_tokens * threshold)

        # Calculate percentage and color
        percentage = (current_tokens / max_tokens) * 100 if max_tokens > 0 else 0

        # Determine color based on usage level
        if percentage >= 90:
            bar_color = red
        elif percentage >= 80:
            bar_color = yellow
        elif percentage >= threshold * 100:
            bar_color = cyan
        else:
            bar_color = green

        # Create progress bar (50 characters wide)
        bar_width = 50
        filled_chars = (
            int(bar_width * current_tokens / max_tokens) if max_tokens > 0 else 0
        )
        bar = "█" * filled_chars + "░" * (bar_width - filled_chars)

        # Format the status line
        usage_text = f"{current_tokens:,} / {max_tokens:,}"
        threshold_text = (
            f"(threshold: {threshold_tokens:,})"
            if percentage >= threshold * 100
            else ""
        )

        # Build the status bar display
        status_line = f"\n{dim('─' * 80)}\n"
        status_line += f"{bar_color(f'{usage_text}')} {dim(threshold_text)}\n"
        status_line += f"{bar_color(bar)}\n"

        # Add compacted indicator if needed
        if percentage >= threshold * 100:
            status_line += f"\n{yellow('⚠️ Context approaching limit. Consider using /compact to save tokens.')}\n"

        print(status_line)

    except Exception as e:
        # Don't crash the CLI if token counting fails
        pass


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
    print(dim("  /image    - Upload an image for vision analysis"))
    print(dim("  /todo     - Show task list"))
    print(dim("  /model    - Change LLM model mid-session"))
    print(dim("  /context  - Show current context token usage"))
    print(dim("  /clear    - Clear conversation history"))
    print(dim("  /compact  - Manually compact context to save tokens"))
    print("\nUse '!' prefix for direct terminal commands:")
    print(dim("  ! ls      - List files (direct bash execution)"))
    print(dim("  ! pwd     - Print working directory"))

    # Display initial context status bar
    display_context_status_bar(coder)

    try:
        turn_counter = 0
        command_history = []

        import readline

        if sys.platform != "win32":
            readline.set_history_length(500)

        while True:
            # Display minimal prompt (Claude Code style)
            print("> ", end="", flush=True)
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
                    display_file_tree(result)
                    continue

                elif cmd == "/read":
                    """Preview file contents using the read tool."""
                    if not arg:
                        print(f"\n{green('⎿')} Usage: /read <filepath>")
                        continue

                    from openagent.tools import read

                    try:
                        result = read(arg)  # read() is synchronous, no await needed
                        display_code_block(f"File: {arg}", result)
                    except Exception as e:
                        print(f"\n{red('⎿')} Error reading file: {e}")
                    continue

                elif cmd == "/todo":
                    from openagent.tools import todo_list

                    result = todo_list()
                    print(f"\n{green('⎿')} {result}")
                    continue

                elif cmd == "/image":
                    """Upload an image for vision analysis (Qwen3.5 multimodal)."""
                    import base64

                    from openagent.tools.computer_use import screenshot

                    if not arg:
                        # Take a screenshot and analyze
                        print(f"\n{green('⎿')} Taking screenshot...")
                        img_base64 = screenshot(return_base64=True)

                        if img_base64:
                            # Run multimodal analysis
                            try:
                                result = await coder.run_multimodal(
                                    image_data=img_base64,
                                    text="Analyze this screenshot and describe what you see.",
                                )
                                print(f"\n{green('⎿')} {result}")
                            except Exception as e:
                                print(f"\n{red('⎿')} Error analyzing image: {e}")
                        continue

                    # Load image from file
                    try:
                        img_bytes = Path(arg).read_bytes()
                        img_base64 = base64.b64encode(img_bytes).decode("utf-8")

                        # Run multimodal analysis
                        try:
                            result = await coder.run_multimodal(
                                image_data=img_base64,
                                text=arg or "Analyze this image.",
                            )
                            print(f"\n{green('⎿')} {result}")
                        except Exception as e:
                            print(f"\n{red('⎿')} Error analyzing image: {e}")
                    except Exception as e:
                        print(f"\n{red('⎿')} Error loading image: {e}")
                    continue

                elif cmd == "/model":
                    """Change LLM model mid-session."""
                    if not arg:
                        provider = coder.provider
                        print(f"\n{green('⎿')} Current model: {provider.model}")
                        print(f"{green('⎿')} Provider: {type(provider).__name__}")
                        continue

                    new_model = arg.strip()
                    base_url = getattr(coder.provider, "base_url", None)

                    _, provider_class_name = detect_provider(new_model, base_url)

                    class_mapping = {
                        "OpenAIProvider": ("OpenAI", "openai"),
                        "AnthropicProvider": ("Anthropic", "anthropic"),
                        "GoogleProvider": ("Google", "google"),
                        "OllamaProvider": ("Ollama", "ollama"),
                    }

                    if provider_class_name not in class_mapping:
                        print(
                            f"\n{red('⎿')} Unknown provider type for model: {new_model}"
                        )
                        continue

                    provider_name, module_name = class_mapping[provider_class_name]

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

                        api_key_env = get_api_key_env_var(provider_name)
                        api_key = os.environ.get(api_key_env) if api_key_env else None

                        new_provider_kwargs = {"model": new_model, "api_key": api_key}
                        if base_url:
                            new_provider_kwargs["base_url"] = base_url

                        coder.provider = ProviderClass(**new_provider_kwargs)
                        print(
                            f"\n{green('⎿')} Switched to {provider_name} model: {new_model}"
                        )
                    except Exception as e:
                        print(f"\n{red('⎿')} Error switching model: {e}")
                    continue

                elif cmd == "/context":
                    """Show current context token usage."""
                    try:
                        display_context_status_bar(coder)
                    except Exception as e:
                        print(f"\n{red('⎿')} Error displaying context status: {e}")
                    continue

                elif cmd == "/compact":
                    """Manually compact conversation context."""
                    try:
                        max_tokens = getattr(coder, "max_context_tokens", 128000)
                        threshold = getattr(coder, "compact_threshold", 0.8)

                        summary = await coder.session.compact_context(
                            provider=coder.provider,
                            keep_recent=5,
                            summary_type="detailed",
                        )
                        print(f"\n{green('⎿')} Context compacted!")
                        print(dim("Summary preview:"))
                        print(
                            dim(
                                summary[:300] + "..." if len(summary) > 300 else summary
                            )
                        )
                    except Exception as e:
                        print(f"\n{red('⎿')} Error during compaction: {e}")
                    continue

                elif cmd in ("/clear", "/reset"):
                    coder.session.clear()
                    turn_counter = 0
                    print(f"\n{green('⎿')} Conversation history cleared.")
                    continue

                elif cmd in ("/quit", "/exit"):
                    print("Goodbye!")
                    break

                else:
                    print(
                        f"{red('⎿')} Unknown command: {cmd}. Use /list, /read, /todo, /model, or /clear"
                    )
                    continue

            if typed_input.startswith("!"):
                cmd = typed_input[1:].strip()
                if not cmd:
                    print(f"\n{green('⎿')} Usage: ! <command>")
                    continue

                from openagent.tools import bash

                try:
                    result = await coder.bash_manager.execute_command(
                        (await coder.bash_manager.start_session()).split("-")[0], cmd
                    )
                    display_truncated_output(result)
                except Exception as e:
                    display_error_box(str(e))
                continue

            # Handle quit/exit commands
            if typed_input.lower() in ("quit", "exit"):
                print("Goodbye!")
                break

            # Run the agent and increment turn counter
            result = await coder.run(typed_input)
            turn_counter += 1
            display_truncated_output(result)

            # Update context status bar after each interaction
            display_context_status_bar(coder, turn_counter)

    except KeyboardInterrupt:
        print("\n\nGoodbye!")


async def main():
    """Main entry point for the CLI."""
    args = setup_argparse()

    # Handle config commands
    if args.command == "config":
        await handle_config_command(args)
        return

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


async def handle_config_command(args: argparse.Namespace) -> None:
    """Handle config subcommand."""
    from openagent.core.config import (
        ConfigManager,
        SecretManager,
        get_config as get_config_func,
    )

    if args.config_action == "save-key":
        provider = args.provider
        api_key = args.api_key

        # Determine env var name
        env_var = f"{provider.upper()}_API_KEY"

        # Save to secrets
        SecretManager.save_api_key(provider, api_key)

        print(
            f"\n{green('✓')} Saved {provider} API key securely to ./secrets/secrets.json"
        )
        print(f"  Set via environment variable: {env_var}")
        print(f"\nTip: You can also set it directly:")
        print(f"  export {env_var}='{api_key[:3]}...{api_key[-3:]}'")

        # Update default provider if not set
        try:
            config = get_config_func()
            if config.search.provider == "tavily":
                print(f"\nNote: Default search provider is already set to {provider}")
            else:
                print(f"\nNote: You can set {provider} as default with:")
                print(f"  python cli_coder.py config set-provider {provider}")
        except Exception:
            pass

    elif args.config_action == "delete-key":
        provider = args.provider

        if SecretManager.delete_api_key(provider):
            print(f"\n{green('✓')} Deleted {provider} API key from secrets file")
        else:
            print(f"\n{red('✗')} No {provider} API key found to delete")

    elif args.config_action == "set-provider":
        provider = args.provider
        ConfigManager.update_config(provider=provider)

        print(f"\n{green('✓')} Set default search provider to: {provider}")
        print(f"  Tavily uses AI-optimized results, DuckDuckGo works without API key")

    elif args.config_action == "show-config":
        try:
            config = get_config_func()
            print(f"\n{bold('OpenAgent Configuration')}\n")
            print(f"Search Provider:     {config.search.provider}")
            print(f"  - Tavily Max Results:     {config.search.tavily_max_results}")
            print(f"  - DuckDuck Max Results:   {config.search.duckduck_max_results}")
            print(f"  - Fallback to DuckDuck:   {config.search.fallback_to_duckduck}")

            # Check API key status
            print(f"\n{bold('API Key Status')}\n")
            tavily_key = SecretManager.get_api_key("tavily")
            if tavily_key:
                masked = f"{tavily_key[:3]}...{tavily_key[-3:]}"
                print(f"{green('✓')} Tavily API key: {masked}")
            else:
                print(f"{red('✗')} Tavily API key: Not set")
                print(
                    f"    Set with: python cli_coder.py config save-key tavily YOUR_KEY"
                )

            duckduck_key = SecretManager.get_api_key("duckduck")
            if duckduck_key:
                masked = f"{duckduck_key[:3]}...{duckduck_key[-3:]}"
                print(f"{green('✓')} DuckDuck API key: {masked}")
            else:
                print(f"{red('✗')} DuckDuck API key: Not set (not required)")

            print(f"\n{bold('Convenience')}\n")
            print(
                f"  Save key:     python cli_coder.py config save-key tavily YOUR_KEY"
            )
            print(f"  Delete key:   python cli_coder.py config delete-key tavily")
            print(
                f"  Set provider: python cli_coder.py config set-provider [tavily|duckduck]"
            )

        except Exception as e:
            print(f"\n{red('✗')} Error reading configuration: {e}")

    elif args.config_action == "reset":
        from openagent.core.config import reset_config

        reset_config()
        print(f"\n{green('✓')} Configuration reset to defaults")

    else:
        print(f"\n{red('✗')} Unknown config action: {args.config_action}")
        print(
            "Available actions: save-key, delete-key, set-provider, show-config, reset"
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nGoodbye!")
