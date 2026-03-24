from __future__ import annotations

from openagent.core.tool import ToolEntry, ToolRegistry, tool
from openagent.core.types import ToolResultBlock, ToolUseBlock
from openagent.runtime.context import RuntimeContext
from openagent.runtime.tool_executor import ToolExecutor


def test_runtime_context_carries_bootstrap_data() -> None:
    context = RuntimeContext(project_name="bootstrap-project")

    assert context.project_name == "bootstrap-project"


async def test_tool_executor_executes_registered_tool_call() -> None:
    registry = ToolRegistry()

    @tool
    def greet(name: str) -> str:
        return f"hello {name}"

    registry.register(greet)
    executor = ToolExecutor(registry)

    result = await executor.execute(
        ToolUseBlock(id="call_123", name="greet", arguments={"name": "Ada"}),
        RuntimeContext(project_name="bootstrap-project"),
    )

    assert result == ToolResultBlock(
        tool_use_id="call_123",
        tool_name="greet",
        content="hello Ada",
    )


async def test_tool_executor_passes_explicit_runtime_context() -> None:
    registry = ToolRegistry()

    @tool
    def describe_project(context: RuntimeContext, suffix: str) -> str:
        return f"{context.project_name}{suffix}"

    registry.register(describe_project)
    executor = ToolExecutor(registry)

    result = await executor.execute(
        ToolUseBlock(
            id="call_456",
            name="describe_project",
            arguments={"suffix": " runtime"},
        ),
        RuntimeContext(project_name="bootstrap-project"),
    )

    assert result == ToolResultBlock(
        tool_use_id="call_456",
        content="bootstrap-project runtime",
        tool_name="describe_project",
    )


async def test_tool_executor_injects_context_by_name_only() -> None:
    registry = ToolRegistry()

    @tool
    def describe_project(ctx: RuntimeContext, suffix: str) -> str:
        return f"{ctx.project_name}{suffix}"

    registry.register(describe_project)
    executor = ToolExecutor(registry)

    result = await executor.execute(
        ToolUseBlock(
            id="call_789",
            name="describe_project",
            arguments={"suffix": " runtime"},
        ),
        RuntimeContext(project_name="bootstrap-project"),
    )

    assert result.is_error is True
    assert "ctx" in result.content


async def test_tool_executor_uses_registry_public_resolution_api() -> None:
    @tool
    def greet(name: str) -> str:
        return f"hello {name}"

    registry_entry = ToolEntry(
        name="greet",
        description="",
        parameters={"type": "object", "properties": {"name": {"type": "string"}}},
        func=greet,
    )

    class ResolveOnlyRegistry:
        def resolve(self, name: str) -> ToolEntry | None:
            if name == "greet":
                return registry_entry
            return None

    executor = ToolExecutor(ResolveOnlyRegistry())

    result = await executor.execute(
        ToolUseBlock(id="call_999", name="greet", arguments={"name": "Ada"}),
        RuntimeContext(project_name="bootstrap-project"),
    )

    assert result == ToolResultBlock(
        tool_use_id="call_999",
        content="hello Ada",
        tool_name="greet",
    )
