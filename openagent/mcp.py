from __future__ import annotations

import sys
from contextlib import AsyncExitStack
from typing import Any, Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult


def _fix_windows_cmd(command: str) -> str:
    """On Windows, npm/npx/node scripts need .cmd extension for subprocess spawning."""
    if sys.platform == "win32":
        # Common Node.js commands that need .cmd extension on Windows
        npm_commands = {"npx", "npm", "node", "yarn", "pnpm"}
        cmd_lower = command.lower()
        if cmd_lower in npm_commands and not cmd_lower.endswith(".cmd"):
            return command + ".cmd"
    return command


class McpClient:
    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.command = command
        self.args = args or []
        self.env = env
        self.session: ClientSession | None = None
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> "McpClient":
        if self.command.startswith("http://") or self.command.startswith("https://"):
            read, write = await self._stack.enter_async_context(sse_client(self.command))
        else:
            fixed_cmd = _fix_windows_cmd(self.command)
            params = StdioServerParameters(command=fixed_cmd, args=self.args, env=self.env)
            read, write = await self._stack.enter_async_context(stdio_client(params))

        self.session = await self._stack.enter_async_context(
            ClientSession(read, write)
        )
        await self.session.initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self._stack.aclose()

    async def get_tools(self) -> list[Callable[..., Any]]:
        self._ensure_connected()

        result = await self.session.list_tools()
        tools = []
        for tool in result.tools:
            # Create wrapper with attached attributes
            wrapper = self._make_tool_func(tool.name)
            wrapper._tool_name = tool.name  # type: ignore
            wrapper._tool_description = tool.description or ""  # type: ignore
            wrapper._tool_parameters = tool.inputSchema  # type: ignore
            tools.append(wrapper)
        return tools

    def _ensure_connected(self) -> None:
        if not self.session:
            raise RuntimeError("McpClient not connected")

    def _make_tool_func(self, tool_name: str) -> Callable[..., Any]:
        async def tool_wrapper(**kwargs: Any) -> str:
            if not self.session:
                raise RuntimeError("McpClient not connected")

            result: CallToolResult = await self.session.call_tool(tool_name, kwargs)

            # Combine all text content
            texts = []
            for content in result.content:
                if content.type == "text":
                    texts.append(content.text)
                elif content.type == "image":
                    texts.append(f"[Image: {content.mimeType}]")
                elif content.type == "resource":
                    texts.append(f"[Resource: {content.resource.uri}]")

            final_text = "\n".join(texts)
            if result.isError:
                return f"Error from tool: {final_text}"
            return final_text

        return tool_wrapper
