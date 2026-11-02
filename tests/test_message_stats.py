"""Token counts and speeds: from the provider stream to ``message_stats``."""

from __future__ import annotations

from types import SimpleNamespace as NS
from typing import Any
from unittest.mock import MagicMock

from openagent.core.types import Message
from openagent.provider.openai import OpenAIProvider
from openagent.providers import (
    CallClock,
    ProviderMessageCompleted,
    ProviderMessageStarted,
    ProviderTextDelta,
    ProviderUsage,
)
from openagent.runtime.runner import AgentRunner
from tests.support.streaming_provider import ScriptedStreamProvider


def chunk(content: str | None = None, **extra: Any) -> NS:
    delta = NS(content=content, tool_calls=None, reasoning_content=None, reasoning=None)
    extra.setdefault("usage", None)
    return NS(id="chatcmpl_1", choices=[NS(delta=delta)] if content is not None else [], **extra)


def provider_with(*streams: list[NS] | Exception) -> tuple[OpenAIProvider, list[dict[str, Any]]]:
    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider.model = "local"
    provider.base_url = None
    requests: list[dict[str, Any]] = []
    queue = list(streams)

    async def create(**kwargs: Any):
        requests.append(dict(kwargs))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item

        async def replay():
            for c in item:
                yield c

        return replay()

    provider._client = MagicMock()
    provider._client.chat.completions.create = create
    return provider, requests


async def events_of(provider: OpenAIProvider) -> list[Any]:
    return [e async for e in provider.stream(messages=[Message(role="user", content="hi")])]


async def test_llama_cpp_timings_are_used_as_the_speeds():
    usage = NS(prompt_tokens=1200, completion_tokens=80, prompt_tokens_details=None)
    timings = {"prompt_n": 200, "prompt_per_second": 812.5, "predicted_n": 80, "predicted_per_second": 41.25}
    provider, requests = provider_with(
        [chunk("Hel"), chunk("lo"), chunk(usage=usage, timings=timings)]
    )

    events = await events_of(provider)

    assert requests[0]["stream_options"] == {"include_usage": True}
    stats = next(e for e in events if isinstance(e, ProviderUsage))
    assert (stats.input_tokens, stats.output_tokens) == (1200, 80)
    assert stats.cached_tokens == 1000  # prompt_tokens - prompt_n: the reused prefix
    assert (stats.prefill_tps, stats.decode_tps) == (812.5, 41.25)
    assert isinstance(events[-1], ProviderMessageCompleted)
    assert events.index(stats) == len(events) - 2


async def test_a_fully_cached_prompt_reports_no_prefill_rate():
    usage = NS(prompt_tokens=900, completion_tokens=5, prompt_tokens_details=None)
    timings = {"prompt_n": 0, "prompt_per_second": float("inf"), "predicted_per_second": 30.0}
    provider, _ = provider_with([chunk("ok"), chunk(usage=usage, timings=timings)])

    stats = next(e for e in await events_of(provider) if isinstance(e, ProviderUsage))

    assert stats.decode_tps == 30.0
    # Not infinity, and not 0 tok/s: nothing was read, so there is no rate.
    assert stats.prefill_tps is None


async def test_a_server_that_rejects_stream_options_still_answers():
    provider, requests = provider_with(
        Exception("Unrecognized request argument supplied: stream_options"),
        [chunk("hi")],
    )

    events = await events_of(provider)

    assert "stream_options" not in requests[1]
    assert [type(e) for e in events] == [ProviderMessageStarted, ProviderTextDelta, ProviderMessageCompleted]


async def test_without_usage_there_is_no_stats_event():
    provider, _ = provider_with([chunk("hi")])

    assert not any(isinstance(e, ProviderUsage) for e in await events_of(provider))


def test_the_clock_measures_what_the_server_did_not(monkeypatch):
    times = iter([100.0, 100.5, 102.5])
    monkeypatch.setattr("openagent.providers.events.time.monotonic", lambda: next(times))
    clock = CallClock()  # sent at 100.0
    clock.token()  # first token at 100.5
    clock.token()  # later tokens do not move it

    stats = clock.usage("m", input_tokens=1100, output_tokens=81, cached_tokens=100)  # ends at 102.5

    assert stats.time_to_first_token == 0.5
    assert stats.prefill_tps == 2000.0  # 1000 uncached tokens in 0.5 s
    assert stats.decode_tps == 40.0  # 80 tokens after the first, in 2 s


def test_the_clock_has_nothing_to_report_without_counts():
    assert CallClock().usage("m", input_tokens=None, output_tokens=None) is None


async def test_the_runner_sends_message_stats_before_message_completed():
    turn = [
        ProviderMessageStarted(message_id="p1"),
        ProviderTextDelta(message_id="p1", delta="hi"),
        ProviderUsage(message_id="p1", input_tokens=50, output_tokens=2, prefill_tps=500.0, decode_tps=20.0),
        ProviderMessageCompleted(message_id="p1"),
    ]
    runner = AgentRunner(ScriptedStreamProvider([turn]))

    events = [e async for e in runner.stream("go")]

    types = [e.type for e in events]
    assert types.index("message_stats") == types.index("message_completed") - 1
    stats = events[types.index("message_stats")]
    completed = events[types.index("message_completed")]
    assert stats.message_id == completed.message_id
    assert (stats.input_tokens, stats.output_tokens, stats.prefill_tps, stats.decode_tps) == (50, 2, 500.0, 20.0)
