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

import base64
import ipaddress
import os
from typing import Any
from urllib.parse import urlsplit

from ..core.tool import tool

VIEWPORT = {"width": 1280, "height": 800}
MAX_ELEMENTS = 150
MAX_TEXT_CHARS = 6000
ACTION_TIMEOUT_MS = 10_000
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
  const out = [];
  let n = 0;
  for (const el of document.querySelectorAll(selector)) {
    if (n >= maxElements) break;
    if (!visible(el) || el.disabled) continue;
    n += 1;
    el.setAttribute('data-oa-ref', String(n));
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || (tag === 'input' ? `input:${el.type || 'text'}` : tag);
    const name = clean(el.getAttribute('aria-label') || (tag === 'select' ? el.getAttribute('name') : el.innerText) || el.value
      || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name')
      || el.getAttribute('alt'));
    const extra = [];
    if (tag === 'a') extra.push(clean(el.getAttribute('href')));
    if ((tag === 'input' || tag === 'textarea') && el.value && el.type !== 'password') extra.push(`value="${clean(el.value)}"`);
    if (el.type === 'password') extra.push('password');
    if (el.checked) extra.push('checked');
    if (tag === 'select') extra.push(`selected="${clean(el.selectedOptions[0] && el.selectedOptions[0].text)}"`, 'options: ' + [...el.options].slice(0, 15).map(o => clean(o.text)).join(' | '));
    out.push(`[${n}] ${role} "${name}"` + (extra.length ? ` (${extra.join(', ')})` : ''));
  }
  const total = document.querySelectorAll(selector).length;
  return {elements: out, truncated: total > n && n >= maxElements, text: document.body ? document.body.innerText : ''};
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

    @property
    def headless(self) -> bool:
        return os.environ.get("OPENAGENT_BROWSER_HEADLESS", "").lower() in ("1", "true", "yes")

    async def page(self) -> Any:
        if self._page is not None and not self._page.is_closed():
            return self._page
        if self._browser is None or not self._browser.is_connected():
            await self._launch()
        self._page = await self._context.new_page()
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
            self._browser = await self._playwright.chromium.launch(headless=self.headless)
        except Exception as exc:
            await self.close()
            raise BrowserUnavailable(f"could not start Chromium ({exc}); run: playwright install chromium") from exc
        self._context = await self._browser.new_context(viewport=VIEWPORT)
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


async def _snapshot(page: Any) -> str:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5_000)
    except Exception:
        pass
    data = await page.evaluate(_SNAPSHOT_JS, MAX_ELEMENTS)
    text = " ".join((data.get("text") or "").split())
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + " …[page text truncated]"
    elements = data.get("elements") or []
    lines = [f"URL: {page.url}", f"Title: {await page.title()}", "", "Elements (use the [n] as ref):"]
    lines += elements or ["(none)"]
    if data.get("truncated"):
        lines.append(f"…only the first {MAX_ELEMENTS} elements are listed")
    lines += ["", "Page text:", text or "(empty)"]
    return "\n".join(lines)


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
        page = await _session.page()
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
        page = await _session.page()
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
    browser_click,
    browser_type,
    browser_select_option,
    browser_press_key,
    browser_back,
    browser_screenshot,
    browser_close,
]
