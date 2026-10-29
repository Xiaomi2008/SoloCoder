"""Browser use: URL policy, risk classes, wiring, and a real Chromium round-trip."""

from __future__ import annotations

import asyncio
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from openagent.apps.solocoder.agent import build_solocoder_tools
from openagent.core.session import Session
from openagent.core.types import ToolResultBlock
from openagent.runtime.loop import add_screenshot_followup
from openagent.runtime.permissions import ASK_BY_DEFAULT, tool_risk
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
        assert "[3] input:text" in page and "[4] select \"size\"" in page

        await browser.browser_type(ref=3, text="shoes")
        await browser.browser_select_option(ref=4, option="Large")
        done = await browser.browser_click(ref=5)
        assert "next.html?q=shoes&size=Large" in done and "arrived" in done

        await browser.browser_back()
        blocked = await browser.browser_click(ref=2)
        assert blocked.startswith("Error: navigation to http://192.168.1.1/ was blocked")
        assert "Shop" in await browser.browser_snapshot()

        assert "no element [99]" in await browser.browser_click(ref=99)
        assert (await browser.browser_screenshot()).startswith("Screenshot captured")
        assert browser.last_screenshot_base64()
    finally:
        await asyncio.wait_for(browser.close_browser(), 10)
