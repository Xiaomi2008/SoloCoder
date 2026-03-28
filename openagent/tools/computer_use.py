"""Computer use tools for macOS GUI automation.

These tools enable an AI agent to control the computer like a human user
by capturing screenshots and performing mouse/keyboard actions.

Compatible with Qwen3.5 vision models using base64-encoded PNG images.
"""

from __future__ import annotations

import base64
import io
from typing import Literal

from ..core.tool import tool


@tool
def screenshot(
    region: tuple[int, int, int, int] | None = None,
) -> str:
    """Capture a screenshot of the screen or a specific region.

    Returns a base64-encoded PNG image that can be processed by vision-capable
    LLMs like Qwen3.5-35B-A3B (which is an Image-Text-to-Text multimodal model)
    to understand UI elements and determine next actions.

    Qwen3.5-35B-A3B can natively analyze these screenshots and identify:
    - Clickable elements (buttons, links, icons)
    - Text input fields
    - UI layouts and structures
    - On-screen text and information

    Args:
        region: Optional tuple of (x, y, width, height) to capture only a portion
                of the screen. If None, captures the entire primary display.

    Returns:
        A base64-encoded PNG string. The LLM can process this directly in its
        next turn to analyze the UI and determine appropriate actions.

    Example:
        >>> # Capture full screen
        >>> img_data = screenshot()
        >>>
        >>> # Capture specific region (coordinates from previous screenshot analysis)
        >>> img_data = screenshot(region=(100, 200, 500, 300))
    """
    try:
        import pyautogui

        if region:
            x, y, width, height = region
            img = pyautogui.screenshot(region=(x, y, width, height))
        else:
            img = pyautogui.screenshot()

        # Convert to base64 PNG for Qwen3.5 vision model
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        encoded = base64.b64encode(buffer.read()).decode("utf-8")

        return f"[Screenshot captured: {width}x{height} pixels, {len(encoded)} base64 chars]"

    except ImportError:
        return "Error: pyautogui not installed. Install with: pip install pyautogui"
    except Exception as e:
        return f"Error capturing screenshot: {e}"


@tool
def click(x: int, y: int, button: Literal["left", "right", "middle"] = "left") -> str:
    """Click at specified screen coordinates.

    Args:
        x: X coordinate on the screen (pixels from left edge)
        y: Y coordinate on the screen (pixels from top edge)
        button: Which mouse button to click. Default is 'left'.

    Returns:
        Confirmation message with the clicked coordinates.

    Example:
        >>> # Click at center of a button (coordinates determined from screenshot)
        >>> click(500, 300)
        >>>
        >>> # Right-click to open context menu
        >>> click(100, 200, button="right")
    """
    try:
        import pyautogui

        pyautogui.click(x=x, y=y, button=button)
        return f"Clicked at ({x}, {y}) with {button} button"

    except ImportError:
        return "Error: pyautogui not installed. Install with: pip install pyautogui"
    except Exception as e:
        return f"Error clicking at ({x}, {y}): {e}"


@tool
def double_click(x: int, y: int) -> str:
    """Double-click at specified screen coordinates.

    Args:
        x: X coordinate on the screen (pixels from left edge)
        y: Y coordinate on the screen (pixels from top edge)

    Returns:
        Confirmation message with the double-clicked coordinates.

    Example:
        >>> # Double-click to open a file or folder
        >>> double_click(200, 150)
    """
    try:
        import pyautogui

        pyautogui.doubleClick(x=x, y=y)
        return f"Double-clicked at ({x}, {y})"

    except ImportError:
        return "Error: pyautogui not installed. Install with: pip install pyautogui"
    except Exception as e:
        return f"Error double-clicking at ({x}, {y}): {e}"


@tool
def type_text(text: str, interval: float = 0.05) -> str:
    """Type text at the current focused element.

    Types characters with a small delay between each for realism. This simulates
    human typing speed and works with text fields, search boxes, and other
    input elements.

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

        pyautogui.typewrite(text, interval=interval)
        preview = text[:50] + "..." if len(text) > 50 else text
        return f"Typed: {preview}"

    except ImportError:
        return "Error: pyautogui not installed. Install with: pip install pyautogui"
    except Exception as e:
        return f"Error typing '{text[:30]}...': {e}"


@tool
def key_combination(keys: str) -> str:
    """Press a keyboard combination.

    Uses native macOS key names. Common combinations:

    - Navigation: 'cmd+c' (copy), 'cmd+v' (paste), 'cmd+x' (cut), 'cmd+z' (undo)
    - Tabs: 'cmd+tab' (switch app), 'cmd+`' (switch window)
    - Screenshots: 'cmd+shift+3' (full screen), 'cmd+shift+4' (selection)
    - System: 'cmd+space' (Spotlight), 'cmd+q' (quit app), 'cmd+h' (hide app)
    - Window: 'cmd+m' (minimize), 'cmd+w' (close tab/window)
    - Browser: 'cmd+r' (reload), 'cmd+shift+r' (hard reload), 'cmd+/' (find)

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

        # Convert macOS modifiers to pyautogui format
        keys_normalized = keys.lower().replace("super", "win").replace("cmd", "cmd")
        pyautogui.hotkey(keys_normalized)
        return f"Pressed key combination: {keys}"

    except ImportError:
        return "Error: pyautogui not installed. Install with: pip install pyautogui"
    except Exception as e:
        return f"Error pressing '{keys}': {e}"


@tool
def move_mouse(x: int, y: int) -> str:
    """Move mouse cursor to specified coordinates.

    Args:
        x: X coordinate on the screen (pixels from left edge)
        y: Y coordinate on the screen (pixels from top edge)

    Returns:
        Confirmation message with the new cursor position.

    Example:
        >>> # Move cursor to center of screen
        >>> move_mouse(960, 540)
        >>>
        >>> # Move to a menu item before clicking
        >>> move_mouse(50, 20)
        >>> click(50, 20)
    """
    try:
        import pyautogui

        pyautogui.moveTo(x, y)
        return f"Mouse moved to ({x}, {y})"

    except ImportError:
        return "Error: pyautogui not installed. Install with: pip install pyautogui"
    except Exception as e:
        return f"Error moving mouse to ({x}, {y}): {e}"


@tool
def scroll(x: int, y: int, clicks: int) -> str:
    """Scroll at the specified screen location.

    Args:
        x: X coordinate on the screen where scrolling occurs
        y: Y coordinate on the screen where scrolling occurs
        clicks: Number of scroll clicks. Positive value scrolls up,
               negative value scrolls down.

    Returns:
        Confirmation message with scroll details.

    Example:
        >>> # Scroll down in a document
        >>> scroll(500, 400, -10)
        >>>
        >>> # Scroll up in a file browser
        >>> scroll(200, 300, 5)
    """
    try:
        import pyautogui

        pyautogui.scroll(clicks, x=x, y=y)
        direction = "up" if clicks > 0 else "down"
        return f"Scrolled {abs(clicks)} {direction} at ({x}, {y})"

    except ImportError:
        return "Error: pyautogui not installed. Install with: pip install pyautogui"
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
def wait(seconds: float = 1.0) -> str:
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
        import time

        time.sleep(seconds)
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
    "wait",
]
