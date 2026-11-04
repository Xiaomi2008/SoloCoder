"""A Worker's headless browser, for running the page it just built.

A syntax check cannot say whether a game starts, whether its keys work, or what it
looks like. These tools open a file from the ticket directory in Chromium and let
the Worker press keys, read page state and take a screenshot.

Not ``openagent.tools.browser``, which is one shared window for a person to watch,
free to go to any public site, and able to keep sign-ins. A Worker runs unattended,
so its browser is another shape: one per Worker, so concurrent tickets share no
page; headless, so it takes no focus and needs no macOS permissions; a fresh
profile; and held to its directory. Offline plus a route guard mean the page can
load its own files and nothing else, so a page the model wrote cannot reach the
network or read the rest of the disk.

The page is untrusted code, sandboxed by Chromium and not by this module; the guard
is about where it can reach. Every step has a deadline because a page can hang.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from openagent.core.tool import tool

VIEWPORT = {"width": 1024, "height": 640}
STEP_TIMEOUT_S = 15
MAX_ACTIONS = 200
MAX_WAIT_MS = 15_000
MAX_OUTPUT_CHARS = 4_000
MAX_ERRORS = 20
PROMPT = (
    "You can run what you build: if the work includes a web page or browser game, open it with page_open "
    "and exercise it with page_run, page_eval and page_screenshot before you report ready. Say what you "
    "actually observed, and what you did not test."
)


class PageCheck:
    """One headless page, opened on first use and closed at the end of each turn."""

    def __init__(self, directory: str | Path, heartbeat: Callable[[str], None] | None = None) -> None:
        self.directory = Path(directory).resolve()
        self.heartbeat = heartbeat or (lambda note: None)
        self.capture: str | None = None
        self.errors: list[str] = []
        self._held: set[str] = set()
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    def _inside(self, path: Path) -> bool:
        return path.resolve().is_relative_to(self.directory)

    async def _guard(self, route: Any) -> None:
        parsed = urlparse(route.request.url)
        if parsed.scheme in ("data", "blob") or (parsed.scheme == "file" and self._inside(Path(unquote(parsed.path)))):
            await route.continue_()
        else:
            self._note_error(f"blocked request outside the ticket directory: {route.request.url[:200]}")
            await route.abort()

    def _note_error(self, text: str) -> None:
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(text[:500])

    async def _start(self) -> Any:
        if self._page is not None:
            return self._page
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "page tools need Playwright: pip install 'openagent[browser]' && playwright install chromium"
            ) from exc
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        context = await self._browser.new_context(
            viewport=VIEWPORT, offline=True, accept_downloads=False, service_workers="block"
        )
        context.set_default_timeout(STEP_TIMEOUT_S * 1000)
        await context.route("**/*", self._guard)
        # A second window is one the Worker has no way to see or close.
        self._page = await context.new_page()
        context.on("page", lambda extra: asyncio.ensure_future(extra.close()))
        self._page.on("pageerror", lambda error: self._note_error(f"uncaught: {error}"))
        self._page.on(
            "console", lambda message: self._note_error(f"console.error: {message.text}") if message.type == "error" else None
        )
        return self._page

    async def close(self) -> None:
        browser, playwright = self._browser, self._playwright
        self._browser = self._playwright = self._page = None
        self._held.clear()
        for closer in (getattr(browser, "close", None), getattr(playwright, "stop", None)):
            if closer is not None:
                try:
                    await asyncio.wait_for(closer(), STEP_TIMEOUT_S)
                except Exception:
                    pass

    def _errors_since(self) -> str:
        if not self.errors:
            return "No page errors."
        text = "Page errors:\n" + "\n".join(self.errors)
        self.errors = []
        return text

    async def _step(self, note: str, work: Callable[[], Any], timeout: float = STEP_TIMEOUT_S) -> str:
        """Run one step under a deadline. Every failure is a result, not an exception."""
        self.heartbeat(note)
        try:
            return await asyncio.wait_for(work(), timeout)
        except asyncio.TimeoutError:
            return f"Error: no answer in {timeout:g}s; the page may be hung. {self._errors_since()}"
        except Exception as exc:
            first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            return f"Error: {first} {self._errors_since()}"
        finally:
            self.heartbeat(f"Finished: {note}")

    def tools(self) -> list[Callable[..., Any]]:
        @tool
        async def page_open(path: str) -> str:
            """Open a file from your ticket directory in a headless browser.

            The page has no network and can load only files from this directory.

            Args:
                path: The file, relative to the ticket directory, such as index.html.
            """
            target = self.directory / path
            if not self._inside(target):
                return "Error: that file is outside the ticket directory."
            if not target.is_file():
                return f"Error: no file at {path}."

            async def work() -> str:
                page = await self._start()
                self.errors = []
                # The browser keeps a key down across a load, and the new page
                # would run with it held.
                for key in sorted(self._held):
                    await page.keyboard.up(key)
                self._held.clear()
                await page.goto(target.resolve().as_uri(), wait_until="load")
                return f"Opened {path}: title {await page.title()!r}. {self._errors_since()}"

            return await self._step(f"Opening {path}", work)

        @tool
        async def page_run(actions: str) -> str:
            """Press keys and wait, in order. The page keeps running in real time
            between your tool calls, so put timed input in one call.

            Actions are separated by semicolons: press KEY, down KEY, up KEY, wait MS,
            click X Y. Keys are named like ArrowRight, Space, a or Enter. To run and
            jump: down ArrowRight; wait 400; press Space; wait 500; up ArrowRight

            Args:
                actions: The actions to perform, separated by semicolons.
            """
            steps = [part.split() for part in actions.split(";") if part.strip()]
            if not steps:
                return "Error: no actions given."
            if len(steps) > MAX_ACTIONS:
                return f"Error: at most {MAX_ACTIONS} actions in one call."
            total = 0
            for step in steps:
                if step[0] == "wait":
                    if len(step) != 2 or not step[1].isdigit():
                        return f"Error: wait takes milliseconds: {' '.join(step)}"
                    total += int(step[1])
                elif step[0] == "click":
                    if len(step) != 3 or not all(value.isdigit() for value in step[1:]):
                        return f"Error: click takes x and y: {' '.join(step)}"
                elif step[0] not in ("press", "down", "up") or len(step) != 2:
                    return f"Error: unknown action: {' '.join(step)}"
            if total > MAX_WAIT_MS:
                return f"Error: at most {MAX_WAIT_MS} ms of waiting in one call."

            async def work() -> str:
                if self._page is None:
                    return "Error: open a page with page_open first."
                page = self._page
                for step in steps:
                    if step[0] == "wait":
                        await asyncio.sleep(int(step[1]) / 1000)
                    elif step[0] == "press":
                        await page.keyboard.press(step[1])
                    elif step[0] == "down":
                        await page.keyboard.down(step[1])
                        self._held.add(step[1])
                    elif step[0] == "up":
                        await page.keyboard.up(step[1])
                        self._held.discard(step[1])
                    else:
                        await page.mouse.click(int(step[1]), int(step[2]))
                held = f" Still held: {', '.join(sorted(self._held))}." if self._held else ""
                return f"Done: {len(steps)} actions.{held} Title {await page.title()!r}. {self._errors_since()}"

            return await self._step(f"Running {len(steps)} page actions", work, STEP_TIMEOUT_S + total / 1000)

        @tool
        async def page_eval(expression: str) -> str:
            """Evaluate a JavaScript expression in the page and return it as JSON.

            Use it to read game state, such as ({x: player.x, lives}). Top-level let
            and const are readable by name, but things inside a closure are not.

            Args:
                expression: The JavaScript expression.
            """

            async def work() -> str:
                if self._page is None:
                    return "Error: open a page with page_open first."
                value = await self._page.evaluate(expression)
                return f"{json.dumps(value, default=str)[:MAX_OUTPUT_CHARS]}\n{self._errors_since()}"

            return await self._step("Reading page state", work)

        @tool
        async def page_screenshot() -> str:
            """Capture the page as an image, which you will see in the next message."""

            async def work() -> str:
                if self._page is None:
                    return "Error: open a page with page_open first."
                data = await self._page.screenshot(type="jpeg", quality=70)
                self.capture = base64.b64encode(data).decode("ascii")
                return f"Screenshot captured. {self._errors_since()}"

            return await self._step("Capturing the page", work)

        return [page_open, page_run, page_eval, page_screenshot]
