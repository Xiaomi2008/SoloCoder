"""Browser use: the model drives its own Chromium window through Playwright.

Text first. ``browser_snapshot`` returns the page's text and a numbered list of
the elements the model can act on; the action tools take those numbers
(``ref``), not pixel coordinates, so a model without vision can browse too.
``browser_screenshot`` adds the rendered page as an image for vision models.

The browser is a separate profile in its own window: it has none of the user's
cookies or sign-ins, and it cannot reach their Chrome tabs. Navigation is
limited to http(s) on the public internet and this machine (``localhost``, for
a dev server under test); ``file://`` and LAN addresses are refused, so a page
the model reads cannot steer it at a router or a NAS.

Requires ``pip install 'openagent[browser]'`` and ``playwright install chromium``.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import time
import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from ..core.tool import tool

VIEWPORT = {"width": 1280, "height": 800}
MAX_ELEMENTS = 150
MAX_TEXT_CHARS = 6000
ACTION_TIMEOUT_MS = 10_000
DEFAULT_PROFILE_DIR = Path.home() / ".openagent" / "browser-profile"


@dataclass(frozen=True)
class SnapshotLimits:
    max_elements: int = MAX_ELEMENTS
    max_text_chars: int = MAX_TEXT_CHARS


#: A Hacker News snapshot at the full limits is ~3,000 tokens: nothing for a
#: cloud context, a few pages and a 32k local context is full.
COMPACT_LIMITS = SnapshotLimits(max_elements=60, max_text_chars=1500)

# Per run, not per process: the service sets it before a run's task starts, so
# a local model's run and a cloud model's run on one service do not share it.
snapshot_limits: contextvars.ContextVar[SnapshotLimits] = contextvars.ContextVar(
    "browser_snapshot_limits", default=SnapshotLimits()
)

# Where screencast frames go: the service sets one per run, so the app shows
# the page live. A frame is a dict with data (base64 JPEG), url, title, width,
# height. Read at each tool call, because the screencast's own callbacks run
# outside any run's context.
frame_sink: contextvars.ContextVar[Callable[[dict[str, Any]], None] | None] = contextvars.ContextVar(
    "browser_frame_sink", default=None
)
FRAME_INTERVAL_S = 0.25  # at most 4 frames a second reach the client
SCREENCAST = {"format": "jpeg", "quality": 60, "maxWidth": 960, "maxHeight": 600, "everyNthFrame": 1}

# A persistent profile keeps sign-ins between runs; None is a fresh profile
# every launch. Process-wide: there is one browser.
_profile_dir: Path | None = None

# Which browser Playwright drives: None is its bundled Chrome for Testing;
# "chrome" is the user's installed Google Chrome — the ordinary browser, still
# with a profile of its own, never the user's.
_channel: str | None = None
CHANNELS = ("chromium", "chrome")


def configure_browser(profile_dir: str | Path | None = None, channel: str | None = None) -> None:
    """Keep the profile in ``profile_dir`` (sign-ins persist); drive ``channel``."""
    global _profile_dir, _channel
    _profile_dir = Path(profile_dir).expanduser() if profile_dir else None
    if channel not in (None, *CHANNELS):
        raise ValueError(f"browser channel must be one of {', '.join(CHANNELS)}, not {channel!r}")
    _channel = None if channel in (None, "chromium") else channel
NAVIGATION_TIMEOUT_MS = 30_000

# The last capture's JPEG, base64. The agent loop attaches it to the next turn
# as an image, as it does for the desktop `screenshot` tool.
_last_screenshot_base64: str | None = None

# Tags every visible, actionable element with `data-oa-ref` and describes it.
# Refs are renumbered by every snapshot; an action on a stale ref fails loudly.
_SNAPSHOT_JS = """
(maxElements) => {
  document.querySelectorAll('[data-oa-ref]').forEach(el => el.removeAttribute('data-oa-ref'));
  const selector = 'a[href], button, input:not([type=hidden]), select, textarea, summary, '
    + '[role=button], [role=link], [role=checkbox], [role=radio], [role=tab], [role=menuitem], '
    + '[role=option], [role=switch], [role=combobox], [role=textbox], [contenteditable=""], '
    + '[contenteditable=true], [onclick]';
  const visible = el => {
    if (!el.getClientRects().length) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
  // A logo link has no text of its own; its image's alt or its SVG's title names it.
  const pictured = el => {
    const img = el.querySelector('img[alt]:not([alt=""])');
    if (img) return img.alt;
    const title = el.querySelector('svg title');
    return title ? title.textContent : '';
  };
  // Labels that name a consequential action: sending, buying, deleting,
  // signing in. A click on one asks; following a link or ticking a box does not.
  const risky = /\\b(submit|send|post|publish|reply|comment|buy|purchase|order|checkout|check out|pay|donate|subscribe|unsubscribe|delete|remove|destroy|discard|confirm|sign in|sign up|log in|login|register|transfer|book|reserve|apply|save|accept|agree|upload|install|share)\\b/i;
  // Submitting a POST form, or any form with a password, changes something;
  // a GET form (a search box) is a navigation.
  const formChanges = f => !!f && ((f.getAttribute('method') || 'get').toLowerCase() === 'post' || !!f.querySelector('input[type=password]'));
  const out = [];
  const policy = {};
  let n = 0;
  for (const el of document.querySelectorAll(selector)) {
    if (n >= maxElements) break;
    if (!visible(el) || el.disabled) continue;
    n += 1;
    el.setAttribute('data-oa-ref', String(n));
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || (tag === 'input' ? `input:${el.type || 'text'}` : tag);
    const name = clean(el.getAttribute('aria-label') || (tag === 'select' ? el.getAttribute('name') : el.innerText) || el.value
      || el.getAttribute('placeholder') || el.getAttribute('title') || pictured(el)
      || el.getAttribute('name') || el.getAttribute('alt'));
    const extra = [];
    if (tag === 'a') extra.push(clean(el.getAttribute('href')));
    if ((tag === 'input' || tag === 'textarea') && el.value && el.type !== 'password') extra.push(`value="${clean(el.value)}"`);
    if (el.type === 'password') extra.push('password');
    if (el.checked) extra.push('checked');
    if (tag === 'select') extra.push(`selected="${clean(el.selectedOptions[0] && el.selectedOptions[0].text)}"`, 'options: ' + [...el.options].slice(0, 15).map(o => clean(o.text)).join(' | '));
    out.push(`[${n}] ${role} "${name}"` + (extra.length ? ` (${extra.join(', ')})` : ''));
    const submits = (tag === 'button' && (el.getAttribute('type') || 'submit') === 'submit' && el.form)
      || (tag === 'input' && ['submit', 'image'].includes(el.type));
    const toggles = tag === 'input' && ['checkbox', 'radio'].includes(el.type);
    const fillable = (tag === 'input' && !submits && !toggles) || tag === 'textarea' || tag === 'select'
      || el.isContentEditable || ['textbox', 'combobox'].includes(el.getAttribute('role'));
    policy[String(n)] = {
      // Unlabelled buttons ask: nothing says what they do.
      click: risky.test(name) || (submits && formChanges(el.form)) || (!name && tag !== 'a' && !toggles && !fillable),
      // Enter in a field outside any form is how chat boxes send.
      submit: el.form ? formChanges(el.form) : true,
    };
  }
  const total = document.querySelectorAll(selector).length;
  return {elements: out, policy, truncated: total > n && n >= maxElements, text: document.body ? document.body.innerText : ''};
}
"""


class BrowserUnavailable(RuntimeError):
    pass


def check_url(url: str) -> str | None:
    """Why ``url`` may not be opened, or ``None`` when it may."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return f"only http and https URLs can be opened, not '{parts.scheme or url}'"
    host = (parts.hostname or "").lower()
    if not host:
        return f"'{url}' has no host"
    if host == "localhost" or host.endswith(".localhost"):
        return None
    if host.endswith(".local") or host.endswith(".lan") or host.endswith(".internal"):
        return f"'{host}' is a local-network name; only public sites and localhost are allowed"
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None
    if address.is_loopback:
        return None
    if address.is_private or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified:
        return f"'{host}' is a private-network address; only public sites and localhost are allowed"
    return None


class BrowserSession:
    """One Chromium, one context, one page — started on first use."""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._closed = False
        self.title = ""
        self._sink: Callable[[dict[str, Any]], None] | None = None
        self._cast_page: Any = None
        self._cdp: Any = None
        self._last_frame_at = 0.0
        self._pending: dict[str, Any] | None = None
        self._flush: asyncio.TimerHandle | None = None

    @property
    def headless(self) -> bool:
        return os.environ.get("OPENAGENT_BROWSER_HEADLESS", "").lower() in ("1", "true", "yes")

    async def page(self) -> Any:
        if self._page is not None and not self._page.is_closed():
            return self._page
        if self._context is None or self._closed:
            await self._launch()
        # A persistent profile opens with a tab of its own; use it.
        open_pages = [p for p in self._context.pages if not p.is_closed()]
        self._page = open_pages[0] if open_pages else await self._context.new_page()
        return self._page

    async def _launch(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserUnavailable(
                "browser tools need Playwright: pip install 'openagent[browser]' && playwright install chromium"
            ) from exc
        await self.close()
        self._playwright = await async_playwright().start()
        try:
            if _profile_dir is not None:
                _profile_dir.mkdir(parents=True, exist_ok=True)
                self._context = await self._playwright.chromium.launch_persistent_context(
                    str(_profile_dir), headless=self.headless, viewport=VIEWPORT, channel=_channel
                )
            else:
                self._browser = await self._playwright.chromium.launch(headless=self.headless, channel=_channel)
                self._context = await self._browser.new_context(viewport=VIEWPORT)
        except Exception as exc:
            await self.close()
            hint = (
                "is Google Chrome installed in /Applications?" if _channel == "chrome"
                else "run: playwright install chromium"
            )
            raise BrowserUnavailable(f"could not start {'Google Chrome' if _channel else 'Chromium'} ({exc}); {hint}") from exc
        self._closed = False
        # The user closing the window ends the context; the next call relaunches.
        self._context.on("close", lambda *_: setattr(self, "_closed", True))
        self._context.set_default_timeout(ACTION_TIMEOUT_MS)
        self._context.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
        # Links and redirects are navigations the model did not type; hold them
        # to the same rule as browser_navigate.
        await self._context.route("**/*", _guard_documents)

    async def close(self) -> None:
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._playwright, "stop", None),
        ):
            if closer is None:
                continue
            try:
                await closer()
            except Exception:
                pass
        self._playwright = self._browser = self._context = self._page = None
        self._cast_page = self._cdp = None
        if self._flush is not None:
            self._flush.cancel()
        self._flush = self._pending = None

    # ------------------------------------------------------------ screencast

    async def stream_to(self, sink: Callable[[dict[str, Any]], None] | None, page: Any) -> None:
        """Send ``page``'s frames to ``sink`` from now on.

        Chromium screencasts only when the page changes, so an idle page costs
        nothing. Best effort: a client without frames still has the snapshots.
        """
        self._sink = sink
        if sink is None or self._cast_page is page:
            return
        try:
            if self._cdp is not None:
                try:
                    await self._cdp.detach()
                except Exception:
                    pass
            self._cdp = await self._context.new_cdp_session(page)
            self._cdp.on("Page.screencastFrame", self._on_frame)
            await self._cdp.send("Page.startScreencast", SCREENCAST)
            self._cast_page = page
        except Exception:
            self._cdp = self._cast_page = None

    def _on_frame(self, params: dict[str, Any]) -> None:
        cdp = self._cdp
        if cdp is not None:
            # Unacknowledged, Chromium stops sending.
            asyncio.ensure_future(_quietly(cdp.send("Page.screencastFrameAck", {"sessionId": params.get("sessionId")})))
        meta = params.get("metadata") or {}
        page = self._cast_page
        self._pending = {
            "data": params.get("data", ""),
            "url": page.url if page is not None else "",
            "title": self.title,
            "width": int(meta.get("deviceWidth") or VIEWPORT["width"]),
            "height": int(meta.get("deviceHeight") or VIEWPORT["height"]),
        }
        wait = FRAME_INTERVAL_S - (time.monotonic() - self._last_frame_at)
        if wait <= 0:
            self._emit()
        elif self._flush is None:
            # Throttled, not dropped: the newest frame goes out when the window
            # opens, so the last state of the page always reaches the client.
            self._flush = asyncio.get_running_loop().call_later(wait, self._emit)

    def _emit(self) -> None:
        self._flush = None
        frame, self._pending = self._pending, None
        if frame is None or self._sink is None:
            return
        self._last_frame_at = time.monotonic()
        try:
            self._sink(frame)
        except Exception:
            pass

    async def show(self) -> dict[str, Any]:
        """Bring the agent's window forward for the user to take over."""
        if self._page is None or self._page.is_closed():
            return {"open": False, "headless": self.headless, "url": "", "pid": 0}
        try:
            await self._page.bring_to_front()
        except Exception:
            pass
        return {"open": True, "headless": self.headless, "url": self._page.url, "pid": _browser_pid()}


def _browser_pid() -> int:
    """The agent's browser app process: a descendant of this one.

    The app activates it by pid. By bundle id it would also raise the user's
    own Chrome windows once the channel is "chrome" — same bundle, other
    profile.
    """
    import subprocess

    try:
        rows = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,comm="], capture_output=True, text=True, timeout=5
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return 0
    parent: dict[int, int] = {}
    command: dict[int, str] = {}
    for row in rows:
        parts = row.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            parent[int(parts[0])], command[int(parts[0])] = int(parts[1]), parts[2]
    me = os.getpid()

    def descends(pid: int) -> bool:
        for _ in range(64):
            pid = parent.get(pid, 0)
            if pid == me:
                return True
            if pid <= 1:
                return False
        return False

    for pid, comm in command.items():
        # The app's main executable, not its Helper processes.
        if comm.endswith(("/MacOS/Google Chrome", "/MacOS/Google Chrome for Testing", "/MacOS/Chromium")) and descends(pid):
            return pid
    return 0


async def _quietly(awaitable: Any) -> None:
    try:
        await awaitable
    except Exception:
        pass


# ref -> {"click": asks, "submit": asks} from the latest snapshot. The
# permission gate reads it to ask only for consequential actions.
_ref_policy: dict[str, dict[str, bool]] = {}

# The last document navigation the guard refused, as (url, reason). The action
# that caused it reports it; a bare chrome-error page tells the model nothing.
_blocked: tuple[str, str] | None = None


async def _guard_documents(route: Any) -> None:
    global _blocked
    request = route.request
    refused = check_url(request.url) if request.resource_type == "document" else None
    if refused:
        _blocked = (request.url, refused)
        await route.abort("blockedbyclient")
    else:
        await route.continue_()


_session = BrowserSession()


def last_screenshot_base64() -> str | None:
    return _last_screenshot_base64


async def close_browser() -> None:
    await _session.close()


async def show_browser() -> dict[str, Any]:
    return await _session.show()


async def _page() -> Any:
    """The agent's page, streaming to this run's sink."""
    page = await _session.page()
    await _session.stream_to(frame_sink.get(), page)
    return page


def call_risk(name: str, arguments: dict[str, Any]) -> str | None:
    """The risk of one browser call, from what the latest snapshot knows.

    ``browser`` asks in ask mode, ``network`` does not. Acting on a page asks
    only when it is consequential: submitting a form that changes something,
    a control labelled like send/buy/delete/sign in, an unlabelled button, or
    Enter. A ref the snapshot does not know asks — fail closed. ``None`` means
    the tool's static class applies.
    """

    def by_ref(kind: str) -> str:
        ref = str(arguments.get("ref", "")).strip().strip("[]")
        policy = _ref_policy.get(ref)
        return "browser" if policy is None or policy.get(kind, True) else "network"

    if name == "browser_click":
        return by_ref("click")
    if name == "browser_type":
        submit = arguments.get("submit", False)
        if isinstance(submit, str):
            submit = submit.strip().lower() in ("1", "true", "yes")
        return by_ref("submit") if submit else "network"
    if name == "browser_select_option":
        return "network"
    if name == "browser_press_key":
        return "browser" if "enter" in str(arguments.get("key", "")).lower() else "network"
    return None


async def _snapshot(page: Any) -> str:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5_000)
    except Exception:
        pass
    limits = snapshot_limits.get()
    data = await _scan(page, limits.max_elements)
    _session.title = await page.title()
    text = " ".join((data.get("text") or "").split())
    if len(text) > limits.max_text_chars:
        text = text[: limits.max_text_chars] + " …[page text truncated; browser_find searches all of it]"
    elements = data.get("elements") or []
    lines = [f"URL: {page.url}", f"Title: {_session.title}", "", "Elements (use the [n] as ref):"]
    lines += elements or ["(none)"]
    if data.get("truncated"):
        lines.append(f"…only the first {limits.max_elements} elements are listed; browser_find reaches the rest")
    lines += ["", "Page text:", text or "(empty)"]
    return "\n".join(lines)


async def _scan(page: Any, max_elements: int) -> dict[str, Any]:
    """Tag the page's elements and record which refs are consequential."""
    global _ref_policy
    data = await page.evaluate(_SNAPSHOT_JS, max_elements)
    _ref_policy = data.get("policy") or {}
    return data


async def _element(page: Any, ref: int | str) -> Any:
    ref = str(ref).strip().strip("[]")
    locator = page.locator(f'[data-oa-ref="{ref}"]')
    if await locator.count() == 0:
        raise ValueError(f"no element [{ref}] on this page; call browser_snapshot for current refs")
    return locator.first


async def _run(action: Any) -> str:
    """Run an action and answer with the page it leaves, or a one-line error."""
    global _blocked
    try:
        page = await _page()
        _blocked = None
        await action(page)
        if _blocked is not None:
            url, reason = _blocked
            if page.url.startswith("chrome-error://"):
                await page.go_back(wait_until="domcontentloaded")
            return f"Error: navigation to {url} was blocked: {reason}"
        return await _snapshot(page)
    except BrowserUnavailable as exc:
        return f"Error: {exc}"
    except Exception as exc:
        message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return f"Error: {message}"


@tool
async def browser_navigate(url: str) -> str:
    """Open a URL in the agent's browser and return the page snapshot (text plus numbered elements).

    Args:
        url: http(s) URL; public sites and localhost only.
    """
    if "://" not in url:
        url = f"https://{url}"
    refused = check_url(url)
    if refused:
        return f"Error: {refused}"

    async def go(page: Any) -> None:
        await page.goto(url, wait_until="domcontentloaded")

    return await _run(go)


@tool
async def browser_snapshot() -> str:
    """Return the current page: URL, title, numbered actionable elements, and visible text."""

    async def noop(page: Any) -> None:
        if page.url == "about:blank":
            raise ValueError("no page is open; call browser_navigate first")

    return await _run(noop)


@tool
async def browser_find(text: str) -> str:
    """Search the whole current page for text; returns matching elements (with refs) and text excerpts.

    Args:
        text: Words to look for, case-insensitive.
    """
    needle = text.strip().lower()
    if not needle:
        return "Error: text is required"
    try:
        page = await _page()
        if page.url == "about:blank":
            return "Error: no page is open; call browser_navigate first"
        data = await _scan(page, 10_000)
    except BrowserUnavailable as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}"

    elements = [line for line in data.get("elements") or [] if needle in line.lower()][:30]
    body = " ".join((data.get("text") or "").split())
    excerpts, start = [], 0
    while len(excerpts) < 8:
        at = body.lower().find(needle, start)
        if at < 0:
            break
        excerpts.append("…" + body[max(0, at - 120): at + len(needle) + 120] + "…")
        start = at + len(needle)
    if not elements and not excerpts:
        return f"No match for '{text}' on {page.url}"
    lines = [f"Matches for '{text}' on {page.url}:", "", "Elements:", *(elements or ["(none)"])]
    lines += ["", "Text:", *(excerpts or ["(none)"])]
    return "\n".join(lines)


@tool
async def browser_click(ref: int) -> str:
    """Click an element by its [n] ref from the latest snapshot. Returns the new snapshot.

    Args:
        ref: Element number from browser_snapshot.
    """

    async def click(page: Any) -> None:
        await (await _element(page, ref)).click()
        await page.wait_for_timeout(500)

    return await _run(click)


@tool
async def browser_type(ref: int, text: str, submit: bool = False) -> str:
    """Replace the text in an input by its [n] ref. Returns the new snapshot.

    Args:
        ref: Element number from browser_snapshot.
        text: Text to enter (replaces the current value).
        submit: Press Enter afterwards.
    """

    async def fill(page: Any) -> None:
        element = await _element(page, ref)
        if (await element.get_attribute("type") or "").lower() == "password":
            # A model that types a password learned it from somewhere it should
            # not have — a page, a file — and a remembered profile makes that worse.
            raise ValueError(
                f"[{ref}] is a password field; the user types passwords themselves in the browser window"
            )
        await element.fill(text)
        if submit:
            await element.press("Enter")
            await page.wait_for_timeout(800)

    return await _run(fill)


@tool
async def browser_select_option(ref: int, option: str) -> str:
    """Choose an option in a <select> by its [n] ref. Returns the new snapshot.

    Args:
        ref: Element number from browser_snapshot.
        option: The option's visible label or value.
    """

    async def select(page: Any) -> None:
        element = await _element(page, ref)
        try:
            await element.select_option(label=option)
        except Exception:
            await element.select_option(value=option)

    return await _run(select)


@tool
async def browser_press_key(key: str) -> str:
    """Press a key on the page, e.g. Enter, Escape, Tab, PageDown, ArrowDown. Returns the new snapshot.

    Args:
        key: Playwright key name.
    """

    async def press(page: Any) -> None:
        await page.keyboard.press(key)
        await page.wait_for_timeout(300)

    return await _run(press)


@tool
async def browser_back() -> str:
    """Go back one page in the browser history. Returns the new snapshot."""

    async def back(page: Any) -> None:
        await page.go_back(wait_until="domcontentloaded")

    return await _run(back)


@tool
async def browser_screenshot() -> str:
    """Capture the visible page as an image (vision models). Elements are still acted on by ref."""
    global _last_screenshot_base64
    try:
        page = await _page()
        data = await page.screenshot(type="jpeg", quality=70)
    except Exception as exc:
        return f"Error: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}"
    _last_screenshot_base64 = base64.b64encode(data).decode("ascii")
    return f"Screenshot captured of {page.url}"


@tool
async def browser_close() -> str:
    """Close the agent's browser window."""
    await _session.close()
    return "Browser closed"


browser_tools = [
    browser_navigate,
    browser_snapshot,
    browser_find,
    browser_click,
    browser_type,
    browser_select_option,
    browser_press_key,
    browser_back,
    browser_screenshot,
    browser_close,
]
