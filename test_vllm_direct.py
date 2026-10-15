#!/usr/bin/env python3
"""Direct vLLM multimodal test - no interactive input."""

from __future__ import annotations

import asyncio
import base64
import httpx
import sys


def get_test_png_bytes():
    """Get a minimal valid PNG image (1x1 red pixel)."""
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )


async def test_vllm_multimodal(base_url: str, model_name: str = "qwen3.5-35b-a3b"):
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
        "model": model_name,
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


async def main():
    """Run test."""
    print("=" * 60)
    print("vLLM Multimodal Test - Qwen3.5")
    print("=" * 60)

    # Configuration - modify these to match your setup
    BASE_URL = "http://localhost:8000/v1"  # vLLM default
    MODEL_NAME = "qwen3.5-35b-a3b"

    print(f"\nConfiguration:")
    print(f"   API URL: {BASE_URL}")
    print(f"   Model: {MODEL_NAME}")

    # Check connection first
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{BASE_URL}/v1/models")
            if response.status_code == 200:
                print(f"\n✅ vLLM is responding!")
                models = response.json().get("data", [])
                print(f"   Available models: {len(models)}")
                for m in models[:5]:
                    print(f"   - {m.get('id', 'unknown')}")
    except Exception as e:
        print(f"\n❌ Cannot connect to {BASE_URL}: {e}")
        print("\nTo start vLLM:")
        print(f"  vllm serve Qwen/Qwen3.5-35B-A3B --port 8000")
        return

    # Run test
    print("\n" + "=" * 60)
    success = await test_vllm_multimodal(BASE_URL, MODEL_NAME)

    print("\n" + "=" * 60)
    print(f"Result: {'✅ PASSED' if success else '❌ FAILED'}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
