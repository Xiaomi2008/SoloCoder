"""Native desktop apps, through the agent-desktop CLI's accessibility tree.

agent-desktop (https://github.com/vercel-labs/agent-desktop) reads an app's
accessibility tree and acts on the refs it returns, so the model works with named
buttons and fields instead of pixels. It is an external binary:
``npm install -g agent-desktop``, plus macOS Accessibility and, for screenshots,
Screen Recording permission for whatever launched it.

A short list of tools over the CLI and not its MCP server, for two reasons. The
server serves all 60 commands, and a local model pays for every schema in prefill
time. And ``McpClient`` turns an image into the text ``[Image: image/png]``, so a
screenshot through it would show the model nothing.

Games and other custom-drawn UI expose next to nothing in the tree. A browser game
is better checked with a headless page (``openagent.apps.superagent.page_check``);
this is for apps that have real controls.

``build_desktop_tools`` is the only way in. Given ``allowed_apps`` it returns tools
that refuse every other app and every ref they did not issue: the operator's list
is the authority for a worker that nobody is watching. There is deliberately no
clipboard tool, and no window listing across apps, whose titles would show a worker
what else is open on the machine.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from openagent.core.tool import tool

COMMAND_TIMEOUT_S = 45
MAX_OUTPUT_CHARS = 20_000
#: A Retina window is 3000+ px wide, far more than a vision encoder keeps.
SCREENSHOT_MAX_PX = 1280
#: Homebrew and npm's global bin are not on the PATH of an app started from the Dock.
_EXTRA_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
_INSTALL_HINT = "agent-desktop is not installed: npm install -g agent-desktop"

_last_screenshot: tuple[str, str] | None = None


def last_screenshot() -> tuple[str, str] | None:
    """The latest capture as ``(base64, mime type)``, for the runtime to attach."""
    return _last_screenshot


def _binary() -> str | None:
    search = os.pathsep.join([os.environ.get("PATH", ""), *_EXTRA_BIN_DIRS])
    return shutil.which("agent-desktop", path=search)


async def _running_apps() -> list[str]:
    """The apps with a Dock tile, by way of macOS's own ``lsappinfo``."""
    if shutil.which("lsappinfo") is None:
        return []
    process = await asyncio.create_subprocess_exec(
        "lsappinfo", "list", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), COMMAND_TIMEOUT_S)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return []
    entries = re.split(r'\n(?=\s*\d+\) ")', stdout.decode(errors="replace"))
    names = {
        match.group(1)
        for entry in entries
        if 'type="Foreground"' in entry and (match := re.search(r'\d+\) "([^"]+)"', entry))
    }
    return sorted(names)


def _choose_window(windows: list[dict[str, Any]]) -> dict[str, Any]:
    """The one a person is looking at, else one that can be drawn."""
    shown = [w for w in windows if w.get("visible", True) and not w.get("minimized")] or windows
    return next((w for w in shown if w.get("is_focused")), shown[0])


def _describe_error(envelope: dict[str, Any]) -> str:
    error = envelope.get("error") or {}
    text = f"Error: {error.get('code', 'FAILED')}: {error.get('message', 'agent-desktop failed')}"
    if error.get("suggestion"):
        text += f" {error['suggestion']}"
    return text


async def _call(*args: str) -> dict[str, Any]:
    """One CLI command as its JSON envelope. A failure of any kind is ``ok: False``."""
    binary = _binary()
    if binary is None:
        return {"ok": False, "error": {"code": "NOT_INSTALLED", "message": _INSTALL_HINT}}
    process = await asyncio.create_subprocess_exec(
        binary, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), COMMAND_TIMEOUT_S)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return {"ok": False, "error": {"code": "TIMEOUT", "message": f"no answer in {COMMAND_TIMEOUT_S}s"}}
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    try:
        envelope = json.loads(stdout)
    except ValueError:
        return {"ok": False, "error": {"code": "BAD_OUTPUT", "message": stdout.decode(errors="replace")[:300]}}
    return envelope if isinstance(envelope, dict) else {"ok": False, "error": {"code": "BAD_OUTPUT"}}


def _shrunk(path: Path) -> tuple[bytes, str]:
    """The capture as a small JPEG where ``sips`` exists (macOS), else as it is."""
    smaller = path.with_suffix(".jpg")
    if shutil.which("sips"):
        done = subprocess.run(
            ["sips", "-Z", str(SCREENSHOT_MAX_PX), "-s", "format", "jpeg", str(path), "--out", str(smaller)],
            capture_output=True,
        )
        if done.returncode == 0 and smaller.exists():
            return smaller.read_bytes(), "image/jpeg"
    return path.read_bytes(), "image/png"


def build_desktop_tools(allowed_apps: Iterable[str] | None = None) -> list[Callable[..., Any]]:
    """The desktop tools, optionally held to the named apps.

    Args:
        allowed_apps: Apps these tools may touch, as their exact names. ``None`` is
            every app, for an interactive session where a person answers the
            permission prompts.
    """
    scope = None if allowed_apps is None else frozenset(allowed_apps)
    issued: set[str] = set()

    def refusal(app: str) -> str | None:
        if scope is not None and app not in scope:
            return f"Error: '{app}' is not an app this worker may use. Allowed: {', '.join(sorted(scope))}"
        return None

    def ref_refusal(ref: str) -> str | None:
        if scope is None:
            return None
        snapshot_id = ref.lstrip("@").split(":", 1)[0]
        return None if snapshot_id in issued else f"Error: {ref} did not come from desktop_snapshot; take one first."

    async def find_window(app: str, wanted: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]] | str:
        """The window to use and the app's others, or an error to return."""
        listing = await _call("list-windows", "--app", app)
        if not listing.get("ok"):
            return _describe_error(listing)
        windows = [w for w in listing.get("data") or [] if isinstance(w, dict) and w.get("id")]
        if not windows:
            return f"Error: {app} has no window to capture."
        # Checked here because a scoped tool must not reach another app's window.
        target = next((w for w in windows if w["id"] == wanted), None) if wanted else _choose_window(windows)
        if target is None:
            return f"Error: {wanted} is not a window of {app}. Its windows: " + ", ".join(w["id"] for w in windows)
        return target, [w for w in windows if w is not target]

    async def act(*args: str) -> str:
        envelope = await _call(*args)
        if not envelope.get("ok"):
            return _describe_error(envelope)
        disposition = (envelope.get("data") or {}).get("disposition") or {}
        # A success code does not prove the app reacted, so say what is known.
        return f"Sent ({disposition.get('delivery', 'delivered')}). Take a new snapshot to confirm the effect."

    @tool
    async def desktop_launch(app: str) -> str:
        """Start a desktop app, or attach to it if it is already running.

        Args:
            app: The app's name, such as Calculator, or its bundle identifier.
        """
        if (refused := refusal(app)) is not None:
            return refused
        envelope = await _call("launch", app, "--activate")
        return _describe_error(envelope) if not envelope.get("ok") else f"{app} is running."

    @tool
    async def desktop_snapshot(app: str, interactive_only: bool = True, window: str = "") -> str:
        """Read an app's window as an accessibility tree whose controls carry refs.

        Refs look like @s8f3k2p9:e8 and stop working when the app's UI changes, so
        take a new snapshot after acting. Custom-drawn content such as a game or a
        canvas has no elements here; use desktop_screenshot for that. For an app with
        several windows the result names the others under other_windows.

        Args:
            app: The app's name.
            interactive_only: Only controls you can act on; False includes all text.
            window: A window id from other_windows; empty chooses the front one.
        """
        if (refused := refusal(app)) is not None:
            return refused
        args = ["snapshot", "--app", app, "--compact"] + (["-i"] if interactive_only else [])
        others: list[dict[str, Any]] = []
        envelope: dict[str, Any] = {}
        if not window:
            envelope = await _call(*args)
        # An app with several windows is refused until one is named.
        if window or (envelope.get("error") or {}).get("code") == "AMBIGUOUS_TARGET":
            found = await find_window(app, window)
            if isinstance(found, str):
                return found
            target, others = found
            envelope = await _call(*args, "--window-id", str(target["id"]))
        if not envelope.get("ok"):
            return _describe_error(envelope)
        data = envelope.get("data") or {}
        if others:
            # First, because a long tree is cut at the end.
            data = {"other_windows": [{"id": w["id"], "title": w.get("title", "")} for w in others], **data}
        if data.get("snapshot_id"):
            issued.add(str(data["snapshot_id"]))
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(text) > MAX_OUTPUT_CHARS:
            return text[:MAX_OUTPUT_CHARS] + "\n[Cut: the tree is too long. Keep interactive_only on.]"
        return text

    @tool
    async def desktop_click(ref: str) -> str:
        """Click a control by a ref from the latest desktop_snapshot.

        Args:
            ref: The whole ref, such as @s8f3k2p9:e8.
        """
        if (refused := ref_refusal(ref)) is not None:
            return refused
        return await act("click", ref)

    @tool
    async def desktop_type(ref: str, text: str) -> str:
        """Type text into a control by a ref from the latest desktop_snapshot.

        Args:
            ref: The whole ref, such as @s8f3k2p9:e8.
            text: The text to type.
        """
        if (refused := ref_refusal(ref)) is not None:
            return refused
        return await act("type", ref, text)

    @tool
    async def desktop_press(combo: str, app: str, bring_forward: bool = False) -> str:
        """Send a key combination to an app.

        Quit shortcuts are refused. Some apps take keys only when frontmost:
        bring_forward moves the app in front of whatever the person is doing, so
        use it only after a press without it was refused.

        Args:
            combo: Such as return, escape, cmd+s or right.
            app: The app's name.
            bring_forward: Bring the app to the front before sending.
        """
        if (refused := refusal(app)) is not None:
            return refused
        return await act("press", combo, "--app", app, *(["--headed"] if bring_forward else []))

    @tool
    async def desktop_screenshot(app: str) -> str:
        """Capture an app's window as an image, for content the tree does not show.

        Args:
            app: The app's name.
        """
        global _last_screenshot
        if (refused := refusal(app)) is not None:
            return refused
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "window.png"
            # --app alone waits on every window on the system and times out when
            # any other app is churning; naming the window as well does not.
            found = await find_window(app)
            if isinstance(found, str):
                return found
            target, _ = found
            envelope = await _call("screenshot", "--app", app, "--window-id", str(target["id"]), str(path))
            if not envelope.get("ok"):
                return _describe_error(envelope)
            if not path.exists():
                return "Error: agent-desktop reported a capture but wrote no file."
            data, mime = await asyncio.to_thread(_shrunk, path)
        _last_screenshot = (base64.b64encode(data).decode("ascii"), mime)
        return f"Screenshot captured of {app}."

    tools = [
        desktop_launch,
        desktop_snapshot,
        desktop_click,
        desktop_type,
        desktop_press,
        desktop_screenshot,
    ]
    if scope is None:

        @tool
        async def desktop_list_apps() -> str:
            """List the running apps, with the names the other desktop tools take."""
            # One stale process makes list-apps retry until any deadline, so keep
            # the deadline short and ask macOS directly.
            envelope = await _call("list-apps", "--timeout-ms", "2000")
            data = envelope.get("data") if envelope.get("ok") else await _running_apps()
            if not data:
                return _describe_error(envelope)
            return json.dumps(data, ensure_ascii=False, separators=(",", ":"))[:MAX_OUTPUT_CHARS]

        tools.append(desktop_list_apps)
    return tools


desktop_tools = build_desktop_tools()
