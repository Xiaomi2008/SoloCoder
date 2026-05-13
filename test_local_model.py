"""Benchmark the local model: speed, latency, tokens/sec."""
import asyncio
import time

from openagent import OpenAIProvider
from openagent.core.types import Message


async def benchmark_chat(provider, prompt, label, expected_tokens=None):
    """Measure chat latency and throughput."""
    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"{'='*50}")

    start = time.perf_counter()
    ttft = None  # time to first token
    tokens = 0
    deltas = []

    async for event in provider.stream(
        messages=[Message(role="user", content=prompt)],
        system_prompt="You are a helpful assistant.",
    ):
        now = time.perf_counter()
        if ttft is None:
            ttft = now - start
        if hasattr(event, "delta") and event.delta:
            tokens += 1
            deltas.append(event.delta)
    elapsed = time.perf_counter() - start

    tokens_per_sec = tokens / elapsed if elapsed > 0 else 0
    response = "".join(deltas)
    word_count = len(response.split())
    words_per_sec = word_count / elapsed if elapsed > 0 else 0

    print(f"  Time to first token:  {ttft*1000:.0f} ms")
    print(f"  Total time:           {elapsed*1000:.0f} ms")
    print(f"  Token count (est.):   {tokens}")
    print(f"  Tokens/sec:           {tokens_per_sec:.1f}")
    print(f"  Words/sec:            {words_per_sec:.1f}")
    print(f"\n  Response (first 200 chars):\n  {response[:200]}...")

    return {
        "label": label,
        "ttft_ms": ttft * 1000,
        "total_ms": elapsed * 1000,
        "tokens": tokens,
        "tokens_per_sec": tokens_per_sec,
    }


async def main():
    provider = OpenAIProvider(
        model="Qwen3.6-27B-Q4_K_M-mtp.gguf",
        base_url="http://172.16.0.217:8000/v1",
    )

    results = []

    # Test 1: short response
    results.append(await benchmark_chat(
        provider,
        "What is 2+2? One sentence.",
        "Short response (math)",
    ))

    # Test 2: medium response
    results.append(await benchmark_chat(
        provider,
        "Explain how a CPU works in 3-4 sentences.",
        "Medium response (explanation)",
    ))

    # Test 3: longer response
    results.append(await benchmark_chat(
        provider,
        "Write a Python function that implements binary search on a sorted list. Include type hints and docstring.",
        "Long response (code generation)",
    ))

    # Summary
    print(f"\n{'='*50}")
    print("  SUMMARY")
    print(f"{'='*50}")
    print(f"  {'Test':<30} {'TTFT (ms)':>10} {'Total (ms)':>10} {'Tok/s':>8}")
    print(f"  {'-'*58}")
    for r in results:
        print(f"  {r['label']:<30} {r['ttft_ms']:>10.0f} {r['total_ms']:>10.0f} {r['tokens_per_sec']:>8.1f}")


if __name__ == "__main__":
    asyncio.run(main())
