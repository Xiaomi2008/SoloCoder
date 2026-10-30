"""Claude in the agent loop: text, thinking and tool calls reach the runner."""

from __future__ import annotations

from types import SimpleNamespace as NS
from typing import Any

from openagent.core.tool import tool
from openagent.provider.anthropic import AnthropicProvider
from openagent.runtime.runner import AgentRunner


class FakeMessages:
    """Replays one scripted event list per request; records the requests."""

    def __init__(self, *turns: list[Any]) -> None:
        self.turns = list(turns)
        self.requests: list[dict[str, Any]] = []

    def stream(self, **kwargs):
        self.requests.append(kwargs)
        events = self.turns.pop(0)

        class _Stream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def __aiter__(self):
                for event in events:
                    yield event

        return _Stream()


def text(*parts: str) -> list[Any]:
    return [
        NS(type="message_start", message=NS(id="msg_t")),
        NS(type="content_block_start", index=0, content_block=NS(type="text")),
        *[NS(type="content_block_delta", index=0, delta=NS(type="text_delta", text=p)) for p in parts],
        NS(type="content_block_stop", index=0),
        # The SDK's own helper event, which repeats the deltas.
        NS(type="text", text="".join(parts)),
        NS(type="message_stop"),
    ]


def tool_call(name: str, json_parts: list[str]) -> list[Any]:
    return [
        NS(type="message_start", message=NS(id="msg_c")),
        NS(type="content_block_start", index=0, content_block=NS(type="thinking")),
        NS(type="content_block_delta", index=0, delta=NS(type="thinking_delta", thinking="I should list files.")),
        NS(type="content_block_stop", index=0),
        NS(type="content_block_start", index=1, content_block=NS(type="tool_use", id="toolu_1", name=name)),
        *[NS(type="content_block_delta", index=1, delta=NS(type="input_json_delta", partial_json=p)) for p in json_parts],
        NS(type="content_block_stop", index=1),
        NS(type="message_stop"),
    ]


def provider(messages: FakeMessages) -> AnthropicProvider:
    p = AnthropicProvider.__new__(AnthropicProvider)
    p.model = "claude-sonnet-5-5"
    p._client = NS(messages=messages)
    return p


@tool
def list_files(folder: str) -> str:
    """List a folder."""
    return f"a.py b.py in {folder}"


async def test_a_claude_tool_call_runs_and_the_answer_streams():
    """Every Claude turn used to reach the runner empty: no text, no tools."""
    fake = FakeMessages(tool_call("list_files", ['{"fol', 'der": "src"}']), text("Two ", "files."))
    runner = AgentRunner(provider(fake), tools=[list_files], reasoning_effort="low")

    events = [e async for e in runner.stream("what is in src?")]
    types = [e.type for e in events]

    assert "reasoning_delta" in types
    started = next(e for e in events if e.type == "tool_call_started")
    assert started.tool_name == "list_files" and started.arguments == {"folder": "src"}
    done = next(e for e in events if e.type == "tool_call_completed")
    assert "a.py b.py in src" in str(done.result)
    assert events[-1].type == "run_completed"
    assert events[-1].result.output_text == "Two files."
    # The second request carries the tool result back, as Anthropic expects.
    last = fake.requests[1]["messages"][-1]
    assert last["role"] == "user" and last["content"][0]["type"] == "tool_result"
    # The Messages API rejects reasoning_effort; it must not be sent.
    assert all("reasoning_effort" not in r for r in fake.requests)


async def test_invalid_tool_json_fails_the_turn_with_a_reason():
    fake = FakeMessages(tool_call("list_files", ['{"folder": ']))
    runner = AgentRunner(provider(fake), tools=[list_files])

    events = [e async for e in runner.stream("go")]

    assert events[-1].type == "run_failed"
    assert "not valid JSON" in events[-1].error
