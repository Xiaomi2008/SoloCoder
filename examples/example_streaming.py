"""Example: Streaming output with on_chunk callback.

Demonstrates how to display LLM responses as they arrive instead of waiting
for the full response. This gives the user immediate feedback.

Usage:
    python examples/example_streaming.py
"""

from __future__ import annotations

import asyncio
import sys

from openagent import Agent, OpenAIProvider


async def main():
    provider = OpenAIProvider(model="gpt-4o")
    agent = Agent(provider=provider, system_prompt="You are a concise assistant.")

    print("Streaming example. Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ").strip()
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        print("Assistant: ", end="", flush=True)
        result = await agent.run(
            user_input,
            on_chunk=lambda chunk: sys.stdout.write(chunk) or sys.stdout.flush(),
        )
        print()  # newline after streaming


if __name__ == "__main__":
    asyncio.run(main())
