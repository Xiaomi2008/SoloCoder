"""The agent-desktop tools, against a fake binary: what they send, and what they refuse."""
import json
import os
import stat

import pytest

from openagent.apps.solocoder.agent import build_solocoder_tools
from openagent.core.tool import ToolRegistry
from openagent.runtime.permissions import tool_risk
from openagent.tools import desktop
from openagent.tools.desktop import build_desktop_tools

SNAPSHOT = {"ok": True, "data": {"app": "Calculator", "snapshot_id": "s1", "tree": {"role": "window"}}}


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """A binary that logs its arguments and prints a canned envelope."""
    calls = tmp_path / "calls.jsonl"
    reply = tmp_path / "reply.json"
    reply.write_text(json.dumps(SNAPSHOT))
    binary = tmp_path / "agent-desktop"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(calls)!r}, 'a').write(json.dumps(sys.argv[1:]) + chr(10))\n"
        f"print(open({str(reply)!r}).read())\n"
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(desktop, "_binary", lambda: str(binary))

    class Fake:
        def sent(self):
            return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []

        def reply(self, envelope):
            reply.write_text(json.dumps(envelope))

    return Fake()


def by_name(tools):
    return {t._tool_name: t for t in tools}


async def test_snapshot_asks_for_the_app_and_returns_the_tree(fake_cli):
    tools = by_name(build_desktop_tools())
    out = await tools["desktop_snapshot"]("Calculator")
    assert fake_cli.sent() == [["snapshot", "--app", "Calculator", "--compact", "-i"]]
    assert json.loads(out)["snapshot_id"] == "s1"


async def test_a_failure_reads_as_an_error_with_the_suggestion(fake_cli):
    fake_cli.reply({"ok": False, "error": {"code": "PERM_DENIED", "message": "no access", "suggestion": "grant it"}})
    out = await by_name(build_desktop_tools())["desktop_snapshot"]("Calculator")
    assert out == "Error: PERM_DENIED: no access grant it"


async def test_a_missing_binary_says_how_to_install(monkeypatch):
    monkeypatch.setattr(desktop, "_binary", lambda: None)
    out = await by_name(build_desktop_tools())["desktop_snapshot"]("Calculator")
    assert "npm install -g agent-desktop" in out


async def test_scoped_tools_refuse_other_apps_without_running_anything(fake_cli):
    tools = by_name(build_desktop_tools(["Calculator"]))
    for call in (
        tools["desktop_snapshot"]("Mail"),
        tools["desktop_launch"]("Mail"),
        tools["desktop_press"]("return", "Mail"),
        tools["desktop_screenshot"]("Mail"),
    ):
        assert (await call).startswith("Error: 'Mail' is not an app")
    assert fake_cli.sent() == []


async def test_scoped_tools_take_only_refs_they_issued(fake_cli):
    tools = by_name(build_desktop_tools(["Calculator"]))
    assert "did not come from" in await tools["desktop_click"]("@s1:e1")
    assert fake_cli.sent() == []
    await tools["desktop_snapshot"]("Calculator")
    fake_cli.reply({"ok": True, "data": {"disposition": {"delivery": "delivered"}}})
    assert (await tools["desktop_click"]("@s1:e1")).startswith("Sent")
    assert "did not come from" in await tools["desktop_click"]("@other:e1")
    assert fake_cli.sent()[-1] == ["click", "@s1:e1"]


async def test_a_screenshot_names_the_window_and_reports_a_windowless_app(fake_cli):
    fake_cli.reply({"ok": True, "data": []})
    assert "no window" in await by_name(build_desktop_tools())["desktop_screenshot"]("Calculator")
    fake_cli.reply({"ok": True, "data": [{"id": "w-1"}]})
    await by_name(build_desktop_tools())["desktop_screenshot"]("Calculator")
    assert fake_cli.sent()[-1][:5] == ["screenshot", "--app", "Calculator", "--window-id", "w-1"]


def test_scoped_tools_offer_no_way_to_see_what_else_is_open():
    assert "desktop_list_apps" in by_name(build_desktop_tools())
    assert "desktop_list_apps" not in by_name(build_desktop_tools(["Calculator"]))


async def test_press_brings_the_app_forward_only_when_asked(fake_cli):
    tools = by_name(build_desktop_tools())
    fake_cli.reply({"ok": True, "data": {}})
    await tools["desktop_press"]("return", "Calculator")
    await tools["desktop_press"]("return", "Calculator", bring_forward=True)
    assert fake_cli.sent() == [["press", "return", "--app", "Calculator"],
                               ["press", "return", "--app", "Calculator", "--headed"]]


def test_tools_are_off_unless_asked_for_and_their_actions_need_permission():
    assert not {t.__name__ for t in build_solocoder_tools(computer_use=False)} & {"desktop_click"}
    names = {t._tool_name for t in build_solocoder_tools(computer_use=False, desktop_use=True)}
    assert {"desktop_snapshot", "desktop_click", "desktop_screenshot"} <= names
    for name in ("desktop_click", "desktop_type", "desktop_press", "desktop_launch"):
        assert tool_risk(name) == "gui"


def test_every_tool_has_a_schema():
    registry = ToolRegistry()
    for fn in build_desktop_tools():
        registry.register(fn)
    assert all(entry.description for entry in registry.definitions)
