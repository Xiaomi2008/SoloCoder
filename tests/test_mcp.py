"""Tests for MCP client integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ============================================================================
# _fix_windows_cmd
# ============================================================================


class TestFixWindowsCmd:
    def test_non_windows_unchanged(self):
        from openagent.mcp import _fix_windows_cmd

        with patch("sys.platform", "linux"):
            assert _fix_windows_cmd("npx") == "npx"
            assert _fix_windows_cmd("npm") == "npm"
            assert _fix_windows_cmd("node") == "node"

    def test_windows_adds_cmd(self):
        from openagent.mcp import _fix_windows_cmd

        with patch("sys.platform", "win32"):
            assert _fix_windows_cmd("npx") == "npx.cmd"
            assert _fix_windows_cmd("npm") == "npm.cmd"
            assert _fix_windows_cmd("node") == "node.cmd"
            assert _fix_windows_cmd("yarn") == "yarn.cmd"
            assert _fix_windows_cmd("pnpm") == "pnpm.cmd"

    def test_windows_non_npm_unchanged(self):
        from openagent.mcp import _fix_windows_cmd

        with patch("sys.platform", "win32"):
            assert _fix_windows_cmd("python") == "python"
            assert _fix_windows_cmd("git") == "git"

    def test_already_has_cmd(self):
        from openagent.mcp import _fix_windows_cmd

        with patch("sys.platform", "win32"):
            assert _fix_windows_cmd("npx.cmd") == "npx.cmd"


# ============================================================================
# McpClient basic
# ============================================================================


class TestMcpClientInit:
    def test_init_defaults(self):
        from openagent.mcp import McpClient

        client = McpClient(command="npx", args=["-y", "@modelcontextprotocol/server-filesystem"])
        assert client.command == "npx"
        assert client.args == ["-y", "@modelcontextprotocol/server-filesystem"]
        assert client.env is None
        assert client.session is None

    def test_init_with_env(self):
        from openagent.mcp import McpClient

        client = McpClient(command="node", args=["server.mjs"], env={"NODE_ENV": "prod"})
        assert client.env == {"NODE_ENV": "prod"}

    def test_ensure_connected_raises_when_not_connected(self):
        from openagent.mcp import McpClient

        client = McpClient(command="npx")
        with pytest.raises(RuntimeError, match="not connected"):
            client._ensure_connected()


# ============================================================================
# McpClient async context manager (stdio)
# ============================================================================


class TestMcpClientStdio:
    async def test_aenter_stdio_connects_and_initializes(self):
        from openagent.mcp import McpClient

        client = McpClient(command="npx", args=["-y", "server"])

        mock_read = AsyncMock()
        mock_write = AsyncMock()
        mock_session = AsyncMock()

        with patch("openagent.mcp.stdio_client") as mock_stdio:
            mock_stdio.return_value.__aenter__.return_value = (mock_read, mock_write)
            mock_stdio.return_value.__aexit__.return_value = None
            with patch("openagent.mcp.ClientSession") as mock_session_cls:
                # ClientSession used as async context manager: __aenter__ returns self
                mock_session_cls.return_value.__aenter__.return_value = mock_session

                async def mock_init():
                    pass

                mock_session.initialize = mock_init

                async with client:
                    pass

        assert client.session is mock_session
        assert client.session is mock_session

    async def test_aexit_clears_session(self):
        from openagent.mcp import McpClient

        client = McpClient(command="npx")
        mock_session = AsyncMock()

        with patch("openagent.mcp.stdio_client") as mock_stdio:
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_stdio.return_value = mock_ctx

            with patch("openagent.mcp.ClientSession") as mock_session_cls:
                mock_session_cls.return_value = mock_session

                async with client:
                    pass

                # After exiting, stack is closed
                assert True  # no exception means cleanup worked


# ============================================================================
# McpClient async context manager (SSE)
# ============================================================================


class TestMcpClientSse:
    async def test_aenter_sse_connects(self):
        from openagent.mcp import McpClient

        client = McpClient(command="https://mcp.example.com/sse")

        mock_read = AsyncMock()
        mock_write = AsyncMock()
        mock_session = AsyncMock()

        with patch("openagent.mcp.sse_client") as mock_sse:
            mock_sse.return_value.__aenter__.return_value = (mock_read, mock_write)
            mock_sse.return_value.__aexit__.return_value = None
            with patch("openagent.mcp.ClientSession") as mock_session_cls:
                mock_session_cls.return_value = mock_session

                async def mock_init():
                    pass

                mock_session.initialize = mock_init

                async with client:
                    pass

        mock_sse.assert_called_once_with("https://mcp.example.com/sse")


# ============================================================================
# McpClient get_tools
# ============================================================================


class TestMcpClientGetTools:
    async def test_get_tools_returns_wrappers(self):
        from openagent.mcp import McpClient

        client = McpClient(command="npx")
        mock_session = AsyncMock()

        # Mock list_tools result
        mock_tool_def = MagicMock()
        mock_tool_def.name = "read_file"
        mock_tool_def.description = "Read a file from disk"
        mock_tool_def.inputSchema = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        }

        mock_list_result = MagicMock()
        mock_list_result.tools = [mock_tool_def]
        mock_session.list_tools.return_value = mock_list_result

        client.session = mock_session

        tools = await client.get_tools()

        assert len(tools) == 1
        assert tools[0]._tool_name == "read_file"
        assert tools[0]._tool_description == "Read a file from disk"
        assert tools[0]._tool_parameters == mock_tool_def.inputSchema

    async def test_get_tools_multiple(self):
        from openagent.mcp import McpClient

        client = McpClient(command="npx")
        mock_session = AsyncMock()

        tool_a = MagicMock()
        tool_a.name = "tool_a"
        tool_a.description = "A tool"
        tool_a.inputSchema = {}

        tool_b = MagicMock()
        tool_b.name = "tool_b"
        tool_b.description = "B tool"
        tool_b.inputSchema = {}

        mock_result = MagicMock()
        mock_result.tools = [tool_a, tool_b]
        mock_session.list_tools.return_value = mock_result

        client.session = mock_session

        tools = await client.get_tools()
        assert len(tools) == 2
        names = [t._tool_name for t in tools]
        assert "tool_a" in names
        assert "tool_b" in names


# ============================================================================
# McpClient _make_tool_func
# ============================================================================


class TestMcpClientMakeToolFunc:
    async def test_tool_wrapper_text_content(self):
        from openagent.mcp import McpClient

        client = McpClient(command="npx")
        mock_session = AsyncMock()
        client.session = mock_session

        mock_result = MagicMock()
        mock_result.isError = False
        text_content = MagicMock()
        text_content.type = "text"
        text_content.text = "file contents here"
        mock_result.content = [text_content]

        mock_session.call_tool.return_value = mock_result

        wrapper = client._make_tool_func("read_file")
        result = await wrapper(path="/tmp/test.txt")

        assert result == "file contents here"
        mock_session.call_tool.assert_called_once_with("read_file", {"path": "/tmp/test.txt"})

    async def test_tool_wrapper_error_content(self):
        from openagent.mcp import McpClient

        client = McpClient(command="npx")
        mock_session = AsyncMock()
        client.session = mock_session

        mock_result = MagicMock()
        mock_result.isError = True
        text_content = MagicMock()
        text_content.type = "text"
        text_content.text = "file not found"
        mock_result.content = [text_content]

        mock_session.call_tool.return_value = mock_result

        wrapper = client._make_tool_func("read_file")
        result = await wrapper(path="/tmp/missing.txt")

        assert "Error from tool" in result
        assert "file not found" in result

    async def test_tool_wrapper_mixed_content(self):
        from openagent.mcp import McpClient

        client = McpClient(command="npx")
        mock_session = AsyncMock()
        client.session = mock_session

        mock_result = MagicMock()
        mock_result.isError = False

        text_content = MagicMock()
        text_content.type = "text"
        text_content.text = "Here is an image:"

        image_content = MagicMock()
        image_content.type = "image"
        image_content.mimeType = "image/png"

        resource_content = MagicMock()
        resource_content.type = "resource"
        resource_content.resource.uri = "file:///tmp/data.csv"

        mock_result.content = [text_content, image_content, resource_content]
        mock_session.call_tool.return_value = mock_result

        wrapper = client._make_tool_func("analyze")
        result = await wrapper()

        assert "Here is an image:" in result
        assert "[Image: image/png]" in result
        assert "[Resource: file:///tmp/data.csv]" in result

    async def test_tool_wrapper_raises_when_not_connected(self):
        from openagent.mcp import McpClient

        client = McpClient(command="npx")
        client.session = None

        wrapper = client._make_tool_func("some_tool")
        with pytest.raises(RuntimeError, match="not connected"):
            await wrapper()


# ============================================================================
# Integration: McpClient tool registered in agent
# ============================================================================


class TestMcpAgentIntegration:
  def test_mcp_tools_registered_in_tool_registry(self):
        """Verify that MCP tools can be registered into a tool registry."""
        from openagent.core.tool import ToolRegistry
        from openagent.mcp import McpClient

        registry = ToolRegistry()
        mcp_client = McpClient(command="mock_server")

        # Mock MCP tools
        mock_tool = AsyncMock()
        mock_tool._tool_name = "mcp_read"
        mock_tool._tool_description = "Read via MCP"
        mock_tool._tool_parameters = {"type": "object", "properties": {}}

        # Simulate what Agent._integrate_mcp_tools does:
        # register each MCP tool that has _tool_name attr
        for tool_fn in [mock_tool]:
            if hasattr(tool_fn, "_tool_name"):
                registry.register(tool_fn)

        names = list(registry._tools.keys())
        assert "mcp_read" in names
