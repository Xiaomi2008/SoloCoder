"""Computer use tools for macOS GUI automation.

These tools enable an AI agent to control the computer like a human user
by capturing screenshots and performing mouse/keyboard actions.

Qwen3.5-35B-A3B and all Qwen3.5 variants have native multimodal (vision)
capabilities - they can natively analyze images passed as base64 data URIs.
"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import functools
import io
import os
import sys
import time
from typing import Any, Callable, Literal

from ..core.tool import tool

# Global state for screenshot scaling factors
# Used to map image coordinates back to actual screen coordinates
_last_screenshot_size: tuple[int, int] | None = None
_original_screenshot_size: tuple[int, int] | None = None
_last_screenshot_origin: tuple[int, int] | None = None
_CORNER_SAFETY_MARGIN = 1
_screen_capture_requested = False


def _off_loop(fn: Callable[..., str]) -> Callable[..., Any]:
    """Run a blocking GUI tool in a worker thread.

    The tool runner calls a sync tool on the service's event loop, so a
    ``time.sleep`` or a ``screencapture`` subprocess here froze every session,
    the Stop button and the browser frames with it. ``functools.wraps`` keeps
    the signature and docstring the ``@tool`` schema is built from.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        return await asyncio.to_thread(fn, *args, **kwargs)

    return wrapper


def _accessibility_trusted() -> bool | None:
    """Whether macOS lets this process post input events; None off macOS.

    Without the grant, ``CGEventPost`` drops every event without an error,
    and the tool reported a click that never happened. The grant belongs to
    the responsible app: the SoloCoder app for its service, the terminal for
    the CLI.
    """
    if sys.platform != "darwin":
        return None
    try:
        services = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        services.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(services.AXIsProcessTrusted())
    except (OSError, AttributeError):
        return None


def _screen_capture_allowed() -> bool | None:
    """Whether macOS lets this process record the screen; None if unknown.

    Without the grant a capture still succeeds but shows only the wallpaper,
    and the model acted on a desktop with no windows on it.
    """
    if sys.platform != "darwin":
        return None
    try:
        import Quartz
    except ImportError:
        return None
    preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
    if preflight is None:
        return None
    if preflight():
        return True
    global _screen_capture_requested
    if not _screen_capture_requested:
        # Once per process: adds the app to the Screen Recording list, so the
        # person has a switch to turn on rather than an app to find.
        _screen_capture_requested = True
        request = getattr(Quartz, "CGRequestScreenCaptureAccess", None)
        if request is not None:
            request()
    return False


_NO_ACCESSIBILITY = (
    "Error: macOS has not granted Accessibility to the app running SoloCoder, so input "
    "events are dropped. Ask the user to enable it in System Settings > Privacy & "
    "Security > Accessibility, then retry."
)
_NO_SCREEN_RECORDING = (
    "Error: macOS has not granted Screen Recording to the app running SoloCoder, so a "
    "screenshot would show only the wallpaper. Ask the user to enable it in System "
    "Settings > Privacy & Security > Screen Recording (the app may need a restart), then retry."
)


def _host_pid() -> int | None:
    """The SoloCoder app's pid, which its service gets in ``OPENAGENT_HOST_PID``."""
    try:
        return int(os.environ["OPENAGENT_HOST_PID"])
    except (KeyError, ValueError):
        return None


def _host_window_on_screen(pid: int) -> bool:
    # The window server, not NSRunningApplication: the service runs no AppKit
    # run loop, and NSRunningApplication's properties only refresh on one.
    import Quartz

    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    ) or []
    return any(
        w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer") == 0 for w in windows
    )


def _step_aside() -> None:
    """Hide the SoloCoder app before the agent looks at or acts on the screen.

    The person pressed Send in SoloCoder, so it is the frontmost app: typed
    text and shortcuts went into its own composer, and its window covered what
    the agent was trying to see. Hiding it hands focus back to the app the
    person was using. The app shows itself again for a permission prompt and
    when the run ends.
    """
    pid = _host_pid()
    if pid is None or sys.platform != "darwin":
        return
    try:
        from AppKit import NSRunningApplication

        if not _host_window_on_screen(pid):
            return
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            return
        app.hide()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and _host_window_on_screen(pid):
            time.sleep(0.05)
        # The window is gone; give the app behind it a moment to take focus.
        time.sleep(0.15)
    except Exception:
        return


def _ready_for_input() -> str | None:
    """An error to return instead of acting, or None to go ahead."""
    if _accessibility_trusted() is False:
        return _NO_ACCESSIBILITY
    _step_aside()
    return None


def _normalize_image_coordinate(value: int | str, name: str) -> int:
    if isinstance(value, int):
        return value

    if isinstance(value, str):
        normalized = value.strip().rstrip(",")
        if normalized.isdigit():
            return int(normalized)

    raise ValueError(
        f"{name} must be an integer screenshot image coordinate, got {value!r}"
    )


def _is_fail_safe_exception(pyautogui: object, exc: Exception) -> bool:
    fail_safe_exception = getattr(pyautogui, "FailSafeException", None)
    return fail_safe_exception is not None and isinstance(exc, fail_safe_exception)


@tool
@_off_loop
def screenshot(
    region: tuple[int, int, int, int] | None = None,
    return_base64: bool = False,
) -> str:
    """Capture a screenshot of the screen or a specific region.

    Returns a base64-encoded JPEG string that can be used with vision-capable
    LLMs like Qwen3.5-35B-A3B (Image-Text-to-Text model). The base64 string
    can be passed directly to the LLM as image input.

    Args:
        region: Optional tuple of (x, y, width, height) to capture only a portion
                of the screen. If None, captures the entire primary display.
        return_base64: If True, returns raw base64 string for direct LLM input.
                      If False, returns a message indicating the image was captured
                      (for backward compatibility). Default is False.

    Returns:
        Base64-encoded JPEG string if return_base64=True, otherwise a message
        confirming the screenshot was taken.

    Example:
        >>> # Capture screenshot for vision analysis
        >>> img_base64 = screenshot(return_base64=True)
        >>> # Pass img_base64 to the vision model as image input
        >>>
        >>> # Capture specific region
        >>> img_base64 = screenshot(region=(100, 200, 500, 300), return_base64=True)
    """
    try:
        import pyautogui
        from PIL import Image

        if _screen_capture_allowed() is False:
            return _NO_SCREEN_RECORDING
        _step_aside()

        if region:
            x, y, capture_width, capture_height = region
            img = pyautogui.screenshot(region=(x, y, capture_width, capture_height))
            original_size = (capture_width, capture_height)
            origin = (x, y)
        else:
            img = pyautogui.screenshot()
            # Use pyautogui.size() for coordinate consistency
            # On HiDPI/Retina displays, PIL image is at native pixel density (2x),
            # but pyautogui.click()/moveTo() use logical coordinates from pyautogui.size()
            screen_width, screen_height = pyautogui.size()
            original_size = (screen_width, screen_height)
            origin = (0, 0)

        # Resize image if too large to reduce token consumption
        # Qwen3.5 vision models work well with ~512px width for screenshots
        max_width = 512

        capture_width, capture_height = original_size

        if capture_width > max_width:
            new_width = max_width
            new_height = int(capture_height * (max_width / capture_width))
            img = img.resize((new_width, new_height), Image.LANCZOS)

        # Encode as JPEG to keep screenshot payloads smaller for multimodal turns.
        buffer = io.BytesIO()
        img.convert("RGB").save(buffer, format="JPEG", quality=60, optimize=True)
        buffer.seek(0)
        base64_data = base64.b64encode(buffer.read()).decode("utf-8")

        # Store scaling factors for coordinate mapping (always, regardless of return_base64)
        global _last_screenshot_size, _original_screenshot_size, _last_screenshot_origin
        _last_screenshot_size = (img.width, img.height)
        _original_screenshot_size = original_size
        _last_screenshot_origin = origin

        if return_base64:
            return base64_data

        return f"Screenshot captured: {img.width}x{img.height} pixels (from {original_size}), {len(base64_data)} base64 chars"

    except ImportError:
        return "Error: pyautogui not installed. Install with: pip install pyautogui"
    except Exception as e:
        return f"Error capturing screenshot: {e}"


def _scale_from_image_to_screen(image_x: int, image_y: int) -> tuple[int, int]:
    """Scale coordinates from screenshot image space to actual screen space.

    This is used when the agent wants to click at a location it saw in the
    screenshot. The screenshot is resized to 512px width for token efficiency,
    so the agent sees smaller coordinates that need to be scaled up.

    Args:
        image_x: X coordinate from the screenshot image
        image_y: Y coordinate from the screenshot image

    Returns:
        Tuple of (scaled_x, scaled_y) in actual screen coordinates
    """
    if _original_screenshot_size is None or _last_screenshot_size is None:
        # No screenshot taken yet, use coordinates as-is
        return image_x, image_y

    img_w, img_h = _last_screenshot_size
    orig_w, orig_h = _original_screenshot_size
    origin_x, origin_y = _last_screenshot_origin or (0, 0)

    if not (0 <= image_x < img_w) or not (0 <= image_y < img_h):
        raise ValueError(
            f"Image coordinates ({image_x}, {image_y}) are outside screenshot bounds "
            f"{img_w}x{img_h}. Use screenshot image coordinates, not full screen coordinates."
        )

    # Calculate scaling ratios
    ratio_x = orig_w / img_w if img_w > 0 else 1.0
    ratio_y = orig_h / img_h if img_h > 0 else 1.0

    # Scale coordinates back to original screen
    scaled_x = int(image_x * ratio_x) + origin_x
    scaled_y = int(image_y * ratio_y) + origin_y

    min_x = origin_x + _CORNER_SAFETY_MARGIN
    min_y = origin_y + _CORNER_SAFETY_MARGIN
    max_x = origin_x + orig_w - 1 - _CORNER_SAFETY_MARGIN
    max_y = origin_y + orig_h - 1 - _CORNER_SAFETY_MARGIN

    if max_x < min_x:
        min_x = max_x = origin_x
    if max_y < min_y:
        min_y = max_y = origin_y

    # Clamp to the captured screen area bounds while avoiding screen corners.
    scaled_x = min(max(scaled_x, min_x), max_x)
    scaled_y = min(max(scaled_y, min_y), max_y)

    return scaled_x, scaled_y


def _get_current_scale_factors() -> tuple[float, float] | None:
    """Get the current screenshot-to-screen scale factors.

    Returns:
        Tuple of (scale_x, scale_y) or None if no screenshot has been taken
    """
    if _original_screenshot_size is None or _last_screenshot_size is None:
        return None

    img_w, img_h = _last_screenshot_size
    orig_w, orig_h = _original_screenshot_size

    return (orig_w / img_w, orig_h / img_h)


@tool
@_off_loop
def click(x: int, y: int, button: Literal["left", "right", "middle"] = "left") -> str:
    """Click at specified coordinates from screenshot image.

    IMPORTANT: Always use coordinates in the last screenshot image, NOT actual screen coordinates!

    Workflow for the agent:
    1. Take screenshot: screenshot(return_base64=True)
    2. Analyze the image to find target position in the resized image (e.g., x=350, y=278)
    3. Call click(350, 278) - this automatically scales to actual screen coordinates

    The screenshot is resized to 512px width for token efficiency.
    Coordinates from the image are automatically scaled to actual screen coordinates using
    the scaling factor calculated when the screenshot was taken.

    Args:
        x: X coordinate in screenshot image (pixels from left, 0 to image width - 1)
        y: Y coordinate in screenshot image (pixels from top, 0 to image height - 1)
        button: Which mouse button to click. Default is 'left'.

    Returns:
        Confirmation message with the actual screen coordinates clicked.

    Example:
        >>> # Agent sees Chrome icon at image position (350, 278) in dock
        >>> click(350, 278)  # Automatically scaled to screen coordinates
        >>> # If screen is 3008x1692 and image is 512x288:
        >>> # click(350, 278) -> actual click at (2056, 1633) with scale factor ~5.88x

    Note:
        - Always call screenshot() before clicking to establish coordinate mapping
        - Use get_screenshot_info() to see current image dimensions and scale factors
        - Coordinates are automatically clamped to avoid screen corners (fail-safe)
    """
    try:
        import pyautogui

        x = _normalize_image_coordinate(x, "x")
        y = _normalize_image_coordinate(y, "y")

        # Scale coordinates from image space to actual screen space
        # (if a screenshot was taken, the globals are set)
        x, y = _scale_from_image_to_screen(x, y)
        blocked = _ready_for_input()
        if blocked:
            return blocked
        pyautogui.click(x=x, y=y, button=button)
        return f"Clicked at ({x}, {y}) with {button} button"

    except ImportError:
        return "Error: pyautogui not installed. Install with: pip install pyautogui"
    except ValueError as e:
        return f"Error clicking: {e}"
    except Exception as e:
        if _is_fail_safe_exception(pyautogui, e):
            return (
                "Error clicking: PyAutoGUI fail-safe triggered. The cursor is at a screen "
                "corner or edge; move it inward and retry."
            )
        return f"Error clicking at ({x}, {y}): {e}"


@tool
@_off_loop
def double_click(x: int, y: int) -> str:
    """Double-click at specified screenshot image coordinates.

    IMPORTANT: Use coordinates in the last screenshot image, NOT actual screen coordinates!

    Args:
        x: X coordinate in the screenshot image (pixels from left edge, 0 to image width - 1)
        y: Y coordinate in the screenshot image (pixels from top edge, 0 to image height - 1)

    Returns:
        Confirmation message with the double-clicked coordinates.

    Example:
        >>> # Double-click to open a file or folder at image position
        >>> double_click(200, 150)  # Automatically scaled to screen

    Note:
        - Coordinates are automatically scaled from image space to screen space
        - Must call screenshot() first to establish coordinate mapping
    """
    try:
        import pyautogui

        x = _normalize_image_coordinate(x, "x")
        y = _normalize_image_coordinate(y, "y")

        # Scale coordinates from image space to actual screen space
        x, y = _scale_from_image_to_screen(x, y)
        blocked = _ready_for_input()
        if blocked:
            return blocked
        pyautogui.doubleClick(x=x, y=y)
        return f"Double-clicked at ({x}, {y})"

    except ImportError:
        return "Error: pyautogui not installed. Install with: pip install pyautogui"
    except ValueError as e:
        return f"Error double-clicking: {e}"
    except Exception as e:
        if _is_fail_safe_exception(pyautogui, e):
            return (
                "Error double-clicking: PyAutoGUI fail-safe triggered. The cursor is at a "
                "screen corner or edge; move it inward and retry."
            )
        return f"Error double-clicking at ({x}, {y}): {e}"


def _post_unicode(text: str, interval: float) -> None:
    """Type ``text`` as Unicode keyboard events, one character per event.

    ``pyautogui.typewrite`` only knows the keys on a US layout and skipped
    everything else without an error: accented letters, CJK, emoji. A
    keyboard event carrying a Unicode string types any character, whatever
    the layout. One character per event because some apps (Electron ones
    among them) read only the first character of a longer string.
    """
    import Quartz

    for char in text:
        if char == "\n":
            # A newline as a string is a character, not the Return key: a
            # form field would not submit and an editor would not break.
            import pyautogui

            pyautogui.press("enter")
        else:
            units = len(char.encode("utf-16-le")) // 2
            for down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                # A modifier the person is holding must not turn "c" into ⌘C.
                Quartz.CGEventSetFlags(event, 0)
                Quartz.CGEventKeyboardSetUnicodeString(event, units, char)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        if interval > 0:
            time.sleep(interval)


@tool
@_off_loop
def type_text(text: str, interval: float = 0.01) -> str:
    """Type text at the current focused element.

    Types any characters, including accented letters, CJK and emoji. A newline
    presses Return. Click the target field first so it has focus.

    Args:
        text: The text to type
        interval: Delay between each character in seconds. Lower values are faster.

    Returns:
        Confirmation message with the typed text (truncated if long).

    Example:
        >>> # Type into an active input field
        >>> type_text("Hello, World!")
        >>>
        >>> # Type a file path
        >>> type_text("/Users/taozeng/Documents/report.pdf")
    """
    try:
        import pyautogui

        blocked = _ready_for_input()
        if blocked:
            return blocked
        if sys.platform == "darwin":
            _post_unicode(text, interval)
        else:
            pyautogui.write(text, interval=interval)
        preview = text[:50] + "..." if len(text) > 50 else text
        return f"Typed: {preview}"

    except ImportError:
        return "Error: pyautogui not installed. Install with: pip install pyautogui"
    except Exception as e:
        return f"Error typing '{text[:30]}...': {e}"


# What models write -> pyautogui's macOS key names. pyautogui ignores a name it
# does not know without an error, so "cmd+c" was a no-op reported as pressed.
_KEY_ALIASES = {
    "cmd": "command", "⌘": "command", "meta": "command", "super": "command", "win": "command",
    "opt": "option", "⌥": "option", "control": "ctrl", "⌃": "ctrl", "⇧": "shift",
    "return": "enter", "⏎": "enter", "escape": "esc", "spacebar": "space",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "page_up": "pageup", "page_down": "pagedown", "forwarddelete": "del",
    "plus": "+", "minus": "-",
}


def _parse_keys(keys: object) -> list[str]:
    """``"cmd+shift+t"`` (or ``["cmd", "t"]``) as pyautogui key names.

    Raises ValueError naming any key pyautogui does not know, so the model
    hears about a typo instead of reading "pressed" for nothing.
    """
    import pyautogui

    if isinstance(keys, str):
        text = keys.strip().lower()
        # "cmd++" is cmd and the plus key; split on "+" except a trailing one.
        parts = [p for p in text.split("+") if p] + (["+"] if text.endswith("++") or text == "+" else [])
    elif isinstance(keys, (list, tuple)):
        parts = [str(k).strip().lower() for k in keys]
    else:
        raise ValueError(f"keys must be a string like 'cmd+c', got {keys!r}")
    names = [_KEY_ALIASES.get(p.replace(" ", ""), p.replace(" ", "")) for p in parts]
    unknown = [p for p, n in zip(parts, names) if not pyautogui.isValidKey(n)]
    if not names or unknown:
        raise ValueError(
            f"unknown key(s) {unknown or [keys]!r}; use names like cmd, shift, option, "
            "ctrl, enter, esc, tab, space, up, f5, or a single character"
        )
    return names


@tool
@_off_loop
def key_combination(keys: str) -> str:
    """Press a keyboard combination.

    Uses native macOS key names. Common combinations:

    - Navigation: 'cmd+c' (copy), 'cmd+v' (paste), 'cmd+x' (cut), 'cmd+z' (undo)
    - Tabs: 'cmd+tab' (switch app), 'cmd+`' (switch window)
    - Screenshots: 'cmd+shift+3' (full screen), 'cmd+shift+4' (selection)
    - System: 'cmd+space' (Spotlight), 'cmd+q' (quit app), 'cmd+h' (hide app)
    - Window: 'cmd+m' (minimize), 'cmd+w' (close tab/window)
    - Browser: 'cmd+r' (reload), 'cmd+shift+r' (hard reload), 'cmd+f' (find)
    - Single keys: 'enter', 'esc', 'tab', 'space', 'up', 'f5'

    Args:
        keys: Keyboard combination as a string. Use '+' to separate keys.
              macOS modifier is 'cmd' (not 'ctrl' or 'super').

    Returns:
        Confirmation message with the pressed key combination.

    Example:
        >>> # Copy selected text
        >>> key_combination("cmd+c")
        >>>
        >>> # Switch to another application
        >>> key_combination("cmd+tab")
        >>>
        >>> # Open Spotlight search
        >>> key_combination("cmd+space")
    """
    try:
        import pyautogui

        try:
            names = _parse_keys(keys)
        except ValueError as e:
            return f"Error pressing keys: {e}"
        blocked = _ready_for_input()
        if blocked:
            return blocked
        pyautogui.hotkey(*names)
        return f"Pressed key combination: {'+'.join(names)}"

    except ImportError:
        return "Error: pyautogui not installed. Install with: pip install pyautogui"
    except Exception as e:
        if _is_fail_safe_exception(pyautogui, e):
            return (
                "Error pressing keys: PyAutoGUI fail-safe triggered. The cursor is at a "
                "screen corner or edge; move it inward and retry."
            )
        return f"Error pressing '{keys}': {e}"


@tool
@_off_loop
def move_mouse(x: int, y: int) -> str:
    """Move mouse cursor to specified screenshot image coordinates.

    IMPORTANT: Use coordinates in the last screenshot image, NOT actual screen coordinates!

    Args:
        x: X coordinate in the screenshot image (pixels from left edge, 0 to image width - 1)
        y: Y coordinate in the screenshot image (pixels from top edge, 0 to image height - 1)

    Returns:
        Confirmation message with the new cursor position.

    Example:
        >>> # Move cursor to center of screen (in image coordinates)
        >>> move_mouse(256, 144)  # Center of 512x288 image
        >>>
        >>> # Move to a menu item before clicking
        >>> move_mouse(50, 20)
        >>> click(50, 20)

    Note:
        - Coordinates are automatically scaled from image space to screen space
        - Must call screenshot() first to establish coordinate mapping
    """
    try:
        import pyautogui

        x = _normalize_image_coordinate(x, "x")
        y = _normalize_image_coordinate(y, "y")

        # Scale coordinates from image space to actual screen space
        x, y = _scale_from_image_to_screen(x, y)
        blocked = _ready_for_input()
        if blocked:
            return blocked
        pyautogui.moveTo(x, y)
        return f"Mouse moved to ({x}, {y})"

    except ImportError:
        return "Error: pyautogui not installed. Install with: pip install pyautogui"
    except ValueError as e:
        return f"Error moving mouse: {e}"
    except Exception as e:
        if _is_fail_safe_exception(pyautogui, e):
            return (
                "Error moving mouse: PyAutoGUI fail-safe triggered. The cursor is at a "
                "screen corner or edge; move it inward and retry."
            )
        return f"Error moving mouse to ({x}, {y}): {e}"


@tool
@_off_loop
def scroll(x: int, y: int, clicks: int) -> str:
    """Scroll at a point in the last screenshot image.

    Uses screenshot image coordinates, like click: point at the area to scroll.

    Args:
        x: X coordinate in the screenshot image
        y: Y coordinate in the screenshot image
        clicks: Number of scroll clicks. Positive value scrolls up,
               negative value scrolls down.

    Returns:
        Confirmation message with scroll details.

    Example:
        >>> # Scroll down in a document
        >>> scroll(250, 200, -10)
        >>>
        >>> # Scroll up in a file browser
        >>> scroll(100, 150, 5)
    """
    try:
        import pyautogui

        x = _normalize_image_coordinate(x, "x")
        y = _normalize_image_coordinate(y, "y")
        # Screen coordinates, as click uses: the model passes what it saw, and
        # unscaled it scrolled a point far up and to the left of that.
        x, y = _scale_from_image_to_screen(x, y)
        blocked = _ready_for_input()
        if blocked:
            return blocked
        pyautogui.scroll(clicks, x=x, y=y)
        direction = "up" if clicks > 0 else "down"
        return f"Scrolled {abs(clicks)} {direction} at ({x}, {y})"

    except ImportError:
        return "Error: pyautogui not installed. Install with: pip install pyautogui"
    except ValueError as e:
        return f"Error scrolling: {e}"
    except Exception as e:
        return f"Error scrolling at ({x}, {y}): {e}"


@tool
def get_screen_resolution() -> str:
    """Get the current screen resolution and number of displays.

    Returns:
        A string describing the screen configuration with dimensions.

    Example:
        >>> get_screen_resolution()
        "Primary display: 1920x1080"
    """
    try:
        import pyautogui

        width, height = pyautogui.size()
        return f"Screen resolution: {width}x{height} pixels"

    except ImportError:
        return "Error: pyautogui not installed. Install with: pip install pyautogui"
    except Exception as e:
        return f"Error getting screen resolution: {e}"


@tool
def get_screenshot_info() -> str:
    """Get information about the current screenshot including dimensions and scale factors.

    This tool helps the agent understand the coordinate space after taking a screenshot.
    The screenshot is resized to 512px width for token efficiency, so coordinates from
    the image need to be scaled to actual screen coordinates.

    Returns:
        A string with screenshot dimensions, screen resolution, and scaling factor.

    Example:
        >>> # After taking a screenshot
        >>> get_screenshot_info()
        "Screenshot: 512x288 (from 3008x1692). Scale: 5.88x"
        >>> # Now agent knows: image (100, 50) -> screen (588, 294)
    """
    try:
        if _original_screenshot_size is None or _last_screenshot_size is None:
            return "No screenshot taken yet. Call screenshot() first."

        img_w, img_h = _last_screenshot_size
        orig_w, orig_h = _original_screenshot_size
        scale_x = orig_w / img_w
        scale_y = orig_h / img_h

        return (
            f"Screenshot: {img_w}x{img_h} (from {orig_w}x{orig_h}). "
            f"Scale: {scale_x:.2f}x. Origin: {_last_screenshot_origin or (0, 0)}. Use image coordinates."
        )

    except Exception as e:
        return f"Error: {e}"


@tool
async def wait(seconds: float = 1.0) -> str:
    """Wait for a specified number of seconds.

    Useful when waiting for UI elements to appear, animations to complete,
    or network operations to finish.

    Args:
        seconds: Number of seconds to wait. Default is 1.0.

    Returns:
        Confirmation message with wait duration.

    Example:
        >>> # Wait for a loading spinner to disappear
        >>> wait(2.0)
        >>>
        >>> # Short pause between actions
        >>> wait(0.5)
    """
    try:
        await asyncio.sleep(seconds)
        return f"Waited for {seconds} seconds"

    except Exception as e:
        return f"Error waiting for {seconds} seconds: {e}"


# ============================================================================
# Export all tools
# ============================================================================

__all__ = [
    "screenshot",
    "click",
    "double_click",
    "type_text",
    "key_combination",
    "move_mouse",
    "scroll",
    "get_screen_resolution",
    "get_screenshot_info",
    "wait",
]
