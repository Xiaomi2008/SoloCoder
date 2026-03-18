"""Example usage of OpenAgent with built-in file operation tools.

This example demonstrates how to use the built-in tools (read, write, edit, glob, grep)
with an Agent for file operations tasks.

Run with:
    python example_with_tools.py openai
    python example_with_tools.py anthropic
    python example_with_tools.py ollama
"""

import asyncio
import os
import sys


def get_env_clean(key: str) -> str | None:
    """Get env var with improved robustness for common CLI mistakes."""
    val = os.environ.get(key)
    if val:
        return val.strip()

    for k, v in os.environ.items():
        if k.strip() == key:
            return v.strip()
    return None


def make_provider(name: str):
    """Create a provider instance based on the name."""
    from openagent import (
        AnthropicProvider,
        GoogleProvider,
        OllamaProvider,
        OpenAIProvider,
    )

    providers = {
        "openai": lambda: OpenAIProvider(
            model="gpt-4o",
            api_key=get_env_clean("OPENAI_API_KEY"),
        ),
        "anthropic": lambda: AnthropicProvider(
            model="claude-sonnet-4-20250514",
            api_key=get_env_clean("ANTHROPIC_API_KEY"),
        ),
        "google": lambda: GoogleProvider(
            model="gemini-2.0-flash",
            api_key=get_env_clean("GOOGLE_API_KEY"),
        ),
        "ollama": lambda: OllamaProvider(
            model=os.environ.get("OLLAMA_MODEL", "glm-4.7-flash"),
            host=get_env_clean("OLLAMA_HOST") or "http://localhost:11439",
        ),
    }

    factory = providers.get(name)
    if not factory:
        print(f"Unknown provider: {name}. Choose from: {', '.join(providers)}")
        sys.exit(1)

    if name != "ollama":
        env_var_name = f"{name.upper()}_API_KEY"
        if not get_env_clean(env_var_name):
            print(f"Warning: {env_var_name} not found in environment.")

    return factory()


async def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="OpenAgent Example with Built-in File Tools"
    )
    parser.add_argument(
        "provider",
        nargs="?",
        default="openai",
        help="LLM Provider (default: openai)",
    )
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help="Custom task to run. If not provided, runs a demo task.",
    )

    args = parser.parse_args()

    provider_name = args.provider
    provider = make_provider(provider_name)

    # Import and use built-in tools
    from openagent import Agent
    from openagent.tools import edit, glob, grep, read, write

    agent = Agent(
        provider=provider,
        system_prompt="""You are a helpful assistant with file operations capabilities.

Tool Usage Guidelines:
- Use 'read' to read files by absolute path. You can specify line_start and line_end for ranges.
- Use 'write' to create or overwrite files. It will create parent directories if needed.
- Use 'edit' to make targeted find-and-replace edits in existing files.
- Use 'glob' to search for files by pattern (e.g., "*.py", "**/*.md").
- Use 'grep' to search file contents by keyword or regex.

Always use absolute paths for file operations. Be careful with write/edit operations - verify the path and content before making changes.""",
        tools=[read, write, edit, glob, grep],
    )

    print(f"Using provider: {provider_name}")
    print("---")

    task = (
        args.task
        or """Find all Python files in this project that contain 'async' using glob and grep. Then read the first one to see its structure."""
    )

    answer = await agent.run(task)
    print(answer)


if __name__ == "__main__":
    asyncio.run(main())
