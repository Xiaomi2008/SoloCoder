"""Example: Session auto-save for crash recovery.

When auto_save is configured, the agent writes the full conversation history
to disk after every turn. If the process crashes, you can reload the session
from the saved JSON file.

Usage:
    python examples/example_auto_save.py
"""

from __future__ import annotations

import asyncio
import tempfile

from openagent import Agent, OpenAIProvider


async def main():
    # Create a temporary file for the session save
    save_path = tempfile.mktemp(suffix=".json")

    provider = OpenAIProvider(model="gpt-4o")
    agent = Agent(
        provider=provider,
        system_prompt="You are a helpful assistant.",
        auto_save=save_path,
    )

    print(f"Session will be auto-saved to: {save_path}")

    # Run a few turns
    result = await agent.run("What is Python?")
    print(f"Response 1: {result[:100]}...")

    result = await agent.run("What was my first question?")
    print(f"Response 2: {result[:100]}...")

    # Demonstrate session reload
    print(f"\nReloading session from {save_path}...")
    from openagent.core.session import Session

    loaded = Session.load(save_path)
    print(f"Loaded session has {len(loaded)} messages")

    # Clean up
    import os

    try:
        os.unlink(save_path)
    except OSError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
