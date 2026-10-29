"""Responses-API adapter: the dialect the ChatGPT subscription backend speaks."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openagent.core.types import Message, TextBlock, ToolDef, ToolResultBlock, ToolUseBlock
from openagent.provider.responses import OpenAIResponsesProvider, ResponsesConverterMixin


@pytest.fixture
def converter() -> ResponsesConverterMixin:
    class _C(ResponsesConverterMixin):
        pass

    return _C()


# ------------------------------------------------------------------ requests

def test_system_prompt_becomes_instructions_not_a_message(converter):
    payload = converter.convert_messages([Message(role="user", content="hi")], "You are a coder.")
    assert payload["instructions"] == "You are a coder."
    assert all(item["role"] != "system" for item in payload["input"])


def test_multiple_system_messages_are_joined(converter):
    payload = converter.convert_messages(
        [Message(role="system", content="B"), Message(role="user", content="hi")]
    )
    assert payload["instructions"] == "B"


def test_tool_results_become_function_call_output_items(converter):
    message = Message(
        role="tool_result",
        content=[ToolResultBlock(tool_use_id="call_1", content="42 lines")],
    )
    payload = converter.convert_messages([message])
    item = payload["input"][0]
    assert item["type"] == "function_call_output"
    assert item["call_id"] == "call_1"
    assert item["output"] == "42 lines"


def test_assistant_tool_calls_become_function_call_items(converter):
    message = Message(
        role="assistant",
        content=[
            TextBlock(text="reading the file"),
            ToolUseBlock(id="call_9", name="read_file", arguments={"path": "a.py"}),
        ],
    )
    payload = converter.convert_messages([message])
    items = payload["input"]
    assert items[0]["type"] == "function_call"
    assert items[0]["call_id"] == "call_9"
    assert json.loads(items[0]["arguments"]) == {"path": "a.py"}
    assert items[1]["role"] == "assistant"
    assert items[1]["content"][0]["type"] == "output_text"


def test_tools_use_the_flat_responses_shape(converter):
    tools = converter.convert_tools([ToolDef(name="bash", description="run", parameters={"type": "object"})])
    assert tools == [{"type": "function", "name": "bash", "description": "run", "parameters": {"type": "object"}}]


# ----------------------------------------------------------------- responses

def test_response_items_become_canonical_blocks(converter):
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="done")],
            ),
            SimpleNamespace(
                type="function_call",
                call_id="call_3",
                name="bash",
                arguments=json.dumps({"command": "ls"}),
            ),
        ]
    )
    message = converter.convert_response(response)
    assert message.role == "assistant"
    assert message.text == "done"
    assert message.tool_calls[0].id == "call_3"
    assert message.tool_calls[0].arguments == {"command": "ls"}


def test_non_json_tool_arguments_do_not_crash_the_turn(converter):
    response = SimpleNamespace(
        output=[SimpleNamespace(type="function_call", call_id="c", name="bash", arguments="{truncated")]
    )
    message = converter.convert_response(response)
    assert message.tool_calls[0].arguments["_raw"].startswith("{truncated")


def test_empty_response_still_yields_a_message(converter):
    assert converter.convert_response(SimpleNamespace(output=[])).text == ""


# ------------------------------------------------------------------- provider

class FakeResponses:
    def __init__(self, result):
        self.result = result
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.result


@pytest.fixture
def provider(monkeypatch):
    provider = OpenAIResponsesProvider(
        model="gpt-5-codex",
        api_key="jwt",
        base_url="https://chatgpt.com/backend-api/codex",
        default_headers={"OpenAI-Organization": "acct"},
    )
    provider._client = SimpleNamespace(responses=FakeResponses(
        SimpleNamespace(output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text="ok")])])
    ))
    return provider


async def test_chat_sends_instructions_tools_and_store_false(provider):
    await provider.chat(
        [Message(role="user", content="hi")],
        tools=[ToolDef(name="bash", description="run", parameters={"type": "object"})],
        system_prompt="sys",
    )
    sent = provider._client.responses.kwargs
    assert sent["model"] == "gpt-5-codex"
    assert sent["instructions"] == "sys"
    assert sent["store"] is False
    assert sent["tools"][0]["name"] == "bash"
    assert "stream" not in sent


async def test_stream_yields_deltas_then_a_terminal_event(provider):
    async def events():
        # Real wire order: response.created carries the id every later event shares.
        yield SimpleNamespace(type="response.created", response=SimpleNamespace(id="resp_1"))
        yield SimpleNamespace(type="response.output_text.delta", delta="he")
        yield SimpleNamespace(type="response.output_text.delta", delta="llo")
        yield SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(type="function_call", call_id="c1", name="bash", arguments='{"command":"ls"}'),
        )
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id="resp_1",
                output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text="hello")])],
            ),
        )

    async def create(**kwargs):
        return events()

    provider._client = SimpleNamespace(responses=SimpleNamespace(create=create))

    collected = [event async for event in provider.stream([Message(role="user", content="hi")])]
    from openagent.providers.events import (
        ProviderMessageCompleted,
        ProviderMessageStarted,
        ProviderTextDelta,
        ProviderToolCall,
    )

    assert isinstance(collected[0], ProviderMessageStarted)
    assert [e.delta for e in collected if isinstance(e, ProviderTextDelta)] == ["he", "llo"]
    tool_call = next(e for e in collected if isinstance(e, ProviderToolCall))
    assert (tool_call.id, tool_call.name, tool_call.arguments) == ("c1", "bash", {"command": "ls"})
    assert isinstance(collected[-1], ProviderMessageCompleted)
    # Every event in a stream shares one message id — the UI groups by it.
    assert len({e.message_id for e in collected}) == 1
    assert collected[-1].message_id == "resp_1"


async def test_stream_reports_errors_as_events(provider):
    async def create(**kwargs):
        raise RuntimeError("401 unauthorized")

    provider._client = SimpleNamespace(responses=SimpleNamespace(create=create))
    collected = [event async for event in provider.stream([Message(role="user", content="hi")])]
    from openagent.providers.events import ProviderError, ProviderMessageStarted

    assert isinstance(collected[0], ProviderMessageStarted)
    errors = [e for e in collected if isinstance(e, ProviderError)]
    assert len(errors) == 1
    assert "401" in errors[0].error
    assert not any(type(e).__name__ == "ProviderMessageCompleted" for e in collected)


async def test_stream_only_chat_collects_the_stream():
    from openagent.provider.responses import OpenAIResponsesProvider

    provider = OpenAIResponsesProvider(api_key="jwt", base_url="http://localhost:1", stream_only=True)
    sent = {}

    async def events():
        yield SimpleNamespace(type="response.created", response=SimpleNamespace(id="resp_1"))
        yield SimpleNamespace(type="response.output_text.delta", delta="ok")
        yield SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(type="function_call", call_id="c1", name="bash", arguments='{"command":"ls"}'),
        )

    async def create(**kwargs):
        sent.update(kwargs)
        return events()

    provider._client = SimpleNamespace(responses=SimpleNamespace(create=create))
    message = await provider.chat([Message(role="user", content="hi")])
    assert sent["stream"] is True
    assert message.text == "ok"
    assert message.tool_calls[0].name == "bash"
    assert message.tool_calls[0].arguments == {"command": "ls"}


async def test_stream_only_chat_raises_the_backend_error():
    from openagent.provider.responses import OpenAIResponsesProvider

    provider = OpenAIResponsesProvider(api_key="jwt", base_url="http://localhost:1", stream_only=True)

    async def create(**kwargs):
        raise ValueError("model_not_found")

    provider._client = SimpleNamespace(responses=SimpleNamespace(create=create))
    with pytest.raises(RuntimeError, match="model_not_found"):
        await provider.chat([Message(role="user", content="hi")])


async def test_chat_completions_kwargs_are_translated(provider):
    await provider.chat([Message(role="user", content="hi")], reasoning_effort="high", max_tokens=256)
    sent = provider._client.responses.kwargs
    assert sent["reasoning"] == {"effort": "high"}
    assert sent["max_output_tokens"] == 256
    assert "reasoning_effort" not in sent and "max_tokens" not in sent


def _streaming(provider, events):
    sent = {}

    async def create(**kwargs):
        sent.update(kwargs)

        async def gen():
            yield SimpleNamespace(type="response.created", response=SimpleNamespace(id="resp_1"))
            for event in events:
                yield event

        return gen()

    provider._client = SimpleNamespace(responses=SimpleNamespace(create=create))
    return sent


async def test_a_tool_call_sent_whole_on_added_and_done_runs_once(provider):
    from openagent.providers.events import ProviderToolCall

    item = SimpleNamespace(type="function_call", call_id="c1", name="bash", arguments='{"command":"rm x"}')
    _streaming(provider, [
        SimpleNamespace(type="response.output_item.added", item=item),
        SimpleNamespace(type="response.output_item.done", item=item),
    ])
    collected = [e async for e in provider.stream([Message(role="user", content="hi")])]
    assert len([e for e in collected if isinstance(e, ProviderToolCall)]) == 1


async def test_reasoning_summaries_stream_as_thinking(provider):
    from openagent.providers.events import ProviderReasoningDelta, ProviderTextDelta

    _streaming(provider, [
        SimpleNamespace(type="response.reasoning_summary_part.added", summary_index=0),
        SimpleNamespace(type="response.reasoning_summary_text.delta", delta="**Factoring**"),
        SimpleNamespace(type="response.reasoning_summary_part.added", summary_index=1),
        SimpleNamespace(type="response.reasoning_summary_text.delta", delta="7 x 143"),
        SimpleNamespace(type="response.output_text.delta", delta="No."),
    ])
    collected = [e async for e in provider.stream([Message(role="user", content="hi")])]
    thinking = "".join(e.delta for e in collected if isinstance(e, ProviderReasoningDelta))
    assert thinking == "**Factoring**\n\n7 x 143"
    assert [e.delta for e in collected if isinstance(e, ProviderTextDelta)] == ["No."]


async def test_a_reasoning_model_asks_for_a_summary_alongside_the_effort():
    provider = OpenAIResponsesProvider(api_key="jwt", base_url="http://localhost:1", reasoning_summary="auto")
    sent = _streaming(provider, [])
    _ = [e async for e in provider.stream([Message(role="user", content="hi")], reasoning_effort="low")]
    assert sent["reasoning"] == {"effort": "low", "summary": "auto"}


async def test_a_model_without_reasoning_gets_no_reasoning_parameter(provider):
    sent = _streaming(provider, [])
    _ = [e async for e in provider.stream([Message(role="user", content="hi")])]
    assert "reasoning" not in sent
