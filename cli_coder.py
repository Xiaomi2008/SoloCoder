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
  python cli_coder.py auth status                            # Credentials in the Keychain
  python cli_coder.py auth login claude                       # Claude Pro/Max sign-in (unofficial)
  python cli_coder.py models list --discover                  # Models from running local servers
  python cli_coder.py models use lmstudio/qwen3-coder-32b     # Pick the model

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
        choices=["tavily", "ebay_id", "ebay_secret"],
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
        choices=["tavily", "ebay_id", "ebay_secret"],
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

    # Credential and model management (auth / models subcommands)
    from openagent.provider.cli import add_subcommands as add_model_subcommands

    add_model_subcommands(subparsers)

    # The agent service (serve) and its headless client (tui)
    from openagent.service.cli import add_subcommands as add_service_subcommands

    add_service_subcommands(subparsers)

    parser.add_argument(
        "--model",
        "-m",
        default="gpt-4o",
        help="LLM model to use (default: gpt-4o, try claude-sonnet-4 for Anthropic)",
    )

    parser.add_argument(
        "--image",
        "-i",
        default=None,
        type=str,
        help="Path to an image to analyze with vision (Qwen3.5 multimodal)",
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
        default=None,
        help="Maximum context window size in tokens (default: the model's, else 128000)",
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
        "-p",
        "--prompt",
        default=None,
        help="Run this prompt and exit, instead of a session",
    )

    parser.add_argument(
        "--permission-mode",
        choices=("ask", "allow_readonly", "allow_all"),
        default="ask",
        help="ask (default) prompts per risky tool; allow_readonly refuses them; allow_all runs them",
    )

    parser.add_argument(
        "--session",
        default=None,
        help="Resume a stored session id",
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


def build_provider_for(model: str, base_url: str | None) -> Any:
    """A provider for a bare model name, as ``/model`` asks for one."""
    provider_name, class_name = detect_provider(model, base_url)
    if class_name == "AnthropicProvider":
        from openagent.provider.anthropic import AnthropicProvider as ProviderClass
    elif class_name == "GoogleProvider":
        from openagent.provider.google import GoogleProvider as ProviderClass
    elif class_name == "OllamaProvider":
        from openagent.provider.ollama import OllamaProvider as ProviderClass
    else:
        from openagent.provider.openai import OpenAIProvider as ProviderClass
    env_var = get_api_key_env_var(provider_name)
    kwargs: dict[str, Any] = {"model": model, "api_key": os.environ.get(env_var) if env_var else None}
    if base_url:
        kwargs["base_url"] = base_url
    return ProviderClass(**kwargs)


async def run_interactive_session(
    coder,
    initial_image: str | None = None,
    *,
    model_name: str | None = None,
    prompt: str | None = None,
    permission_mode: str = "ask",
    session_id: str | None = None,
    compact_threshold: float = 0.8,
    disable_compaction: bool = False,
) -> int:
    """Run the chat session, or one prompt when ``prompt`` is given."""
    from openagent.cli import style
    from openagent.cli.chat import Chat
    from openagent.cli.input import Input
    from openagent.core.config import ConfigManager

    live = style.live_terminal() and prompt is None
    history = None
    if style.interactive_input() and prompt is None:
        history = ConfigManager.get_config_path().parent / "cli_history_py"

    async def switch_model(name: str) -> str:
        coder.provider = build_provider_for(name, getattr(coder.provider, "base_url", None))
        return name

    chat = Chat(
        coder,
        live=live,
        input_=Input(history),
        model_name=model_name or getattr(coder.provider, "model", "model"),
        permission_mode=permission_mode,
        session_id=session_id,
        switch_model=switch_model,
        compact_threshold=compact_threshold,
        disable_compaction=disable_compaction,
    )
    if prompt is not None:
        return await chat.once(prompt, initial_image)
    return await chat.loop(initial_image)


async def main():
    """Main entry point for the CLI."""
    args = setup_argparse()

    # Handle config commands
    if args.command == "config":
        await handle_config_command(args)
        return

    # Handle auth / models commands (no agent loop involved)
    if args.command in ("auth", "models"):
        from openagent.provider.cli import handle_auth_async, handle_models_async

        handler = handle_auth_async if args.command == "auth" else handle_models_async
        raise SystemExit(await handler(args))

    # Handle the service and its client: they own the process from here.
    if args.command in ("serve", "tui"):
        service_handler = getattr(args, "handler", None)
        if service_handler is None:
            raise SystemExit(2)
        raise SystemExit(await service_handler(args))

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
                pass

    # Build provider kwargs
    provider_kwargs: dict[str, Any] = {
        "model": args.model,
        "api_key": api_key or None,  # Will use env var if not provided
    }

    # Add base_url if specified (for OpenAI-compatible APIs)
    if args.base_url:
        provider_kwargs["base_url"] = args.base_url

    # A catalog key (`openai_oauth/gpt-6.1-sol`) names an endpoint and its auth;
    # the registry resolves stored credentials (subscription sign-ins included).
    from openagent.provider.registry import ProviderRegistry

    registry = ProviderRegistry()
    endpoint, _, registry_model = args.model.partition("/")
    max_context_tokens = args.max_context_tokens
    if registry_model and endpoint in registry.endpoints and not args.api_key:
        from openagent.provider.catalog import ModelCatalog

        # The catalog knows the model's real window (gpt-6.1-sol: 500k); the
        # endpoint default is a guess. Discovery is skipped when it is known.
        catalog = ModelCatalog(registry)
        ref = catalog.get(args.model)
        if ref is None:
            await catalog.discover(endpoint)
            ref = catalog.get(args.model)
        if ref is None or args.base_url:
            ref = registry.ref(endpoint, registry_model, base_url=args.base_url)
        provider = await registry.build_async(ref)
        max_context_tokens = max_context_tokens or ref.caps.context_window
    else:
        # Create provider with optional API key override
        provider = ProviderClass(**provider_kwargs)

    # Create the coder agent with compaction settings
    coder = CoderAgent(
        provider=provider,
        max_turns=args.max_turns,
        working_dir=args.working_dir,
        max_context_tokens=max_context_tokens or 128000,
        compact_threshold=args.compact_threshold,
        disable_compaction=args.disable_compaction,
    )

    # Run interactive session
    if args.debug_llm:
        print(yellow("Debug logging enabled: OpenAgent + provider logs at DEBUG"))

    # Handle initial image input if provided
    initial_image = None
    if args.image:
        try:
            img_bytes = Path(args.image).read_bytes()
            import base64
            initial_image = base64.b64encode(img_bytes).decode("utf-8")
        except Exception as e:
            print(f"\n{red('⎿')} Error loading image: {e}")

    # The conversation to continue, before the header shows anything.
    from openagent.cli import style
    from openagent.service import sessions

    session_id = None
    if args.session:
        session_id = sessions.check_session_id(args.session)
        stored = sessions.load_or_start(session_id)
        if len(stored) or stored.system_prompt:
            coder.session = stored
    if args.prompt is not None or not style.live_terminal():
        window = max_context_tokens or 128000
        print(f"Using {args.model} (context {window})")

    raise SystemExit(
        await run_interactive_session(
            coder,
            initial_image=initial_image,
            model_name=args.model,
            prompt=args.prompt,
            permission_mode=args.permission_mode,
            session_id=session_id,
            compact_threshold=args.compact_threshold,
            disable_compaction=args.disable_compaction,
        )
    )


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

        backend = (
            "macOS Keychain"
            if SecretManager.keychain_available()
            else "./secrets/secrets.json (0600)"
        )
        print(f"\n{green('✓')} Saved {provider} API key to the {backend}")
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
            print(f"\n{green('✓')} Deleted {provider} API key")
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


def run() -> None:
    """Synchronous entry point for the installed console script.

    ``main`` is async, but a console script calls it synchronously. Without
    this wrapper, ``openagent auth login …`` returns a coroutine object and
    never awaits it — the command prints the coroutine and exits.
    """
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nGoodbye!")


if __name__ == "__main__":
    run()
