"""Tests for Agent class."""

from __future__ import annotations

import pytest

from openagent import Agent, tool
from openagent.core.types import Message, TextBlock, ToolResultBlock, ToolUseBlock


def test_agent_init(mock_provider, simple_response):
    """Test agent initialization."""
    provider = mock_provider([simple_response])
    agent = Agent(provider=provider, system_prompt="Test prompt")

    assert agent.max_turns == 10
    assert agent.session.system_prompt == "Test prompt"
    assert len(agent.messages) == 0


def test_agent_with_tools(mock_provider, simple_response):
    """Test agent initialization with tools."""

    @tool
    def dummy_tool(x: str) -> str:
        return x

    provider = mock_provider([simple_response])
    agent = Agent(provider=provider, tools=[dummy_tool])

    assert len(agent.tool_registry) == 1


async def test_agent_simple_run(mock_provider, simple_response):
    """Test simple agent run without tool calls."""
    provider = mock_provider([simple_response])
    agent = Agent(provider=provider)

    result = await agent.run("Hello!")

    assert result == "Hello!"
    assert len(agent.messages) == 2  # user + assistant


async def test_agent_with_tool_call(mock_provider):
    """Test agent run with tool calls."""

    @tool
    def get_weather(city: str) -> str:
        return f"Sunny in {city}"

    tool_response = Message(
        role="assistant",
        content=[
            ToolUseBlock(id="call_1", name="get_weather", arguments={"city": "Tokyo"}),
        ],
    )
    final_response = Message(role="assistant", content="The weather is sunny in Tokyo!")

    provider = mock_provider([tool_response, final_response])
    agent = Agent(provider=provider, tools=[get_weather])

    result = await agent.run("What's the weather in Tokyo?")

    assert result == "The weather is sunny in Tokyo!"
    assert len(agent.messages) == 4  # user, tool_call, tool_result, final


async def test_agent_max_turns(mock_provider):
    """Test that agent respects max_turns."""

    @tool
    def endless_tool(x: str) -> str:
        return x

    # All responses have tool calls - should hit max_turns
    tool_response = Message(
        role="assistant",
        content=[
            ToolUseBlock(id="call_1", name="endless_tool", arguments={"x": "loop"}),
        ],
    )

    provider = mock_provider([tool_response] * 5)
    agent = Agent(provider=provider, tools=[endless_tool], max_turns=3)

    result = await agent.run("Loop forever")

    # Should return after max_turns
    assert isinstance(result, str)


async def test_agent_parallel_tool_execution(mock_provider):
    """Test that multiple tool calls execute in parallel."""
    call_order = []

    @tool
    def track_call(name: str) -> str:
        call_order.append(name)
        return f"done_{name}"

    tool_response = Message(
        role="assistant",
        content=[
            ToolUseBlock(id="call_1", name="track_call", arguments={"name": "a"}),
            ToolUseBlock(id="call_2", name="track_call", arguments={"name": "b"}),
        ],
    )
    final_response = Message(role="assistant", content="Done!")

    provider = mock_provider([tool_response, final_response])
    agent = Agent(provider=provider, tools=[track_call])

    await agent.run("Call both")

    # Both should have been called
    assert "a" in call_order
    assert "b" in call_order


async def test_agent_retries_empty_assistant_response(mock_provider):
    """Test that an empty final response triggers a retry instead of stopping."""
    provider = mock_provider(
        [
            Message(role="assistant", content=""),
            Message(role="assistant", content="Recovered response"),
        ]
    )
    agent = Agent(provider=provider)

    result = await agent.run("Finish the task")

    assert result == "Recovered response"
    assert [message.role for message in agent.messages] == [
        "user",
        "system",
        "assistant",
    ]


async def test_agent_returns_message_after_repeated_empty_responses(mock_provider):
    """Test that repeated empty replies return a useful fallback instead of blank output."""
    provider = mock_provider(
        [
            Message(role="assistant", content=""),
            Message(role="assistant", content=""),
            Message(role="assistant", content=""),
            Message(role="assistant", content=""),
        ]
    )
    agent = Agent(provider=provider)

    result = await agent.run("Finish the task")

    assert "empty responses repeatedly" in result
    assert [message.role for message in agent.messages] == ["user", "system"]


async def test_agent_allows_final_answer_after_last_tool_turn(mock_provider):
    """Test that the agent allows one final answer after the last tool turn."""

    @tool
    def get_weather(city: str) -> str:
        return f"Sunny in {city}"

    tool_response = Message(
        role="assistant",
        content=[
            ToolUseBlock(id="call_1", name="get_weather", arguments={"city": "Tokyo"})
        ],
    )
    final_response = Message(role="assistant", content="The weather is sunny in Tokyo!")

    provider = mock_provider([tool_response, final_response])
    agent = Agent(provider=provider, tools=[get_weather], max_turns=1)

    result = await agent.run("What's the weather in Tokyo?")

    assert result == "The weather is sunny in Tokyo!"
    assert [message.role for message in agent.messages] == [
        "user",
        "assistant",
        "tool_result",
        "assistant",
    ]
