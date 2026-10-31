"""Tools ported from PR #10: git, http_request, async background shells, lazy MCP."""

from __future__ import annotations

import subprocess

import httpx
import pytest

from openagent.core.agent import Agent
from openagent.core.tool import ToolContext, ToolRegistry
from openagent.core.types import ToolUseBlock
from openagent.runtime.permissions import call_risk, tool_risk
from openagent.tools import http as http_module
from openagent.tools.builtin import bash_background, bash_output, kill_shell
from openagent.tools.git import git_commit, git_diff, git_log, git_status
from openagent.tools.http import http_request
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


# --------------------------------------------------------------------- http


@pytest.fixture
def served(monkeypatch):
    """Route http_request through a mock transport; returns the requests seen."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/moved":
            return httpx.Response(302, headers={"location": "http://10.0.0.1/admin"})
        return httpx.Response(201, json={"ok": True}, headers={"x-noise": "1"})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        http_module.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    return seen


async def test_http_request_sends_method_headers_and_body(served):
    result = await http_request(
        "https://api.example.com/items", method="post", headers={"X-Key": "k"}, body='{"a": 1}'
    )

    assert result.startswith("HTTP 201 Created")
    assert "content-type: application/json" in result and "x-noise" not in result
    assert '{"ok":true}' in result
    request = served[0]
    assert (request.method, request.headers["x-key"], request.content) == ("POST", "k", b'{"a": 1}')


async def test_http_request_does_not_follow_a_redirect_into_the_private_network(served):
    result = await http_request("https://api.example.com/moved")
    assert result.startswith("HTTP 302")
    assert "location: http://10.0.0.1/admin" in result
    assert len(served) == 1


@pytest.mark.parametrize("url", ["http://192.168.1.1/", "http://10.0.0.5/x", "file:///etc/passwd", "http://nas.local/"])
async def test_http_request_refuses_private_and_non_http_urls(served, url):
    assert (await http_request(url)).startswith("Error")
    assert not served


async def test_http_request_rejects_bad_input(served):
    assert (await http_request("https://api.example.com", method="TRACE")).startswith("Error")
    assert (await http_request("https://api.example.com", body="x")).startswith("Error")
    assert not served


def test_http_request_risk_follows_the_method():
    assert call_risk("http_request", {"url": "https://x"}) == "network"
    assert call_risk("http_request", {"method": "head"}) == "network"
    assert call_risk("http_request", {"method": "DELETE"}) == "exec"


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
