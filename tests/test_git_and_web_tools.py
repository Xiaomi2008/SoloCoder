"""Git tools, web_fetch, async background shells, lazy MCP setup."""

from __future__ import annotations

import subprocess

import httpx
import pytest

from openagent.core.agent import Agent
from openagent.core.tool import ToolContext, ToolRegistry
from openagent.core.types import ToolUseBlock
from openagent.runtime.permissions import tool_risk
from openagent.tools.builtin import bash_background, bash_output, kill_shell, web_fetch
from openagent.tools.git import git_commit, git_diff, git_log, git_status
from tests.support.streaming_provider import ScriptedStreamProvider


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Tester")
    (tmp_path / "a.txt").write_text("one\n")
    git("add", "a.txt")
    git("commit", "-q", "-m", "first commit")
    return tmp_path


# ---------------------------------------------------------------------- git


async def test_git_tools_run_in_the_session_directory_not_the_process_one(repo):
    ctx = ToolContext(working_dir=str(repo))

    assert "Working tree clean" in await git_status(tool_context=ctx)
    assert "first commit" in await git_log(tool_context=ctx)

    (repo / "a.txt").write_text("one\ntwo\n")
    (repo / "b.txt").write_text("new\n")
    status = await git_status(tool_context=ctx)
    assert " M a.txt" in status and "?? b.txt" in status
    diff = await git_diff(tool_context=ctx)
    assert "+two" in diff and "\033[" not in diff


async def test_git_commit_stages_the_named_paths(repo):
    ctx = ToolContext(working_dir=str(repo))
    (repo / "b.txt").write_text("new\n")

    assert (await git_commit("nothing staged", tool_context=ctx)).startswith("Nothing to commit")
    result = await git_commit("add b", paths=["b.txt"], tool_context=ctx)

    assert "add b" in result
    assert "add b" in await git_log(count=1, tool_context=ctx)
    assert await git_diff(staged=True, tool_context=ctx) == "No staged changes."


async def test_git_diff_refuses_an_option_disguised_as_a_ref(repo):
    result = await git_diff(ref="--output=/tmp/x", tool_context=ToolContext(working_dir=str(repo)))
    assert result.startswith("Error")


async def test_git_outside_a_repository_is_an_error(tmp_path):
    assert (await git_status(tool_context=ToolContext(working_dir=str(tmp_path)))).startswith("Error")


def test_read_only_git_tools_need_no_approval():
    assert [tool_risk(n) for n in ("git_status", "git_diff", "git_log")] == ["read"] * 3
    assert tool_risk("git_commit") == "write"


# ---------------------------------------------------------------- web_fetch


@pytest.fixture
def served(monkeypatch):
    """Route web_fetch through a mock transport."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/old":
            return httpx.Response(301, headers={"location": "https://example.com/new"})
        if path == "/new":
            return httpx.Response(
                200,
                html="<html><style>p{}</style><script>x()</script><p>Hello <b>page</b></p></html>",
                headers={"x-noise": "1", "last-modified": "Wed, 01 Jan 2026 00:00:00 GMT"},
            )
        if path == "/limited":
            return httpx.Response(429, json={"error": "slow down"}, headers={"retry-after": "30"})
        return httpx.Response(200, text="x" * 6000)

    monkeypatch.setattr(
        httpx, "get", lambda url, **kw: httpx.Client(transport=httpx.MockTransport(handler)).get(url, **kw)
    )


def test_web_fetch_shows_status_and_key_headers_then_text(served):
    result = web_fetch("https://example.com/new")

    lines = result.splitlines()
    assert lines[0] == "HTTP 200 OK"
    assert "content-type: text/html; charset=utf-8" in lines
    assert "last-modified: Wed, 01 Jan 2026 00:00:00 GMT" in lines
    assert "x-noise" not in result
    assert lines[-1] == "Hello page"


def test_web_fetch_follows_redirects_and_says_where_it_landed(served):
    """It used to stop at the redirect and report it as an error."""
    result = web_fetch("https://example.com/old")
    assert result.splitlines()[0] == "HTTP 200 OK (https://example.com/new)"
    assert "Hello page" in result


def test_web_fetch_returns_the_body_of_an_error_status(served):
    result = web_fetch("https://example.com/limited")
    assert result.startswith("HTTP 429 Too Many Requests")
    assert "retry-after: 30" in result
    assert "slow down" in result


def test_web_fetch_cuts_long_content(served):
    result = web_fetch("https://example.com/big")
    assert "[cut at 5,000 of 6,000 characters]" in result


def test_web_fetch_needs_no_approval_and_http_request_is_gone():
    import openagent.tools as tools

    assert tool_risk("web_fetch") == "network"
    assert not hasattr(tools, "http_request")


# --------------------------------------------------------- background shells


async def test_background_shells_work_inside_the_running_event_loop(tmp_path):
    """They called asyncio.run(), which raises inside the agent's own loop."""
    registry = ToolRegistry()
    for fn in (bash_background, bash_output, kill_shell):
        registry.register(fn)
    ctx = ToolContext(working_dir=str(tmp_path))

    started = await registry.execute(
        ToolUseBlock(id="1", name="bash_background", arguments={"command": "sleep 30"}), context=ctx
    )
    assert not started.is_error and "Started bash session" in started.content, started.content
    session_id = started.content.split("'")[1]

    killed = await registry.execute(
        ToolUseBlock(id="2", name="kill_shell", arguments={"session_id": session_id}), context=ctx
    )
    assert not killed.is_error and not killed.content.startswith("Error"), killed.content


# ---------------------------------------------------------------------- MCP


class FakeMcp:
    def __init__(self) -> None:
        self.connected = 0

    async def __aenter__(self):
        self.connected += 1
        return self

    async def get_tools(self):
        async def mcp_echo(text: str) -> str:
            return text

        mcp_echo._tool_name = "mcp_echo"
        mcp_echo._tool_description = "Echo"
        mcp_echo._tool_parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
        return [mcp_echo]


async def test_an_agent_with_mcp_can_be_built_inside_a_running_loop():
    mcp = FakeMcp()
    agent = Agent(provider=ScriptedStreamProvider(), mcp_client=mcp)
    assert mcp.connected == 0

    await agent.run("hi")
    await agent.run("again")

    assert mcp.connected == 1
    assert "mcp_echo" in [entry.name for entry in agent.tool_registry.entries]
