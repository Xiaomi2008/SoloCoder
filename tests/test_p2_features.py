"""Tests for P2 features: MCP deferred init, auto-save, config, session leak fix."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openagent.core.types import Message, ToolDef
from tests.conftest import MockProvider


# ============================================================================
# MCP deferred integration - Agent.__init__ must not block
# ============================================================================


class TestMcpDeferredInit:
    def test_init_does_not_call_mcp(self):
        """Agent.__init__ should not integrate MCP tools immediately."""
        from openagent.core.agent import Agent

        mcp_client = MagicMock()
        provider = MockProvider(responses=[Message(role="assistant", content="OK")])

        # __init__ should complete instantly even with a broken MCP client
        agent = Agent(provider=provider, mcp_client=mcp_client)
        assert agent._mcp_client is mcp_client
        assert agent._mcp_integrated is False

    async def test_mcp_integrated_on_first_run(self):
        """MCP tools should be integrated during the first run() call."""
        from openagent.core.agent import Agent

        mock_mcp = MagicMock()
        mock_tool = MagicMock()
        mock_tool._tool_name = "mcp_tool"
        mock_mcp.get_tools = AsyncMock(return_value=[mock_tool])
        mock_mcp.__aenter__ = AsyncMock(return_value=mock_mcp)
        mock_mcp.__aexit__ = AsyncMock(return_value=None)

        provider = MockProvider(responses=[Message(role="assistant", content="Done")])
        agent = Agent(provider=provider, mcp_client=mock_mcp)

        assert agent._mcp_integrated is False
        result = await agent.run("test")
        assert agent._mcp_integrated is True
        assert "mcp_tool" in agent.tool_registry._tools

    async def test_mcp_timeout_does_not_block(self):
        """MCP integration with slow server should timeout and continue."""
        from openagent.core.agent import Agent

        async def slow_connect(self):
            await asyncio.sleep(10)  # Simulates unresponsive server

        mock_mcp = MagicMock()
        mock_mcp.__aenter__ = slow_connect
        mock_mcp.__aexit__ = AsyncMock(return_value=None)

        provider = MockProvider(responses=[Message(role="assistant", content="OK")])
        agent = Agent(provider=provider, mcp_client=mock_mcp)

        # Should complete quickly despite slow MCP (timeout=0.1s)
        await agent._integrate_mcp_tools(timeout=0.1)

    async def test_mcp_integrated_only_once(self):
        """MCP should only be integrated once, even across multiple runs."""
        from openagent.core.agent import Agent

        integrate_count = 0

        async def count_integrations():
            nonlocal integrate_count
            integrate_count += 1
            return []

        mock_mcp = MagicMock()
        mock_mcp.__aenter__ = AsyncMock(return_value=mock_mcp)
        mock_mcp.__aexit__ = AsyncMock(return_value=None)
        mock_mcp.get_tools = AsyncMock(side_effect=count_integrations)

        provider = MockProvider(responses=[
            Message(role="assistant", content="First"),
            Message(role="assistant", content="Second"),
        ])
        agent = Agent(provider=provider, mcp_client=mock_mcp)

        await agent.run("one")
        await agent.run("two")

        assert integrate_count == 1, "MCP should only be integrated once"


# ============================================================================
# Auto-save session
# ============================================================================


class TestAutoSave:
    async def test_auto_save_writes_file(self, tmp_path):
        """When auto_save is set, session should be saved after each run."""
        from openagent.core.agent import Agent
        import json

        save_path = tmp_path / "session.json"
        provider = MockProvider(responses=[Message(role="assistant", content="Done!")])
        agent = Agent(provider=provider, auto_save=str(save_path))

        await agent.run("hello")

        assert save_path.exists()
        data = json.loads(save_path.read_text())
        assert data["system_prompt"] == ""
        assert len(data["messages"]) >= 2  # user + assistant

    async def test_auto_save_persists_tool_calls(self, tmp_path):
        """Auto-saved session should include tool call history."""
        from openagent.core.agent import Agent
        from openagent.core.types import TextBlock, ToolUseBlock
        from openagent import tool
        import json

        save_path = tmp_path / "session_with_tools.json"
        provider = MockProvider(responses=[
            Message(
                role="assistant",
                content=[
                    TextBlock(text="using tool"),
                    ToolUseBlock(id="c1", name="read", arguments={"file": "x.py"}),
                ],
            ),
            Message(role="assistant", content="done"),
        ])

        @tool
        def read(file: str) -> str:
            return "file contents"

        agent = Agent(
            provider=provider,
            tools=[read],
            auto_save=str(save_path),
        )
        await agent.run("read a file")

        data = json.loads(save_path.read_text())
        roles = [m["role"] for m in data["messages"]]
        assert "tool_result" in roles

    async def test_auto_save_missing_path_no_crash(self):
        """Auto-save to an unwritable path should not crash the agent."""
        from openagent.core.agent import Agent

        provider = MockProvider(responses=[Message(role="assistant", content="OK")])
        agent = Agent(
            provider=provider,
            auto_save="/impossible/path/session.json",
        )
        # Should not raise
        result = await agent.run("test")
        assert result == "OK"


# ============================================================================
# Config file support
# ============================================================================


class TestConfig:
    def test_config_from_missing_file(self):
        """Loading from non-existent file returns defaults."""
        from openagent.core.config import Config as C

        config = C.from_file("/nonexistent/path/config.yml")
        assert config.model == "gpt-4o"
        assert config.max_turns == 20

    def test_config_from_file(self, tmp_path):
        """Loading from YAML file reads all fields."""
        from openagent.core.config import Config as C

        config_file = tmp_path / "config.yml"
        config_file.write_text(
            "model: claude-sonnet-4\n"
            "max_turns: 50\n"
            "base_url: http://localhost:8080\n"
            "auto_save: /tmp/session.json\n"
            "enable_learning: true\n"
            "max_messages: 100\n"
        )

        config = C.from_file(config_file)
        assert config.model == "claude-sonnet-4"
        assert config.max_turns == 50
        assert config.base_url == "http://localhost:8080"
        assert config.auto_save == "/tmp/session.json"
        assert config.enable_learning is True
        assert config.max_messages == 100

    def test_config_override(self):
        """Override replaces specified fields."""
        from openagent.core.config import Config as C

        config = C(model="gpt-4o", max_turns=20)
        overridden = config.override(model="claude-sonnet-4")
        assert overridden.model == "claude-sonnet-4"
        assert overridden.max_turns == 20  # unchanged

    def test_override_skips_falsy(self):
        """Override should skip None and empty string values."""
        from openagent.core.config import Config as C

        config = C(model="gpt-4o", max_turns=20)
        overridden = config.override(model=None, max_turns="")
        assert overridden.model == "gpt-4o"
        assert overridden.max_turns == 20

    def test_apply_env(self, monkeypatch):
        """Environment variables override config values."""
        from openagent.core.config import Config as C

        monkeypatch.setenv("SOLO_CODER_MODEL", "claude-opus-4-7")
        monkeypatch.setenv("SOLO_CODER_MAX_TURNS", "100")

        config = C().apply_env()
        assert config.model == "claude-opus-4-7"
        assert config.max_turns == 100

    def test_env_priority_over_file(self, tmp_path, monkeypatch):
        """Env vars take priority over config file values."""
        from openagent.core.config import Config as C

        config_file = tmp_path / "config.yml"
        config_file.write_text("model: gpt-4o\n")

        monkeypatch.setenv("SOLO_CODER_MODEL", "claude-sonnet-4")

        config = C.from_file(config_file).apply_env()
        assert config.model == "claude-sonnet-4"

    def test_to_dict(self):
        from openagent.core.config import Config as C

        config = C(model="test-model", max_turns=42)
        d = config.to_dict()
        assert d["model"] == "test-model"
        assert d["max_turns"] == 42


# ============================================================================
# CLI bash session reuse (verified by checking the code structure)
# ============================================================================


class TestCliBashSessionReuse:
    def test_cli_uses_single_session(self):
        """Verify CLI code creates a single bash session for all commands."""
        source = Path(__file__).parent.parent / "cli_coder.py"
        content = source.read_text()

        assert "cli_bash_session = await coder.bash_manager.start_session()" in content
        assert "cli_bash_session," in content

    def test_cli_cleans_up_session(self):
        """Verify CLI cleans up the bash session on exit."""
        source = Path(__file__).parent.parent / "cli_coder.py"
        content = source.read_text()

        assert "finally:" in content
        assert "kill_session(cli_bash_session)" in content
