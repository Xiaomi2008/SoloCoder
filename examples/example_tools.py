"""Example usage of OpenAgent with built-in tools.

Run with one of:
    OPENAI_API_KEY=sk-... python example_tools.py openai
    ANTHROPIC_API_KEY=sk-... python example_tools.py anthropic
    GOOGLE_API_KEY=... python example_tools.py google
    python example_tools.py ollama
"""

import asyncio
import sys

from openagent import Agent, AnthropicProvider, GoogleProvider, OllamaProvider, OpenAIProvider
from openagent.tools import (
    bash, edit, glob, grep, notebook_edit, read, web_fetch, web_search, write,
)


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
    import os

    parser = argparse.ArgumentParser(description="OpenAgent Example with Built-in Tools")
    parser.add_argument("provider", nargs="?", default="openai", help="LLM Provider")
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help="Custom task to run. If not provided, runs a demo task.",
    )

    args = parser.parse_args()

    provider_name = args.provider
    provider = make_provider(provider_name)

    # All built-in tools available to the agent
    tools = [read, write, edit, glob, grep, notebook_edit, web_search, web_fetch]

    try:
        agent = Agent(
            provider=provider,
            system_prompt="""You are a helpful assistant with file operations and web search capabilities.

Tool Usage Guidelines:
- Use 'read' to read files by absolute path. You can specify line_start and line_end for ranges.
- Use 'write' to create or overwrite files. It will create parent directories if needed.
- Use 'edit' to make targeted find-and-replace edits in existing files.
- Use 'glob' to search for files by pattern (e.g., "*.py", "**/*.md").
- Use 'grep' to search file contents by keyword or regex.
- Use 'notebook_edit' to edit Jupyter notebook cells.
- Use 'web_search' to find current information on the web.
- Use 'web_fetch' to read the full content of a URL.

Always use absolute paths for file operations. Be careful with write/edit operations - verify the path and content before making changes.""",
            tools=tools,
        )

        print(f"Using provider: {provider_name}")
        print("---")

        task = args.task or """Find all Python files in this project that contain 'async' using glob and grep. Then read the first one to see its structure."""

        answer = await agent.run(task)
        print(answer)

    finally:
        pass


if __name__ == "__main__":
    asyncio.run(main())
