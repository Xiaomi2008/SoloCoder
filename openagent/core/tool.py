from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Callable, get_type_hints
from dataclasses import dataclass

from .retry import with_retry
from .types import ToolDef, ToolResultBlock, ToolUseBlock

PYTHON_TYPE_TO_JSON: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _build_parameters_schema(func: Callable[..., Any]) -> dict[str, Any]:
    hints = get_type_hints(func)
    sig = inspect.signature(func)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name == "self":
            continue
        hint = hints.get(name, str)
        json_type = PYTHON_TYPE_TO_JSON.get(hint, "string")
        properties[name] = {"type": json_type}

        if param.default is inspect.Parameter.empty:
            required.append(name)
        else:
            properties[name]["default"] = param.default

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def tool(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    retry: bool = False,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> Any:
    """Decorator to register a function as a tool.

    Args:
        func: The function to decorate (if not using keyword args only)
        name: Optional custom name for the tool
        description: Optional custom description for the tool
        retry: If True, apply automatic retry logic with exponential backoff
        max_retries: Maximum retry attempts (default: 3)
        base_delay: Initial delay in seconds for retries (default: 1.0)

    Returns:
        Decorated function with tool metadata

    Example:
        @tool(retry=True, max_retries=5)
        def read_file(path: str) -> str:
            \"\"\"Read a file with automatic retry on transient failures.\"\"\"
            ...
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn._tool_name = name or fn.__name__  # type: ignore[attr-defined]
        fn._tool_description = description or fn.__doc__ or ""  # type: ignore[attr-defined]
        fn._tool_parameters = _build_parameters_schema(fn)  # type: ignore[attr-defined]

        # Apply retry decorator if requested
        if retry:
            fn = with_retry(max_retries=max_retries, base_delay=base_delay)(fn)  # type: ignore[assignment]

        return fn

    if func is not None:
        return decorator(func)
    return decorator


@dataclass
class ToolEntry:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        func: Callable[..., Any],
    ) -> None:
        """Register a tool with explicit definition."""
        self._tools[name] = ToolEntry(
            name=name,
            description=description,
            parameters=parameters,
            func=func,
        )

    def register(self, func: Callable[..., Any]) -> None:
        """Register a Python function as a tool (via @tool decorator or automatic inspection)."""
        if not hasattr(func, "_tool_name"):
            func = tool(func)
        
        self.register_tool(
            name=getattr(func, "_tool_name"),
            description=getattr(func, "_tool_description"),
            parameters=getattr(func, "_tool_parameters"),
            func=func,
        )

    def get(self, name: str) -> Callable[..., Any] | None:
        entry = self._tools.get(name)
        return entry.func if entry else None

    @property
    def definitions(self) -> list[ToolDef]:
        return [
            ToolDef(
                name=entry.name,
                description=entry.description,
                parameters=entry.parameters,
            )
            for entry in self._tools.values()
        ]

    async def execute(self, tool_call: ToolUseBlock) -> ToolResultBlock:
        entry = self._tools.get(tool_call.name)
        if entry is None:
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content=f"Error: tool '{tool_call.name}' not found",
                is_error=True,
            )
        try:
            func = entry.func

            # Check if the function has retry logic applied via @with_retry decorator
            # The decorator wraps the function and adds _retry_config attribute
            has_retry = hasattr(func, '_retry_config') or (
                hasattr(func, '__wrapped__') and hasattr(func.__wrapped__, '_retry_config')
            )

            if asyncio.iscoroutinefunction(func):
                result = await func(**tool_call.arguments)
            else:
                result = func(**tool_call.arguments)

            # Ensure result is a string (or convert to JSON string if not)
            content = result if isinstance(result, str) else json.dumps(result)
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content=content,
            )
        except Exception as e:
            # If retry was configured but exhausted, the exception will propagate here
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content=f"Error: {e}",
                is_error=True,
            )

    def __len__(self) -> int:
        return len(self._tools)
