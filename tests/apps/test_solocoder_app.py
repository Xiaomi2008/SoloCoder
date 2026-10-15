from __future__ import annotations

from typing import Any

import pytest


def test_build_solocoder_tools_includes_workflow_tools():
    from openagent.apps.solocoder import build_solocoder_tools

    tool_names = {tool._tool_name for tool in build_solocoder_tools()}

    assert "todo_write" in tool_names
    assert "ask_user_question" in tool_names
    assert "skill" in tool_names


def test_coder_agent_exists():
    from openagent.apps.solocoder.agent import CoderAgent

    assert CoderAgent is not None
    assert hasattr(CoderAgent, "run")


def test_create_coder_exists():
    from openagent.apps.solocoder.agent import create_coder

    assert callable(create_coder)


class DummyProvider:
    def __init__(self) -> None:
        self.model = "dummy-model"
        self.api_key = None

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("chat should not be called in this test")


@pytest.mark.asyncio
async def test_coder_agent_instantiates():
    from openagent.apps.solocoder.agent import CoderAgent

    agent = CoderAgent(provider=DummyProvider())
    assert agent is not None
    assert len(agent.tool_registry) > 0
