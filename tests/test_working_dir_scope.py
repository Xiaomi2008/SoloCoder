"""The session working directory must scope the tools, not decorate them.

Found by the Phase 1 live gate: an agent built with ``working_dir=/tmp/x``
created its file in the repo, because every tool resolved paths against
``Path.cwd()``. In a .app bundle that means writing inside the app.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openagent import Agent, tool
from openagent.core.tool import ToolContext, ToolRegistry
from openagent.core.types import Message, ToolUseBlock
from openagent.tools.builtin import bash, edit, glob, grep, read, write


@pytest.fixture
def project(tmp_path):
    (tmp_path / "alpha.py").write_text("print('alpha')\n")
    (tmp_path / "beta.txt").write_text("needle in beta\n")
    return tmp_path


# ---------------------------------------------------------------- ToolContext


def test_tool_context_anchors_relative_paths(project):
    ctx = ToolContext(working_dir=str(project))
    assert ctx.resolve("notes.txt") == (project / "notes.txt").resolve()
    assert ctx.resolve(None) == project.resolve()
    assert ctx.resolve("sub/deep.txt") == (project / "sub" / "deep.txt").resolve()


def test_tool_context_leaves_absolute_paths_alone(project):
    ctx = ToolContext(working_dir=str(project))
    elsewhere = (project.parent / "elsewhere.txt").resolve()
    assert ctx.resolve(str(elsewhere)) == elsewhere


def test_tool_context_expands_tilde():
    ctx = ToolContext(working_dir="~/nope")
    assert "~" not in str(ctx.resolve("x"))


# ---------------------------------------------------------------------- tools


def test_write_resolves_relative_paths_into_the_session(project):
    ctx = ToolContext(working_dir=str(project))
    write(path="notes.txt", content="scoped", tool_context=ctx)
    assert (project / "notes.txt").read_text() == "scoped"


def test_read_edit_and_glob_use_the_session(project):
    ctx = ToolContext(working_dir=str(project))
    assert "alpha" in read(path="alpha.py", tool_context=ctx)
    edit(path="alpha.py", find="alpha", replace="omega", tool_context=ctx)
    assert "omega" in (project / "alpha.py").read_text()
    assert "alpha.py" in glob(pattern="*.py", tool_context=ctx)
    assert "beta.txt" in grep(pattern="needle", tool_context=ctx)


def test_bash_runs_in_the_session(project):
    ctx = ToolContext(working_dir=str(project))
    assert bash(command="pwd", tool_context=ctx).strip() == str(project)


def test_bash_working_dir_is_relative_to_the_session(project):
    ctx = ToolContext(working_dir=str(project))
    (project / "sub").mkdir()
    assert bash(command="pwd", working_dir="sub", tool_context=ctx).strip() == str(project / "sub")


def test_default_context_is_the_process_directory(tmp_path, monkeypatch):
    """No context = the old behaviour, so existing callers do not move."""
    monkeypatch.chdir(tmp_path)
    assert ToolContext().resolve("here.txt") == (tmp_path / "here.txt").resolve()


# ----------------------------------------------------------------------- schema


def test_the_injected_context_never_reaches_the_model():
    """A model that can set tool_context escapes the session scope."""
    registry = ToolRegistry()
    registry.register(write)
    registry.register(bash)
    registry.register(grep)
    for definition in registry.definitions:
        assert "tool_context" not in definition.parameters["properties"]
        assert "tool_context" not in definition.parameters.get("required", [])


# ------------------------------------------------------------------------ agent


class _ScriptedProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.model = "scripted"

    async def chat(self, messages, tools=None, system_prompt="", **kwargs):
        return self.responses.pop(0)

    async def close(self):
        return None


async def test_agent_scopes_tool_paths_to_its_working_dir(project, tmp_path):
    agent = Agent(
        provider=_ScriptedProvider(
            [
                Message(
                    role="assistant",
                    content=[
                        ToolUseBlock(
                            id="1",
                            name="write",
                            arguments={"path": "notes.txt", "content": "scoped by the agent"},
                        )
                    ],
                ),
                Message(role="assistant", content="done"),
            ]
        ),
        tools=[write],
        working_dir=str(project),
    )

    assert await agent.run("create notes.txt") == "done"
    assert (project / "notes.txt").read_text() == "scoped by the agent"
    assert not (Path.cwd() / "notes.txt").exists()


async def test_agent_defaults_to_the_process_directory(project, monkeypatch):
    monkeypatch.chdir(project)
    agent = Agent(
        provider=_ScriptedProvider(
            [
                Message(
                    role="assistant",
                    content=[ToolUseBlock(id="1", name="write", arguments={"path": "here.txt", "content": "x"})]
                ),
                Message(role="assistant", content="done"),
            ]
        ),
        tools=[write],
    )
    await agent.run("go")
    assert (project / "here.txt").exists()


async def test_coder_agent_run_moves_the_scope(project, tmp_path):
    from openagent.apps.solocoder import CoderAgent

    coder = CoderAgent(
        provider=_ScriptedProvider(
            [
                Message(
                    role="assistant",
                    content=[ToolUseBlock(id="1", name="write", arguments={"path": "notes.txt", "content": "a"})]
                ),
                Message(role="assistant", content="done"),
            ]
        ),
        working_dir=str(project),
    )
    assert coder.working_dir == str(project)
    await coder.run("first")
    assert (project / "notes.txt").exists()

    coder2 = CoderAgent(
        provider=_ScriptedProvider(
            [
                Message(
                    role="assistant",
                    content=[ToolUseBlock(id="2", name="write", arguments={"path": "notes.txt", "content": "b"})]
                ),
                Message(role="assistant", content="done"),
            ]
        ),
        working_dir=str(project),
    )
    moved = tmp_path / "moved"
    moved.mkdir()
    await coder2.run("second", working_dir=str(moved))
    assert coder2.working_dir == str(moved)
    assert (moved / "notes.txt").read_text() == "b"
