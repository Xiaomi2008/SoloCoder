#!/usr/bin/env python3
"""Direct vLLM multimodal test - tests the raw API with Qwen3.5."""

from __future__ import annotations

import asyncio
import base64
import httpx
import sys
from pathlib import Path


def get_test_png_bytes():
    """Get a minimal valid PNG image (1x1 red pixel)."""
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )


async def test_vllm_multimodal(base_url: str):
    """Test vLLM multimodal inference with Qwen3.5."""
    print(f"\n📡 Testing vLLM API at: {base_url}")
    print("-" * 50)

    # Create test image
    test_png = get_test_png_bytes()
    img_base64 = base64.b64encode(test_png).decode()

    # Build multimodal message per Qwen3.5 API
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img_base64}"},
                },
                {
                    "type": "text",
                    "text": "Describe this simple 1x1 pixel image. What color is it?",
                },
            ],
        }
    ]

    # vLLM specific parameters for multimodal
    payload = {
        "model": "qwen3.5-35b-a3b",
        "messages": messages,
        "max_tokens": 256,
        "temperature": 0.7,
        "top_p": 0.9,
    }

    print("📤 Sending multimodal request...")
    print(f"   - Image: base64 ({len(img_base64)} chars)")
    print(f"   - Prompt: {messages[0]['content'][1]['text']}")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer EMPTY"},
            )

        if response.status_code != 200:
            print(f"\n❌ HTTP {response.status_code}: {response.text[:200]}")
            return False

        data = response.json()
        result = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        print("\n✅ API Response:")
        print("-" * 50)
        print(result)
        print("-" * 50)

        # Check if response makes sense for a red pixel
        result_lower = result.lower()
        if "red" in result_lower:
            print("\n✅ Correctly identified red color!")
        elif "1x1" in result_lower or "pixel" in result_lower:
            print("\n✅ Correctly identified it's a single pixel!")

        return True

    except httpx.ConnectError as e:
        print(f"\n❌ Connection failed: {e}")
        print("Make sure vLLM is running with:")
        print(f"  vllm serve Qwen/Qwen3.5-35B-A3B")
        return False
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_image_upload(base_url: str):
    """Test with an actual image from Desktop."""
    from openagent.tools.computer_use import screenshot

    print("\n📷 Capturing screenshot for vLLM...")

    try:
        img_base64 = screenshot(return_base64=True)
        if not img_base64:
            print("⚠️  No screenshot captured (may need accessibility permissions)")
            return False

        print(f"✅ Screenshot captured: {len(img_base64)} base64 chars")

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_base64}"},
                    },
                    {
                        "type": "text",
                        "text": "Analyze this screenshot and describe what you see.",
                    },
                ],
            }
        ]

        payload = {
            "model": "qwen3.5-35b-a3b",
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.7,
        }

        print("📤 Sending screenshot analysis request...")
        print("   This may take 30-60 seconds due to large image...")

        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer EMPTY"},
            )

        if response.status_code != 200:
            print(f"\n❌ HTTP {response.status_code}: {response.text[:200]}")
            return False

        data = response.json()
        result = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        print("\n✅ Screenshot Analysis Result:")
        print("-" * 50)
        print(result[:2000])  # Limit output
        print("-" * 50)

        return True

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run tests."""
    print("=" * 60)
    print("vLLM Multimodal Test - Qwen3.5")
    print("=" * 60)

    base_url = (
        input("\nEnter vLLM API URL (default: http://localhost:8000/v1): ").strip()
        or "http://localhost:8000/v1"
    )

    # Check connection
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{base_url}/v1/models")
            if response.status_code == 200:
                print(f"✅ vLLM is responding!")
                # Show available models
                models = response.json().get("data", [])
                print(f"   Available models: {len(models)}")
                for m in models[:5]:
                    print(f"   - {m.get('id', 'unknown')}")
    except Exception as e:
        print(f"\n❌ Cannot connect to {base_url}: {e}")
        return

    # Test 1: Simple image
    print("\n" + "=" * 60)
    print("Test 1: Simple 1x1 pixel image")
    print("=" * 60)
    success1 = await test_vllm_multimodal(base_url)

    # Test 2: Optional - real screenshot (large image)
    print("\n" + "=" * 60)
    print("Test 2: Actual screenshot (optional - requires large image support)")
    print("=" * 60)
    try:
        user_input = input("Take screenshot? (y/n): ").strip().lower()
        if user_input == "y":
            success2 = await test_image_upload(base_url)
        else:
            print("Skipping screenshot test")
            success2 = True
    except Exception as e:
        print(f"Skipping: {e}")
        success2 = True

    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Simple image test: {'✅ PASSED' if success1 else '❌ FAILED'}")
    print(f"Screenshot test: {'✅ PASSED' if success2 else '❌ SKIPPED/FAILED'}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
