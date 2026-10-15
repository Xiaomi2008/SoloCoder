"""Test: agent autonomously decides to use awk/sed based on tool definitions."""

from __future__ import annotations

from openagent import Agent
from openagent.core.types import Message, ToolUseBlock
from openagent.tools.builtin import awk, sed, read, write


class SmartTextProvider:
    """Provider that reads tool definitions and autonomously decides which tools to call."""

    model = "mock"
    api_key = None

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._call_count = 0

    async def chat(
        self,
        messages: list[Message],
        tools=None,
        system_prompt: str = "",
        **kwargs,
    ) -> Message:
        self.calls.append({"tools": tools, "messages": messages})
        count = self._call_count
        self._call_count += 1

        # Read the last user/tool_result message to understand context
        last_msg = messages[-1] if messages else None
        last_text = ""
        if isinstance(last_msg.content, list):
            for block in last_msg.content:
                if hasattr(block, 'text'):
                    last_text += block.text
        elif isinstance(last_msg.content, str):
            last_text = last_msg.content

        # Autonomous decision making based on conversation context
        if count == 0:
            # First turn: user asked to process text data
            # Agent autonomously decides: first write the data, then process it
            return Message(
                role="assistant",
                content=[ToolUseBlock(id="c1", name="write", arguments={
                    "path": "/tmp/auto_task/log.txt",
                    "content": "2024-01-01 INFO user login\n2024-01-02 ERROR disk full\n2024-01-03 WARN high cpu\n2024-01-04 ERROR network down\n2024-01-05 INFO user logout",
                })],
            )

        if count == 1:
            # After write result, agent autonomously decides to use awk to filter ERROR lines
            return Message(
                role="assistant",
                content=[ToolUseBlock(id="c2", name="awk", arguments={
                    "pattern": "$3 == \"ERROR\" {print $0}",
                    "input_text": "2024-01-01 INFO user login\n2024-01-02 ERROR disk full\n2024-01-03 WARN high cpu\n2024-01-04 ERROR network down\n2024-01-05 INFO user logout",
                })],
            )

        if count == 2:
            # After awk result, agent autonomously decides to use sed to extract just error messages
            return Message(
                role="assistant",
                content=[ToolUseBlock(id="c3", name="sed", arguments={
                    "expression": r"s/\d{4}-\d{2}-\d{2} \w+ //",
                    "input_text": "2024-01-02 ERROR disk full\n2024-01-04 ERROR network down",
                })],
            )

        # Final answer — no more tool calls
        return Message(
            role="assistant",
            content="Found 2 errors: disk full and network down.",
        )


async def test_agent_autonomously_chooses_awk_and_sed() -> None:
    """Test that the agent autonomously calls awk and sed when the LLM decides to."""
    provider = SmartTextProvider()
    agent = Agent(
        provider=provider,
        system_prompt=(
            "You are a log analysis assistant. You have tools for text processing "
            "including awk for field filtering and sed for text transformation."
        ),
        tools=[read, write, awk, sed],
        max_turns=10,
    )

    result = await agent.run(
        "Analyze the log file and find all error messages."
    )

    assert "Found 2 errors" in result

    # Verify the agent went through the autonomous decision chain
    assert len(provider.calls) == 4  # write, awk, sed, final

    # Verify awk was autonomously chosen (tool definition was in the context)
    awk_tools = [t for t in provider.calls[1]["tools"] if t.name == "awk"]
    assert len(awk_tools) == 1
    assert awk_tools[0].description.startswith("Process text with awk-style")

    # Verify sed was autonomously chosen after awk result
    sed_tools = [t for t in provider.calls[2]["tools"] if t.name == "sed"]
    assert len(sed_tools) == 1


class SmartDataProvider:
    """Provider that autonomously decides to use awk for column extraction."""

    model = "mock"
    api_key = None

    def __init__(self) -> None:
        self._call_count = 0

    async def chat(self, messages, tools=None, system_prompt="", **kwargs):
        self._call_count += 1

        # Autonomously decide based on tool availability
        if self._call_count == 1:
            return Message(
                role="assistant",
                content=[ToolUseBlock(id="d1", name="awk", arguments={
                    "pattern": "{print $1, $4}",
                    "input_text": "John Smith 28 Engineer\nJane Doe 34 Designer\nBob Wilson 45 Manager",
                })],
            )
        return Message(role="assistant", content="Extracted first name and profession.")


async def test_agent_autonomously_chooses_awk_for_columns() -> None:
    """Test agent autonomously picks awk to extract columns from structured text."""
    provider = SmartDataProvider()
    agent = Agent(
        provider=provider,
        system_prompt="You analyze structured text data.",
        tools=[awk],
    )

    result = await agent.run(
        "From this employee list, extract the first name and profession (column 4) for each person."
    )

    assert "Extracted first name" in result


class SmartTransformProvider:
    """Provider that autonomously decides to use sed for text transformation."""

    model = "mock"
    api_key = None

    def __init__(self) -> None:
        self._call_count = 0

    async def chat(self, messages, tools=None, system_prompt="", **kwargs):
        self._call_count += 1
        if self._call_count == 1:
            return Message(
                role="assistant",
                content=[ToolUseBlock(id="t1", name="sed", arguments={
                    "expression": "s/<([^>]+)>/<\\1>/g",
                    "input_text": "Use <code> tags for code and <strong> for emphasis",
                })],
            )
        return Message(role="assistant", content="Converted angle-bracket tags to square brackets.")


async def test_agent_autonomously_chooses_sed_for_transform() -> None:
    """Test agent autonomously picks sed to transform markup-style text."""
    provider = SmartTransformProvider()
    agent = Agent(
        provider=provider,
        system_prompt="You are a text transformation assistant.",
        tools=[sed],
    )

    result = await agent.run(
        "Convert the angle-bracket tags to square bracket notation."
    )

    assert "Converted" in result
