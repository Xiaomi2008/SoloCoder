"""Tests for Agent class."""

from __future__ import annotations

import pytest

from openagent import Agent, tool
from openagent.core import agent as core_agent_module
from openagent.core.session import Session
from openagent.core.types import (
    ImageBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from openagent.provider.openai import OpenAIConverterMixin
from openagent.runtime import AgentResult


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


async def test_agent_adds_screenshot_tool_result_as_multimodal_user_message() -> None:
    screenshot_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

    class CapturingProvider:
        def __init__(self) -> None:
            self.model = "mock-model"
            self.api_key = None
            self.calls: list[list[Message]] = []
            self._call_count = 0

        async def chat(self, messages, tools=None, system_prompt="", **kwargs):
            self.calls.append({"messages": list(messages), "kwargs": dict(kwargs)})
            if self._call_count == 0:
                self._call_count += 1
                return Message(
                    role="assistant",
                    content=[
                        ToolUseBlock(
                            id="call_screenshot",
                            name="screenshot",
                            arguments={"return_base64": True},
                        )
                    ],
                )
            return Message(role="assistant", content="done")

    @tool
    def screenshot(return_base64: bool = False) -> str:
        assert return_base64 is True
        return screenshot_base64

    provider = CapturingProvider()
    agent = Agent(provider=provider, tools=[screenshot])

    result = await agent.run("Look at the screen")

    assert result == "done"
    assert len(provider.calls) == 2
    second_call_messages = provider.calls[1]["messages"]
    image_messages = [
        msg for msg in second_call_messages if msg.role == "user" and msg.has_images
    ]
    assert len(image_messages) == 1
    image_blocks = [
        block for block in image_messages[0].content if isinstance(block, ImageBlock)
    ]
    text_blocks = [
        block for block in image_messages[0].content if isinstance(block, TextBlock)
    ]
    assert len(image_blocks) == 1
    assert len(text_blocks) == 1
    assert image_blocks[0].mime_type == "image/jpeg"
    assert image_blocks[0].data == screenshot_base64
    assert "Use screenshot image coordinates only" in text_blocks[0].text
    assert "valid image coordinate range" in text_blocks[0].text
    assert provider.calls[1]["kwargs"]["max_tokens"] == 2048


def test_openai_converter_preserves_png_image_mime_type() -> None:
    session = Session(system_prompt="You are helpful.")
    session.add_user_multimodal(
        text="Describe this screenshot",
        image_data="ZmFrZQ==",
    )

    converted = OpenAIConverterMixin().convert_messages(session.messages)
    user_message = converted["messages"][-1]
    image_blocks = [
        block for block in user_message["content"] if block["type"] == "image_url"
    ]

    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_openai_converter_preserves_explicit_jpeg_image_mime_type() -> None:
    session = Session(system_prompt="You are helpful.")
    session.add_user_multimodal(
        text="Describe this screenshot",
        image_data="ZmFrZQ==",
        image_mime_type="image/jpeg",
    )

    converted = OpenAIConverterMixin().convert_messages(session.messages)
    user_message = converted["messages"][-1]
    image_blocks = [
        block for block in user_message["content"] if block["type"] == "image_url"
    ]

    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_agent_simple_run_bridges_to_runtime_agent(mock_provider, monkeypatch):
    """Test simple runs delegate through the bootstrap runtime agent."""

    calls = {}

    class FakeRuntimeAgent:
        def __init__(self, provider, system_prompt=""):
            calls["provider"] = provider
            calls["system_prompt"] = system_prompt
            self.session = Session(system_prompt=system_prompt)

        async def run(self, user_input: str, **kwargs):
            calls["user_input"] = user_input
            calls["kwargs"] = kwargs
            self.session.add("user", user_input)
            self.session.add("assistant", "Hello from runtime")
            return AgentResult(
                run_id="run_123",
                final_message_id="msg_123",
                output_text="Hello from runtime",
            )

    monkeypatch.setattr(
        core_agent_module, "RuntimeAgent", FakeRuntimeAgent, raising=False
    )

    provider = mock_provider()
    agent = Agent(provider=provider, system_prompt="Test prompt")

    result = await agent.run("Hello!", temperature=0)

    assert result == "Hello from runtime"
    assert calls == {
        "provider": provider,
        "system_prompt": "Test prompt",
        "user_input": "Hello!",
        "kwargs": {"temperature": 0},
    }
    assert [message.role for message in agent.messages] == ["user", "assistant"]


async def test_agent_simple_run_filters_core_only_kwargs_before_provider_call() -> None:
    """Test no-tool runtime bridge strips core-only kwargs before provider.chat."""

    class RecordingProvider:
        def __init__(self) -> None:
            self.kwargs = None

        async def chat(self, messages, tools=None, system_prompt="", **kwargs):
            self.kwargs = kwargs
            return Message(role="assistant", content="Hello!")

    provider = RecordingProvider()
    agent = Agent(provider=provider)

    result = await agent.run(
        "Hello!",
        temperature=0,
        max_context_tokens=123,
        compact_threshold=0.5,
        disable_compaction=True,
    )

    assert result == "Hello!"
    assert provider.kwargs == {"temperature": 0}


async def test_agent_simple_run_compacts_context_before_runtime_provider_call(
    monkeypatch,
) -> None:
    """Test no-tool runtime bridge preserves legacy session compaction behavior."""

    events = []

    class RecordingProvider:
        async def chat(self, messages, tools=None, system_prompt="", **kwargs):
            events.append(("provider_chat", kwargs))
            return Message(role="assistant", content="Hello!")

    provider = RecordingProvider()
    agent = Agent(provider=provider)

    def fake_check_compaction_needed(*, max_tokens: int, threshold: float) -> bool:
        events.append(("check", max_tokens, threshold))
        return True

    async def fake_compact_context(
        *, provider, keep_recent: int, summary_type: str
    ) -> str:
        events.append(("compact", provider, keep_recent, summary_type))
        return "summary"

    monkeypatch.setattr(
        agent.session, "check_compaction_needed", fake_check_compaction_needed
    )
    monkeypatch.setattr(agent.session, "compact_context", fake_compact_context)

    result = await agent.run("Hello!", max_context_tokens=123, compact_threshold=0.5)

    assert result == "Hello!"
    assert events == [
        ("check", 123, 0.5),
        ("compact", provider, 5, "detailed"),
        ("provider_chat", {}),
    ]


async def test_agent_simple_run_disable_compaction_bypasses_runtime_compaction(
    monkeypatch,
) -> None:
    """Test no-tool runtime bridge skips compaction when disabled."""

    events = []

    class RecordingProvider:
        async def chat(self, messages, tools=None, system_prompt="", **kwargs):
            events.append(("provider_chat", kwargs))
            return Message(role="assistant", content="Hello!")

    provider = RecordingProvider()
    agent = Agent(provider=provider)

    def fake_check_compaction_needed(*, max_tokens: int, threshold: float) -> bool:
        events.append(("check", max_tokens, threshold))
        return True

    async def fake_compact_context(
        *, provider, keep_recent: int, summary_type: str
    ) -> str:
        events.append(("compact", provider, keep_recent, summary_type))
        return "summary"

    monkeypatch.setattr(
        agent.session, "check_compaction_needed", fake_check_compaction_needed
    )
    monkeypatch.setattr(agent.session, "compact_context", fake_compact_context)

    result = await agent.run(
        "Hello!",
        max_context_tokens=123,
        compact_threshold=0.5,
        disable_compaction=True,
    )

    assert result == "Hello!"
    assert events == [("provider_chat", {})]


async def test_agent_simple_run_preserves_history_across_no_tool_runs() -> None:
    """Test consecutive no-tool runs keep session history in provider messages."""

    class RecordingProvider:
        def __init__(self) -> None:
            self.calls = []
            self.responses = iter(
                [
                    Message(role="assistant", content="Hello!"),
                    Message(role="assistant", content="I remember."),
                ]
            )

        async def chat(self, messages, tools=None, system_prompt="", **kwargs):
            self.calls.append(
                {
                    "messages": list(messages),
                    "tools": tools,
                    "system_prompt": system_prompt,
                    "kwargs": kwargs,
                }
            )
            return next(self.responses)

    provider = RecordingProvider()
    agent = Agent(provider=provider, system_prompt="Test prompt")

    first_result = await agent.run("Hello!")
    second_result = await agent.run("What did I just say?")

    assert first_result == "Hello!"
    assert second_result == "I remember."
    assert [message.role for message in provider.calls[0]["messages"]] == ["user"]
    assert [message.content for message in provider.calls[0]["messages"]] == ["Hello!"]
    assert [message.role for message in provider.calls[1]["messages"]] == [
        "user",
        "assistant",
        "user",
    ]
    assert [message.content for message in provider.calls[1]["messages"]] == [
        "Hello!",
        "Hello!",
        "What did I just say?",
    ]


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
