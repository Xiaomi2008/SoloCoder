from __future__ import annotations

from openagent.core.tool import ToolRegistry, _execute_tool_entry
from openagent.core.types import ToolResultBlock, ToolUseBlock
from openagent.runtime.context import RuntimeContext


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(
        self,
        tool_call: ToolUseBlock,
        context: RuntimeContext,
    ) -> ToolResultBlock:
        entry = self._registry.resolve(tool_call.name)
        if entry is None:
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                tool_name=tool_call.name,
                content=f"Error: tool '{tool_call.name}' not found",
                is_error=True,
            )
        return await _execute_tool_entry(entry, tool_call, context)
