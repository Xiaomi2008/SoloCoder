"""Tests for tool decorator and registry."""

from __future__ import annotations

import pytest

from openagent import tool
from openagent.core.tool import ToolRegistry, _build_parameters_schema
from openagent.runtime.context import RuntimeContext
from openagent.core.types import ToolResultBlock, ToolUseBlock


def test_tool_decorator_basic():
    """Test basic tool decorator."""

    @tool
    def simple_tool(x: str) -> str:
        """A simple tool."""
        return x

    assert hasattr(simple_tool, "_tool_name")
    assert simple_tool._tool_name == "simple_tool"
    assert simple_tool._tool_description == "A simple tool."


def test_tool_decorator_custom_name():
    """Test tool decorator with custom name."""

    @tool(name="custom_name", description="Custom description")
    def my_tool(x: str) -> str:
        return x

    assert my_tool._tool_name == "custom_name"
    assert my_tool._tool_description == "Custom description"


def test_build_parameters_schema():
    """Test parameter schema generation."""

    def func(name: str, count: int, active: bool = True) -> str:
        return ""

    schema = _build_parameters_schema(func)

    assert schema["type"] == "object"
    assert "name" in schema["properties"]
    assert "count" in schema["properties"]
    assert schema["properties"]["name"]["type"] == "string"
    assert schema["properties"]["count"]["type"] == "integer"
    assert schema["required"] == ["name", "count"]


def test_build_parameters_schema_excludes_runtime_context_parameter():
    def func(context: RuntimeContext, name: str) -> str:
        return name

    schema = _build_parameters_schema(func)

    assert "context" not in schema["properties"]
    assert schema["required"] == ["name"]


def test_registry_register():
    """Test tool registry registration."""
    registry = ToolRegistry()

    @tool
    def my_tool(x: str) -> str:
        return x

    registry.register(my_tool)

    assert len(registry) == 1
    assert registry.get("my_tool") is not None


def test_registry_definitions():
    """Test tool registry definitions export."""
    registry = ToolRegistry()

    @tool
    def get_data(query: str) -> str:
        """Fetch data."""
        return query

    registry.register(get_data)
    defs = registry.definitions

    assert len(defs) == 1
    assert defs[0].name == "get_data"
    assert defs[0].description == "Fetch data."


def test_registry_definitions_exclude_runtime_context_parameter():
    registry = ToolRegistry()

    @tool
    def get_data(context: RuntimeContext, query: str) -> str:
        """Fetch data."""
        return f"{context.project_name}:{query}"

    registry.register(get_data)
    defs = registry.definitions

    assert defs[0].parameters["properties"] == {"query": {"type": "string"}}
    assert defs[0].parameters["required"] == ["query"]


async def test_registry_execute():
    """Test tool execution."""
    registry = ToolRegistry()

    @tool
    def add(a: int, b: int) -> int:
        return a + b

    registry.register(add)

    call = ToolUseBlock(id="123", name="add", arguments={"a": 2, "b": 3})
    result = await registry.execute(call)

    assert result.content == "5"
    assert not result.is_error


async def test_registry_execute_supports_runtime_context_compatibility_path():
    registry = ToolRegistry()

    @tool
    def describe_project(context: RuntimeContext, suffix: str) -> str:
        return f"{context.project_name}{suffix}"

    registry.register(describe_project)

    call = ToolUseBlock(
        id="123", name="describe_project", arguments={"suffix": " runtime"}
    )
    result = await registry.execute(
        call,
        RuntimeContext(project_name="bootstrap-project"),
    )

    assert result == ToolResultBlock(
        tool_use_id="123",
        content="bootstrap-project runtime",
        tool_name="describe_project",
    )


async def test_registry_execute_not_found():
    """Test execution of missing tool."""
    registry = ToolRegistry()

    call = ToolUseBlock(id="123", name="missing", arguments={})
    result = await registry.execute(call)

    assert result.is_error
    assert "not found" in result.content


async def test_registry_execute_error():
    """Test tool that raises exception."""
    registry = ToolRegistry()

    @tool
    def fail() -> str:
        raise ValueError("Intentional failure")

    registry.register(fail)

    call = ToolUseBlock(id="123", name="fail", arguments={})
    result = await registry.execute(call)

    assert result.is_error
    assert "Intentional failure" in result.content


async def test_async_tool():
    """Test async tool execution."""
    registry = ToolRegistry()

    @tool
    async def async_fetch(url: str) -> str:
        return f"fetched {url}"

    registry.register(async_fetch)

    call = ToolUseBlock(id="123", name="async_fetch", arguments={"url": "test.com"})
    result = await registry.execute(call)

    assert result.content == "fetched test.com"
