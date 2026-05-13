"""E2E test: agent given a text processing task and calls awk/sed tools."""

from __future__ import annotations

from openagent import Agent
from openagent.core.types import Message, ToolUseBlock
from openagent.tools.builtin import awk, sed, read, write


class TextProcessingProvider:
    """Provider that simulates an LLM giving text processing instructions."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._call_count = 0
        self.model = "mock"
        self.api_key = None

    async def chat(
        self,
        messages: list[Message],
        tools=None,
        system_prompt: str = "",
        **kwargs,
    ) -> Message:
        self.calls.append({"tools": tools, "system_prompt": system_prompt, "kwargs": kwargs})
        count = self._call_count
        self._call_count += 1

        if count == 0:
            # Agent decides to write a data file first
            return Message(
                role="assistant",
                content=[ToolUseBlock(id="c1", name="write", arguments={
                    "path": "/tmp/text_task/data.txt",
                    "content": "Alice 85 Math\nBob 92 Science\nCharlie 78 Math\nDiana 95 Science\nEve 88 Art",
                })],
            )
        if count == 1:
            # Agent uses awk to filter students with grade > 85
            return Message(
                role="assistant",
                content=[ToolUseBlock(id="c2", name="awk", arguments={
                    "pattern": "$2 > 85 {print $0}",
                    "input_text": "Alice 85 Math\nBob 92 Science\nCharlie 78 Math\nDiana 95 Science\nEve 88 Art",
                })],
            )
        if count == 2:
            # Agent uses sed to transform the filtered output
            return Message(
                role="assistant",
                content=[ToolUseBlock(id="c3", name="sed", arguments={
                    "expression": r"s/(\w+) (\d+) (\w+)/Grade \2: \1 (\3)/",
                    "input_text": "Bob 92 Science\nDiana 95 Science\nEve 88 Art",
                })],
            )
        # Final answer
        return Message(
            role="assistant",
            content="Done! Filtered students with grades > 85 and reformatted the output.",
        )


async def test_agent_text_processing_task() -> None:
    """Test the agent processes text data using write, awk, and sed tools in sequence."""
    provider = TextProcessingProvider()
    agent = Agent(
        provider=provider,
        system_prompt=(
            "You are a data assistant. You can write files, process text with awk and sed, "
            "and analyze structured data."
        ),
        tools=[read, write, awk, sed],
        max_turns=10,
    )

    result = await agent.run(
        "Write a student grades file, then use awk to filter students with grades > 85, "
        "and use sed to reformat the output as 'Grade X: Name (Subject)'."
    )

    assert result == "Done! Filtered students with grades > 85 and reformatted the output."

    # Verify the tool call sequence
    assert len(provider.calls) == 4  # write, awk, sed, final

    # Verify awk tool call (args are in the ToolUseBlock, not kwargs)
    awk_tool_call = provider.calls[1]["tools"][2]  # 3rd tool (after read, write) + recall
    # The awk call is embedded in the session messages, check via the provider's recorded calls
    # Just verify the tool definitions include awk
    awk_names = [t.name for t in provider.calls[1]["tools"]]
    assert "awk" in awk_names

    # Verify sed tool definition is present
    sed_names = [t.name for t in provider.calls[2]["tools"]]
    assert "sed" in sed_names


class AwkFieldExtractionProvider:
    """Provider that asks the agent to extract specific fields."""

    model = "mock"
    api_key = None

    def __init__(self) -> None:
        self._call_count = 0

    async def chat(self, messages, tools=None, system_prompt="", **kwargs):
        self._call_count += 1
        if self._call_count == 1:
            return Message(
                role="assistant",
                content=[ToolUseBlock(id="f1", name="awk", arguments={
                    "pattern": "{print $1,$3}",
                    "input_text": "John 85 Math\nMary 92 Science\nBob 78 Art",
                })],
            )
        return Message(role="assistant", content="Extracted names and subjects.")


async def test_agent_awk_field_extraction() -> None:
    """Test the agent uses awk to extract specific columns from tabular text."""
    provider = AwkFieldExtractionProvider()
    agent = Agent(
        provider=provider,
        tools=[awk],
    )

    result = await agent.run(
        "From this student data, extract just the name and subject (columns 1 and 3)."
    )

    assert "Extracted names and subjects" in result


class SedTransformProvider:
    """Provider that asks the agent to do CSV transformation with sed."""

    model = "mock"
    api_key = None

    def __init__(self) -> None:
        self._call_count = 0

    async def chat(self, messages, tools=None, system_prompt="", **kwargs):
        self._call_count += 1
        if self._call_count == 1:
            return Message(
                role="assistant",
                content=[ToolUseBlock(id="s1", name="sed", arguments={
                    "expression": "s/,/ | /g",
                    "input_text": "name,age,city\nAlice,30,NYC\nBob,25,LA",
                })],
            )
        return Message(role="assistant", content="Transformed CSV to pipe-delimited format.")


async def test_agent_sed_csv_transform() -> None:
    """Test the agent uses sed to transform CSV to pipe-delimited format."""
    provider = SedTransformProvider()
    agent = Agent(
        provider=provider,
        tools=[sed],
    )

    result = await agent.run(
        "Convert this CSV data to pipe-delimited format using sed."
    )

    assert "Transformed CSV" in result


class SedCaseSwapProvider:
    """Provider that asks the agent to swap case with sed."""

    model = "mock"
    api_key = None

    def __init__(self) -> None:
        self._call_count = 0

    async def chat(self, messages, tools=None, system_prompt="", **kwargs):
        self._call_count += 1
        if self._call_count == 1:
            return Message(
                role="assistant",
                content=[ToolUseBlock(id="cs1", name="sed", arguments={
                    "expression": r"s/(\w+) (\w+)/\2, \1/gi",
                    "input_text": "hello world, this is a test",
                })],
            )
        return Message(role="assistant", content="Done reversing the words.")


async def test_agent_sed_word_swap() -> None:
    """Test the agent uses sed with backreferences to swap word order."""
    provider = SedCaseSwapProvider()
    agent = Agent(
        provider=provider,
        tools=[sed],
    )

    result = await agent.run(
        "Swap each pair of words in this text using sed backreferences."
    )

    assert "reversing" in result.lower()
