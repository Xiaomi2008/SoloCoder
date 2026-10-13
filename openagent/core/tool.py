from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Callable, get_type_hints
from dataclasses import dataclass

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
        if name in _INJECTED_PARAMS:
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
) -> Any:
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn._tool_name = name or fn.__name__  # type: ignore[attr-defined]
        fn._tool_description = description or fn.__doc__ or ""  # type: ignore[attr-defined]
        fn._tool_parameters = _build_parameters_schema(fn)  # type: ignore[attr-defined]
        return fn

    if func is not None:
        return decorator(func)
    return decorator


@dataclass
class ToolContext:
    """Per-session facts a tool needs and the model must not be asked for.

    The model addresses files by path; the *session* decides what a relative
    path means. Before this, ``Agent(working_dir=...)`` was decorative: every
    tool resolved paths against ``Path.cwd()``, so a coding agent scoped to a
    project wrote its files into whatever directory the process happened to
    start in — in a .app bundle, that is inside the app.
    """

    working_dir: str | None = None

    def resolve(self, path: str | Path | None = None) -> Path:
        """Absolute path for ``path``, anchored at the session directory."""
        base = (
            Path(self.working_dir).expanduser().resolve()
            if self.working_dir
            else Path.cwd()
        )
        if path is None or str(path) == "":
            return base
        candidate = Path(path).expanduser()
        return candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()


# Parameters the harness fills in; they must never reach the model's schema.
_INJECTED_PARAMS = frozenset({"context", "tool_context"})


@dataclass
class ToolEntry:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., Any]


async def _execute_tool_entry(
    entry: ToolEntry,
    tool_call: ToolUseBlock,
    context: Any | None = None,
) -> ToolResultBlock:
    try:
        func = entry.func
        arguments = dict(tool_call.arguments)

        if context is not None and "context" in inspect.signature(func).parameters:
            arguments["context"] = context

        # A tool that asks for the session context always gets one: a default
        # ToolContext means "the process directory", which is the old behaviour.
        if "tool_context" in inspect.signature(func).parameters:
            arguments["tool_context"] = (
                context if isinstance(context, ToolContext) else ToolContext()
            )

        if inspect.iscoroutinefunction(func):
            result = await func(**arguments)
        else:
            result = func(**arguments)

        content = result if isinstance(result, str) else json.dumps(result)
        return ToolResultBlock(
            tool_use_id=tool_call.id,
            tool_name=entry.name,
            content=content,
        )
    except Exception as e:
        return ToolResultBlock(
            tool_use_id=tool_call.id,
            tool_name=entry.name,
            content=f"Error: {e}",
            is_error=True,
        )


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

    @property
    def entries(self) -> list[ToolEntry]:
        """Registered tools, for anything that lists them (the service's ``tool.list``)."""
        return list(self._tools.values())

    def get(self, name: str) -> Callable[..., Any] | None:
        entry = self._tools.get(name)
        return entry.func if entry else None

    def resolve(self, name: str) -> ToolEntry | None:
        return self._tools.get(name)

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

    async def execute(
        self,
        tool_call: ToolUseBlock,
        context: Any | None = None,
    ) -> ToolResultBlock:
        entry = self.resolve(tool_call.name)
        if entry is None:
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                tool_name=tool_call.name,
                content=f"Error: tool '{tool_call.name}' not found",
                is_error=True,
            )
        return await _execute_tool_entry(entry, tool_call, context)

    def _display_sub_agent_ui(self, arguments: dict) -> None:
        """Display visual indicator for sub-agent task."""
        from openagent import bold, cyan, dim, yellow

        agent_type = arguments.get("agent_type", "general-purpose")
        description = arguments.get("description", "task")

        icons = {
            "explore": "🔍",
            "plan": "📋",
            "code": "💻",
            "general-purpose": "🤖",
        }
        icon = icons.get(agent_type, "🔧")
        type_name = agent_type.title()

        prefix = "  "
        print(prefix + cyan(icon) + " Sub-Agent " + bold(type_name))
        print(prefix + dim("Description:") + " " + description[:100])
        print(prefix + dim("Status:") + " " + yellow("⠋ Processing..."))
        print(
            prefix + dim("Note:") + " " + cyan("Isolated context to prevent pollution")
        )

    def __len__(self) -> int:
        return len(self._tools)
