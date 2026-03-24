from __future__ import annotations

from typing import Any

import pytest

from openagent.coder import CoderAgent as CompatibilityCoderAgent


def test_solocoder_app_exports_coder_agent():
    from openagent.apps.solocoder import CoderAgent

    assert CoderAgent is CompatibilityCoderAgent


def test_build_solocoder_tools_includes_workflow_tools():
    from openagent.apps.solocoder import build_solocoder_tools

    tool_names = {tool._tool_name for tool in build_solocoder_tools()}

    assert "todo_write" in tool_names
    assert "ask_user_question" in tool_names
    assert "skill" in tool_names


def test_openagent_coder_compatibility_exports_app_types():
    from openagent.apps.solocoder.agent import CoderAgent, create_coder
    from openagent.coder import create_coder as compatibility_create_coder

    assert CompatibilityCoderAgent is CoderAgent
    assert compatibility_create_coder is create_coder


class DummyProvider:
    def __init__(self) -> None:
        self.model = "dummy-model"
        self.api_key = None

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("chat should not be called in this test")


@pytest.mark.asyncio
async def test_coder_agent_run_uses_configured_compaction_defaults(
    monkeypatch: pytest.MonkeyPatch,
):
    from openagent.apps.solocoder.agent import CoderAgent
    from openagent.core.agent import Agent as BaseAgent

    captured: dict[str, Any] = {}

    async def fake_run(self, user_input: str, **kwargs: Any) -> str:
        captured["user_input"] = user_input
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr(BaseAgent, "run", fake_run)

    agent = CoderAgent(
        provider=DummyProvider(),
        max_context_tokens=4096,
        compact_threshold=0.5,
        disable_compaction=True,
    )

    result = await agent.run("refactor this")

    assert result == "ok"
    assert captured["user_input"] == "refactor this"
    assert captured["kwargs"] == {
        "max_context_tokens": 4096,
        "compact_threshold": 0.5,
        "disable_compaction": True,
    }
