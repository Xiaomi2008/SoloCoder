from __future__ import annotations

import asyncio

import pytest

from openagent.core.types import Message, TextBlock, ToolResultBlock, ToolUseBlock
from openagent.runtime import (
    Agent,
    AgentResult,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
)


async def test_runtime_agent_run_returns_agent_result(mock_provider) -> None:
    provider = mock_provider([Message(role="assistant", content="Hello from runtime")])
    agent = Agent(provider=provider, system_prompt="Be helpful")

    result = await agent.run("Hi")

    assert result == AgentResult(
        run_id=result.run_id,
        final_message_id=result.final_message_id,
        output_text="Hello from runtime",
    )
    assert [message.role for message in agent.session.messages] == ["user", "assistant"]
    assert agent.session.messages[-1].text == "Hello from runtime"


async def test_runtime_agent_accepts_text_only_block_content(mock_provider) -> None:
    provider = mock_provider(
        [
            Message(
                role="assistant",
                content=[TextBlock(text="Hello"), TextBlock(text=" from runtime")],
            )
        ]
    )
    agent = Agent(provider=provider)

    result = await agent.run("Hi")

    assert result.output_text == "Hello\n from runtime"
    assert agent.session.messages[-1].role == "assistant"
    assert agent.session.messages[-1].text == "Hello\n from runtime"


async def test_runtime_agent_stream_emits_bootstrap_events(mock_provider) -> None:
    provider = mock_provider([Message(role="assistant", content="Hello from runtime")])
    agent = Agent(provider=provider)

    events = [event async for event in agent.stream("Hi")]

    assert [event.type for event in events] == [
        "run_started",
        "message_started",
        "message_delta",
        "message_completed",
        "run_completed",
    ]
    assert events[2].delta == "Hello from runtime"
    assert events[3].output_text == "Hello from runtime"
    assert events[4].result == AgentResult(
        run_id=events[0].run_id,
        final_message_id=events[1].message_id,
        output_text="Hello from runtime",
    )


async def test_runtime_agent_stream_emits_run_failed_for_provider_exception(
    mock_provider,
) -> None:
    agent = Agent(provider=mock_provider())

    async def broken_chat(*args, **kwargs):
        raise ValueError("provider boom")

    agent.provider.chat = broken_chat

    events = [event async for event in agent.stream("Hi")]

    assert [event.type for event in events] == [
        "run_started",
        "message_started",
        "run_failed",
    ]
    assert events[-1] == RunFailed(run_id=events[0].run_id, error="provider boom")


async def test_runtime_agent_stream_emits_run_cancelled_for_provider_cancellation(
    mock_provider,
) -> None:
    agent = Agent(provider=mock_provider())

    async def cancelled_chat(*args, **kwargs):
        raise asyncio.CancelledError()

    agent.provider.chat = cancelled_chat

    events = [event async for event in agent.stream("Hi")]

    assert [event.type for event in events] == [
        "run_started",
        "message_started",
        "run_cancelled",
    ]
    assert events[-1] == RunCancelled(
        run_id=events[0].run_id,
        reason="Run cancelled",
    )


async def test_runtime_agent_run_uses_stream_event_flow(mock_provider) -> None:
    agent = Agent(provider=mock_provider())
    expected = AgentResult(
        run_id="run_123",
        final_message_id="msg_456",
        output_text="Hello from runtime",
    )

    async def fake_stream(user_input: str, **kwargs):
        assert user_input == "Hi"
        assert kwargs == {"temperature": 0}
        yield RunStarted(run_id="run_123")
        yield RunCompleted(
            run_id="run_123",
            final_message_id="msg_456",
            result=expected,
        )

    agent.stream = fake_stream

    result = await agent.run("Hi", temperature=0)

    assert result == expected


@pytest.mark.parametrize(
    ("terminal_event", "expected_message"),
    [
        (RunFailed(run_id="run_123", error="provider boom"), "provider boom"),
        (RunCancelled(run_id="run_123", reason="user cancelled"), "user cancelled"),
    ],
)
async def test_runtime_agent_run_surfaces_terminal_failure_events(
    mock_provider,
    terminal_event,
    expected_message: str,
) -> None:
    agent = Agent(provider=mock_provider())

    async def fake_stream(user_input: str, **kwargs):
        assert user_input == "Hi"
        assert kwargs == {}
        yield RunStarted(run_id="run_123")
        yield terminal_event

    agent.stream = fake_stream

    with pytest.raises(RuntimeError, match=expected_message):
        await agent.run("Hi")


@pytest.mark.parametrize(
    ("provider_response", "expected_message"),
    [
        (Message(role="user", content="Nope"), "assistant text message"),
        (
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(name="search", arguments={}),
                    TextBlock(text="hi"),
                ],
            ),
            "assistant text message",
        ),
        (
            Message(
                role="assistant",
                content=[ToolResultBlock(tool_use_id="call_123", content="done")],
            ),
            "assistant text message",
        ),
    ],
)
async def test_runtime_agent_rejects_unsupported_provider_outputs(
    mock_provider,
    provider_response: Message,
    expected_message: str,
) -> None:
    agent = Agent(provider=mock_provider([provider_response]))

    with pytest.raises(ValueError, match=expected_message):
        await agent.run("Hi")
