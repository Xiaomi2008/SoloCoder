"""Browser use: URL policy, risk classes, wiring, and a real Chromium round-trip."""

from __future__ import annotations

import asyncio
import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from openagent.apps.solocoder.agent import build_solocoder_tools
from openagent.core.session import Session
from openagent.core.types import ToolResultBlock
from openagent.runtime.loop import add_screenshot_followup
from openagent.runtime.permissions import ASK_BY_DEFAULT, call_risk, tool_risk
from openagent.tools import browser
from openagent.tools.browser import browser_tools, check_url


@pytest.mark.parametrize(
    "url",
    ["https://example.com/a", "http://localhost:3000", "http://127.0.0.1:8000/x", "http://app.localhost"],
)
def test_public_sites_and_this_machine_are_allowed(url):
    assert check_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://192.168.1.1/",
        "http://10.0.0.5",
        "http://172.16.0.1",
        "http://169.254.169.254/latest/meta-data",
        "http://nas.local",
        "http://[fd00::1]/",
    ],
)
def test_other_schemes_and_the_lan_are_refused(url):
    assert check_url(url)


def test_acting_on_a_page_asks_and_reading_does_not():
    for name in ("browser_click", "browser_type", "browser_select_option", "browser_press_key"):
        assert tool_risk(name) in ASK_BY_DEFAULT, name
    for name in ("browser_navigate", "browser_snapshot", "browser_screenshot", "browser_back", "browser_close"):
        assert tool_risk(name) not in ASK_BY_DEFAULT, name


def test_browser_tools_are_opt_in():
    names = lambda tools: {t.__name__ for t in tools}  # noqa: E731
    assert not names(build_solocoder_tools(computer_use=False)) & names(browser_tools)
    assert names(browser_tools) <= names(build_solocoder_tools(computer_use=False, browser_use=True))


def test_a_browser_screenshot_is_attached_as_an_image(monkeypatch):
    monkeypatch.setattr(browser, "_last_screenshot_base64", "aW1hZ2U=")
    session = Session()
    result = ToolResultBlock(tool_use_id="c1", tool_name="browser_screenshot", content="Screenshot captured of x")
    assert add_screenshot_followup(session, [result]) is True
    assert session.messages[-1].has_images


# ------------------------------------------------------------- live Chromium

_PAGE = b"""<html><head><title>Shop</title></head><body>
<a href="/"><img src="logo.png" alt="Shop home"></a>
<a href="/next.html">Next</a> <a href="http://192.168.1.1/">router</a>
<form action="/next.html"><input name="q" placeholder="Search">
<select name="size"><option>Small</option><option>Large</option></select>
<button type="submit">Find</button></form></body></html>"""


@pytest.fixture
def site(tmp_path):
    (tmp_path / "index.html").write_bytes(_PAGE)
    (tmp_path / "next.html").write_bytes(b"<html><head><title>Next</title></head><body>arrived</body></html>")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


async def test_a_form_round_trip_in_real_chromium(site, monkeypatch):
    pytest.importorskip("playwright")
    monkeypatch.setenv("OPENAGENT_BROWSER_HEADLESS", "1")
    try:
        page = await browser.browser_navigate(f"{site}/index.html")
        if page.startswith("Error: could not start Chromium"):
            pytest.skip("Chromium is not installed (playwright install chromium)")
        assert '[1] a "Shop home"' in page, "an image-only link is named by its alt text"
        assert "[4] input:text" in page and "[5] select \"size\"" in page

        await browser.browser_type(ref=4, text="shoes")
        await browser.browser_select_option(ref=5, option="Large")
        done = await browser.browser_click(ref=6)
        assert "next.html?q=shoes&size=Large" in done and "arrived" in done

        await browser.browser_back()
        blocked = await browser.browser_click(ref=3)
        assert blocked.startswith("Error: navigation to http://192.168.1.1/ was blocked")
        assert "Shop" in await browser.browser_snapshot()

        assert "no element [99]" in await browser.browser_click(ref=99)
        assert (await browser.browser_screenshot()).startswith("Screenshot captured")
        assert browser.last_screenshot_base64()
    finally:
        await asyncio.wait_for(browser.close_browser(), 10)


# ------------------------------------------------------- asking less often

def test_page_actions_ask_only_when_consequential(monkeypatch):
    monkeypatch.setattr(browser, "_ref_policy", {
        "1": {"click": False, "submit": False},  # a link
        "2": {"click": True, "submit": True},  # "Place order"
        "3": {"click": False, "submit": False},  # a search box in a GET form
        "4": {"click": False, "submit": True},  # a chat box outside any form
    })
    assert call_risk("browser_click", {"ref": 1}) == "network"
    assert call_risk("browser_click", {"ref": "[2]"}) in ASK_BY_DEFAULT
    assert call_risk("browser_click", {"ref": 99}) in ASK_BY_DEFAULT, "an unknown ref fails closed"
    assert call_risk("browser_type", {"ref": 3, "text": "shoes"}) == "network"
    assert call_risk("browser_type", {"ref": 3, "text": "shoes", "submit": True}) == "network"
    assert call_risk("browser_type", {"ref": 4, "text": "hi", "submit": "true"}) in ASK_BY_DEFAULT
    assert call_risk("browser_select_option", {"ref": 3, "option": "x"}) == "network"
    assert call_risk("browser_press_key", {"key": "PageDown"}) == "network"
    assert call_risk("browser_press_key", {"key": "Enter"}) in ASK_BY_DEFAULT
    assert call_risk("bash", {"command": "ls"}) == "exec", "other tools keep their static class"


_POLICY_PAGE = b"""<html><head><title>Policy</title></head><body>
<a href="/next.html">Read more</a>
<form method="get" action="/next.html"><input name="q"><button>Search</button></form>
<form method="post" action="/next.html"><input name="card"><button>Go</button></form>
<button onclick="x()">Delete account</button>
<button onclick="x()"><svg width="9" height="9"></svg></button>
<label><input type="checkbox"> Remember me</label>
<textarea placeholder="Message"></textarea>
</body></html>"""


async def test_the_snapshot_marks_what_is_consequential(site, tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    monkeypatch.setenv("OPENAGENT_BROWSER_HEADLESS", "1")
    (tmp_path / "policy.html").write_bytes(_POLICY_PAGE)
    try:
        page = await browser.browser_navigate(f"{site}/policy.html")
        if page.startswith("Error: could not start Chromium"):
            pytest.skip("Chromium is not installed (playwright install chromium)")
        refs = {line.split("]", 1)[1].strip(): line[1:].split("]", 1)[0] for line in page.splitlines() if line.startswith("[")}
        ref = lambda label: next(r for line, r in refs.items() if label in line)  # noqa: E731
        asks = lambda tool, **kw: call_risk(tool, kw) in ASK_BY_DEFAULT  # noqa: E731

        assert not asks("browser_click", ref=ref('"Read more"')), "following a link"
        assert not asks("browser_click", ref=ref('"Search"')), "a GET form is a navigation"
        assert asks("browser_click", ref=ref('"Go"')), "a POST form's submit changes something"
        assert asks("browser_click", ref=ref('"Delete account"')), "a risky label"
        assert asks("browser_click", ref=ref('button ""')), "an unlabelled button"
        assert not asks("browser_click", ref=ref("input:checkbox")), "ticking a box"
        assert not asks("browser_type", ref=ref('"Message"'), text="hi"), "typing is not sending"
        assert asks("browser_type", ref=ref('"Message"'), text="hi", submit=True), "Enter outside a form sends"
    finally:
        await asyncio.wait_for(browser.close_browser(), 10)


# ------------------------------------------- local limits, find, profile

_LONG_PAGE = (
    b"<html><head><title>Long</title></head><body>"
    + b"".join(b'<a href="/p%d">item %d</a> filler text here. ' % (i, i) for i in range(200))
    + b"<p>the needle is at the very end</p>"
    + b'<form method="post"><input type="password" name="pw"></form></body></html>'
)


@pytest.fixture
def chromium(monkeypatch):
    pytest.importorskip("playwright")
    monkeypatch.setenv("OPENAGENT_BROWSER_HEADLESS", "1")


async def _open(url):
    page = await browser.browser_navigate(url)
    if page.startswith("Error: could not start Chromium"):
        pytest.skip("Chromium is not installed (playwright install chromium)")
    return page


async def test_a_local_run_gets_a_compact_snapshot_and_find_reaches_the_rest(site, tmp_path, chromium):
    (tmp_path / "long.html").write_bytes(_LONG_PAGE)
    token = browser.snapshot_limits.set(browser.COMPACT_LIMITS)
    try:
        page = await _open(f"{site}/long.html")
        assert "only the first 60 elements" in page and "browser_find" in page
        assert "needle" not in page, "the text past the compact limit is cut"
        assert len(page) < 6000

        found = await browser.browser_find("needle")
        assert "the needle is at the very end" in found
        found = await browser.browser_find("item 199")
        assert '"item 199"' in found, "elements past the snapshot's cut are reachable by ref"
        assert "No match" in await browser.browser_find("zebra")
    finally:
        browser.snapshot_limits.reset(token)
        await asyncio.wait_for(browser.close_browser(), 10)


async def test_the_agent_does_not_type_passwords(site, tmp_path, chromium):
    (tmp_path / "long.html").write_bytes(_LONG_PAGE)
    try:
        await _open(f"{site}/long.html")
        found = await browser.browser_find("password")  # past the snapshot's 150-element cap
        ref = next(line[1:].split("]", 1)[0] for line in found.splitlines() if "input:password" in line)
        refused = await browser.browser_type(ref=int(ref), text="hunter2")
        assert refused.startswith("Error:") and "password field" in refused
    finally:
        await asyncio.wait_for(browser.close_browser(), 10)


async def _cookie_survives_a_restart(site) -> bool:
    await _open(f"{site}/index.html")
    page = await browser._session.page()
    await page.evaluate("document.cookie = 'signed_in=yes; max-age=3600; path=/'")
    await browser.close_browser()
    await _open(f"{site}/index.html")
    page = await browser._session.page()
    return "signed_in=yes" in await page.evaluate("document.cookie")


async def test_sign_ins_persist_only_with_a_profile(site, tmp_path, chromium):
    try:
        assert not await _cookie_survives_a_restart(site), "by default every launch starts signed out"
        browser.configure_browser(tmp_path / "profile")
        await browser.close_browser()
        assert await _cookie_survives_a_restart(site), "a profile keeps the sign-in"
    finally:
        browser.configure_browser(None)
        await asyncio.wait_for(browser.close_browser(), 10)


# ------------------------------------------------------ live view in the app

async def test_a_run_streams_browser_frames_to_its_client(site, tmp_path, chromium):
    """Screencast -> relay -> socket -> `browser_frame` events, while the agent browses."""
    import base64
    import tempfile
    from pathlib import Path

    from openagent.service.client import ServiceClient
    from openagent.service.server import AgentService, ServiceSettings
    from tests.support.streaming_provider import ScriptedStreamProvider, text_turn, tool_turn

    def provider(ref):
        return ScriptedStreamProvider([
            tool_turn("browser_navigate", {"url": f"{site}/index.html"}),
            tool_turn("browser_click", {"ref": 2}, id="call_2"),  # "Next": a link, so no prompt
            text_turn("done"),
        ])

    sock_dir = Path(tempfile.mkdtemp(prefix="oa-"))
    settings = ServiceSettings(
        socket_path=sock_dir / "b.sock", tools=list(browser_tools), provider_factory=provider,
        browser_use=True, discover_on_start=False,
    )
    service = AgentService(settings)
    await service.start()
    try:
        client = await ServiceClient.connect(settings.socket_path, client="pytest")
        workdir = tmp_path / "w"
        workdir.mkdir()
        started = await client.start_run("go", workdir)
        async with asyncio.timeout(60):
            events = [e async for e in client.events()]
        if any("could not start Chromium" in str(getattr(e, "result", "")) for e in events):
            pytest.skip("Chromium is not installed (playwright install chromium)")

        assert events[-1].type == "run_completed"
        frames = [e for e in events if e.type == "browser_frame"]
        assert frames, "the page was streamed"
        assert all(f.run_id == started["run_id"] for f in frames)
        assert base64.b64decode(frames[0].data)[:2] == b"\xff\xd8", "a JPEG"
        assert frames[-1].url.endswith("/next.html"), "the newest frame shows where the agent ended up"
        assert len(frames) <= 4 * 6, "throttled to a few per second"

        shown = await client.call("browser.show", {})
        assert shown["open"] is True and shown["url"].endswith("/next.html")
        await client.close()
    finally:
        await service.stop()
        shutil.rmtree(sock_dir, ignore_errors=True)


# ------------------------------------------------- the installed Google Chrome

async def test_the_chrome_channel_drives_the_installed_chrome(site, chromium):
    import os
    import subprocess
    from pathlib import Path

    app = Path("/Applications/Google Chrome.app/Contents/Info.plist")
    if not app.exists():
        pytest.skip("Google Chrome is not installed")
    installed = subprocess.run(
        ["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleShortVersionString", str(app)],
        capture_output=True, text=True,
    ).stdout.strip()
    browser.configure_browser(channel="chrome")
    try:
        await _open(f"{site}/index.html")
        page = await browser._session.page()
        assert page.context.browser.version == installed, "the user's Chrome, not Chrome for Testing"

        shown = await browser.show_browser()
        assert shown["pid"] > 0, "take-over can find the agent's browser"
        comm = subprocess.run(["ps", "-o", "comm=", "-p", str(shown["pid"])], capture_output=True, text=True).stdout
        assert comm.strip().endswith("/MacOS/Google Chrome")
        assert shown["pid"] != os.getpid()
    finally:
        browser.configure_browser(None)
        await asyncio.wait_for(browser.close_browser(), 10)


def test_an_unknown_channel_is_refused():
    with pytest.raises(ValueError, match="chromium, chrome"):
        browser.configure_browser(channel="firefox")
