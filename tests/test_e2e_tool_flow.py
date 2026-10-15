"""End-to-end tests for the full agent loop with tool execution."""

from __future__ import annotations

from pathlib import Path

from openagent import Agent, tool
from openagent.core.types import Message, ToolUseBlock
from openagent.tools.builtin import read, write, edit, glob, grep


async def test_agent_write_read_edit_loop(tmp_path: Path) -> None:
    """Test the full agent loop using tmp_path in provider tool calls."""
    target_dir = tmp_path / "e2e_test2"
    target_dir.mkdir()
    target_file = target_dir / "hello.txt"

    class TmpPathProvider:
        model = "mock"
        api_key = None
        calls: list[dict] = []

        def __init__(self) -> None:
            self._call_count = 0

        async def chat(self, messages, tools=None, system_prompt="", **kwargs):
            self.calls.append({"tools": tools, "kwargs": kwargs})
            count = self._call_count
            self._call_count += 1
            path = str(target_file)
            if count == 0:
                return Message(
                    role="assistant",
                    content=[ToolUseBlock(id="c1", name="write", arguments={
                        "path": path,
                        "content": "Hello from agent!\nLine two",
                    })],
                )
            if count == 1:
                return Message(
                    role="assistant",
                    content=[ToolUseBlock(id="c2", name="read", arguments={"path": path})],
                )
            if count == 2:
                return Message(
                    role="assistant",
                    content=[ToolUseBlock(id="c3", name="edit", arguments={
                        "path": path,
                        "find": "Line two",
                        "replace": "Line two (edited)",
                    })],
                )
            if count == 3:
                return Message(
                    role="assistant",
                    content=[ToolUseBlock(id="c4", name="read", arguments={"path": path})],
                )
            return Message(role="assistant", content="Done! The file now contains the edited content.")

    provider = TmpPathProvider()
    agent = Agent(
        provider=provider,
        system_prompt="You are a helpful assistant.",
        tools=[read, write, edit],
        max_turns=10,
        
    )

    result = await agent.run("Write a file, read it, edit it, and read again.")

    assert result == "Done! The file now contains the edited content."
    assert target_file.exists()
    content = target_file.read_text()
    assert "Hello from agent!" in content
    assert "Line two (edited)" in content
    assert len(provider.calls) == 5


async def test_agent_parallel_tool_execution_with_real_tools(tmp_path: Path) -> None:
    """Test that multiple tool calls execute in parallel with real tools."""
    target_dir = tmp_path / "parallel_test"
    target_dir.mkdir()

    call_order: list[str] = []

    @tool
    def track_file(name: str) -> str:
        call_order.append(name)
        (target_dir / name).write_text(f"content of {name}")
        return f"created {name}"

    class ParallelProvider:
        def __init__(self) -> None:
            self._call_count = 0
            self.model = "mock"
            self.api_key = None

        async def chat(self, messages, tools=None, system_prompt="", **kwargs):
            if self._call_count == 0:
                self._call_count += 1
                return Message(
                    role="assistant",
                    content=[
                        ToolUseBlock(id="a", name="track_file", arguments={"name": "a.txt"}),
                        ToolUseBlock(id="b", name="track_file", arguments={"name": "b.txt"}),
                        ToolUseBlock(id="c", name="track_file", arguments={"name": "c.txt"}),
                    ],
                )
            return Message(role="assistant", content="All files created.")

    provider = ParallelProvider()
    agent = Agent(
        provider=provider,
        tools=[track_file],
        
    )

    await agent.run("Create files a, b, c in parallel")

    assert len(call_order) == 3
    assert set(call_order) == {"a.txt", "b.txt", "c.txt"}
    assert (target_dir / "a.txt").read_text() == "content of a.txt"


async def test_agent_grep_glob_flow(tmp_path: Path) -> None:
    """Test agent using glob to find files then grep to search them."""
    target_dir = tmp_path / "grep_glob_test"
    target_dir.mkdir()
    (target_dir / "app.py").write_text("print('hello world')\n")
    (target_dir / "test_app.py").write_text("assert True\n")

    call_count = 0

    class GrepGlobProvider:
        model = "mock"
        api_key = None

        async def chat(self, messages, tools=None, system_prompt="", **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return Message(
                    role="assistant",
                    content=[ToolUseBlock(id="g1", name="glob", arguments={
                        "pattern": "*.py",
                        "path": str(target_dir),
                    })],
                )
            if call_count == 2:
                return Message(
                    role="assistant",
                    content=[ToolUseBlock(id="g2", name="grep", arguments={
                        "pattern": "hello",
                        "path": str(target_dir),
                    })],
                )
            return Message(role="assistant", content="Found hello in app.py!")

    provider = GrepGlobProvider()
    agent = Agent(
        provider=provider,
        tools=[glob, grep],
        
    )

    result = await agent.run("Find py files and search for hello")
    assert result == "Found hello in app.py!"
    assert call_count == 3  # glob, grep, final answer


async def test_agent_tool_error_handling() -> None:
    """Test that the agent handles tool errors gracefully."""
    error_count = 0

    class ErrorProvider:
        model = "mock"
        api_key = None

        async def chat(self, messages, tools=None, system_prompt="", **kwargs):
            nonlocal error_count
            error_count += 1
            if error_count == 1:
                return Message(
                    role="assistant",
                    content=[ToolUseBlock(id="e1", name="read", arguments={
                        "path": "/nonexistent/file/that/does/not/exist.txt",
                    })],
                )
            return Message(role="assistant", content="Got the error, continuing.")

    provider = ErrorProvider()
    agent = Agent(
        provider=provider,
        tools=[read],
        
    )

    result = await agent.run("Read a nonexistent file")
    assert "continuing" in result.lower()


async def test_agent_tool_arguments_passed_correctly() -> None:
    """Test that tool arguments from the LLM are passed correctly to the tool."""
    received_args: dict = {}

    @tool
    def capture_args(name: str, count: int = 1) -> str:
        received_args["name"] = name
        received_args["count"] = count
        return f"name={name}, count={count}"

    class ArgProvider:
        model = "mock"
        api_key = None

        def __init__(self) -> None:
            self._call_count = 0

        async def chat(self, messages, tools=None, system_prompt="", **kwargs):
            self._call_count += 1
            if self._call_count == 1:
                return Message(
                    role="assistant",
                    content=[ToolUseBlock(id="a1", name="capture_args", arguments={
                        "name": "test_item",
                        "count": 42,
                    })],
                )
            return Message(role="assistant", content="Captured!")

    provider = ArgProvider()
    agent = Agent(
        provider=provider,
        tools=[capture_args],
        
    )

    await agent.run("Capture args")
    assert received_args == {"name": "test_item", "count": 42}


async def test_agent_multi_turn_no_tools() -> None:
    """Test agent multi-turn without tools (recall auto-add was removed)."""
    call_count = 0

    class NoToolProvider:
        model = "mock"
        api_key = None

        async def chat(self, messages, tools=None, system_prompt="", **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return Message(role="assistant", content="Nothing to recall yet.")
            return Message(role="assistant", content="All done.")

    provider = NoToolProvider()
    agent = Agent(provider=provider, tools=[])

    result = await agent.run("Test no tools")
    assert "Nothing to recall" in result
