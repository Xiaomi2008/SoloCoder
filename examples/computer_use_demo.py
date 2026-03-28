#!/usr/bin/env python3
"""Demo: Computer Use with SoloCoder Agent.

This example shows how to use the computer use tools to automate
tasks on your Mac using a vision-capable LLM like Qwen3.5-VL.

Usage:
    python examples/computer_use_demo.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
import sys

sys.path.insert(0, str(project_root))

from openagent import OpenAIProvider
from openagent.coder import CoderAgent
from openagent.tools.computer_use import (
    click,
    double_click,
    get_screen_resolution,
    key_combination,
    move_mouse,
    screenshot,
    scroll,
    type_text,
    wait,
)


async def main():
    """Run a demo session with computer use tools."""
    print("=" * 60)
    print("SoloCoder Computer Use Demo")
    print("=" * 60)

    # Initialize provider - using LM Studio or local OpenAI-compatible endpoint
    # Qwen3.5-35B-A3B is an Image-Text-to-Text multimodal model that can analyze screenshots
    provider = OpenAIProvider(
        model="qwen3.5-35b-a3b",
        base_url="http://localhost:1234/v1",
        api_key="dummy",  # Not required for local servers
    )

    # Create agent with computer use tools
    tools = [
        # Computer use tools
        screenshot,
        click,
        double_click,
        type_text,
        key_combination,
        move_mouse,
        scroll,
        get_screen_resolution,
        wait,
        # Plus existing file and shell tools for complete automation
    ]

    agent = CoderAgent(
        provider=provider,
        max_turns=50,  # Give more turns for complex automation
        working_dir=str(Path.home() / "Desktop"),  # Default to Desktop
        tools=tools,
    )

    # System prompt template for computer use
    computer_use_system_prompt = """You are an AI agent that can control a Mac computer like a human user. You have access to:

1. Visual tools:
   - screenshot(): See the current screen (returns base64 PNG for vision analysis)
   - get_screen_resolution(): See available screen dimensions

2. Mouse tools:
   - click(x, y): Click at screen coordinates
   - double_click(x, y): Double-click at screen coordinates
   - move_mouse(x, y): Move cursor to coordinates
   - scroll(x, y, clicks): Scroll at location

3. Keyboard tools:
   - type_text("text"): Type text into focused element
   - key_combination("cmd+c"): Press shortcuts (use "cmd" for Command key)

4. Timing:
   - wait(seconds): Wait for UI to update

WORKFLOW:
1. Start with screenshot() to see current state
2. Analyze what you see and plan your next action
3. Use appropriate mouse/keyboard tools to interact
4. Verify with another screenshot if needed
5. Repeat until task is complete

KEYBOARD SHORTCUTS (macOS):
- cmd+c: Copy, cmd+v: Paste, cmd+x: Cut, cmd+z: Undo
- cmd+tab: Switch app, cmd+space: Spotlight search
- cmd+w: Close window, cmd+q: Quit app, cmd+h: Hide app

Always think step-by-step and verify your actions."""

    print("\n" + computer_use_system_prompt)
    print("\n" + "=" * 60)
    print("Example Tasks to Try:")
    print("=" * 60)
    print("1. 'Open Safari and navigate to example.com'")
    print("2. 'Create a new folder called Demo on the Desktop'")
    print("3. 'Take a screenshot of this terminal window'")
    print("4. 'Open Finder and go to Downloads'")
    print("5. Type anything here to test the typing tool")
    print("=" * 60)

    # Run interactive session
    try:
        while True:
            user_input = input("\nTask: ")
            if user_input.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break

            # Use the agent to execute the task
            result = await agent.run(user_input)
            print(f"\n{result}")

    except KeyboardInterrupt:
        print("\n\nGoodbye!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"\nError: {e}")
        print("\nMake sure you have:")
        print("1. Installed computer use dependencies: pip install pyautogui pillow")
        print("2. Set up an LM Studio or local OpenAI-compatible endpoint")
        print("3. Running the server before starting this demo")
