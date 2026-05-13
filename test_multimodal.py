#!/usr/bin/env python3
"""Test script for multimodal (image) support with Qwen3.5."""

from __future__ import annotations

import asyncio
import base64
import socket
import sys
from pathlib import Path


def is_port_open(host: str, port: int) -> bool:
    """Check if a port is open."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


async def test_image_analysis(base_url: str):
    """Test image analysis with a sample image."""
    from openagent.provider.openai import OpenAIProvider
    from openagent.apps.solocoder.agent import CoderAgent

    # Create provider
    provider = OpenAIProvider(
        model="qwen3.5-35b-a3b",
        base_url=base_url,
        api_key="EMPTY",  # vLLM typically uses EMPTY
    )

    # Create agent with computer use tools
    agent = CoderAgent(
        provider=provider,
        max_turns=10,
        working_dir=str(Path.home() / "Desktop"),
    )

    # Take a screenshot
    print("Taking screenshot...")
    from openagent.tools.computer_use import screenshot

    img_base64 = screenshot(return_base64=True)
    if not img_base64:
        print("Failed to capture screenshot")
        return

    print(f"Screenshot captured: {len(img_base64)} base64 chars")

    # Test multimodal analysis
    print("\n=== Testing Multimodal Image Analysis ===\n")
    print("Analyzing screenshot with Qwen3.5-35B-A3B...")
    print("This may take 10-30 seconds depending on image size and model performance.\n")

    try:
        result = await agent.run_multimodal(
            image_data=img_base64,
            text="Analyze this screenshot. Describe what you see on the screen, including any visible text, UI elements, windows, or content.",
        )

        print("\n=== Analysis Result ===\n")
        print(result)
        print("\n=== Test Complete ===")

    except Exception as e:
        print(f"\n=== Error ===\n")
        print(f"Error during multimodal analysis: {e}")
        print("\nPossible issues:")
        print("1. LM Studio may not be running with the correct model")
        print("2. Qwen3.5-35B-A3B may not have been loaded in LM Studio")
        print("3. The image may be too large (try using region-based screenshots)")
        sys.exit(1)


async def main():
    """Run tests."""
    print("=" * 60)
    print("SoloCoder Multimodal Test")
    print("=" * 60)

    # Get base URL from user
    import readline

    base_url = input(
        "\nEnter vLLM/OpenAI-compatible API URL (default: http://localhost:8000/v1): "
    ).strip()
    if not base_url:
        base_url = "http://localhost:8000/v1"  # vLLM default

    # Check if vLLM/server is running
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    if not is_port_open(host, port):
        print(
            f"\n❌ vLLM/OpenAI-compatible server is not running on {base_url}"
        )
        print("\nPlease:")
        if "vllm" in base_url.lower() or port == 8000:
            print("1. Start vLLM with: vllm serve Qwen/Qwen3.5-35B-A3B")
        else:
            print("1. Start your OpenAI-compatible server (LM Studio, vLLM, etc.)")
        print("2. Ensure it's accessible at the URL above")
        print("3. Run this test again")
        sys.exit(1)

    print(f"\n✅ Connected to {base_url}")

    # Check if we have any images to test with
    desktop = Path.home() / "Desktop"
    if desktop.exists():
        images = list(desktop.glob("*.png")) + list(desktop.glob("*.jpg"))
        if images:
            print(f"✅ Found {len(images)} image(s) on Desktop")

    print("\n" + "=" * 60)
    print("Starting test...")
    print("=" * 60)

    await test_image_analysis(base_url)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTest cancelled by user")
        sys.exit(0)
    except Exception as e:
        print(f"\nTest failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
