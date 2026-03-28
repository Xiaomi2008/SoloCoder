# Computer Use Guide

Enable SoloCoder to control your Mac like a human user using vision-based AI interaction.

## Prerequisites

- **macOS** - All tools are optimized for macOS
- **Qwen3.5-VL** or other vision-capable model - The screenshot returns base64-encoded PNG images that vision models can analyze
- **pyautogui** - For mouse and keyboard control

## Installation

```bash
# Install computer use dependencies
uv pip install -e ".[computer-use]"

# Or with pip
pip install -e ".[computer-use]"
```

## Quick Start

### Using the CLI

```bash
# Start with a vision-capable model
python cli_coder.py \
    --model qwen3.5-35b-a3b \
    --base-url http://localhost:1234/v1 \
    -w /path/to/project
```

### Using the Web UI

```bash
streamlit run server.py
```

## Available Computer Use Tools

| Tool | Description |
|------|-------------|
| `screenshot()` | Capture full screen or region as base64 PNG |
| `screenshot(region=(x,y,w,h))` | Capture specific screen region |
| `click(x, y, button="left")` | Click at screen coordinates |
| `double_click(x, y)` | Double-click at screen coordinates |
| `type_text(text)` | Type text into focused element |
| `key_combination(keys)` | Press keyboard shortcuts (e.g., "cmd+c") |
| `move_mouse(x, y)` | Move cursor to coordinates |
| `scroll(x, y, clicks)` | Scroll at location |
| `get_screen_resolution()` | Get current screen dimensions |
| `wait(seconds)` | Wait for UI to update |

## How It Works

### The Computer Use Loop

1. **User gives task**: "Open Safari and navigate to github.com"
2. **Agent captures screenshot**: Uses `screenshot()` to see current screen
3. **Vision model analyzes**: Qwen3.5-VL examines image and identifies clickable elements
4. **Agent decides action**: Determines coordinates for next interaction
5. **Agent executes**: Uses `click()`, `type_text()`, or `key_combination()`
6. **Verify result**: Takes new screenshot to confirm action worked
7. **Repeat** until task complete

### Image Format for Qwen3.5

The `screenshot()` tool returns a base64-encoded PNG string. For Qwen3.5-VL, the agent should format this as an image block:

```python
# Example of how the agent should use the screenshot result
import base64
import io
from PIL import Image

# The tool returns: "Screenshot captured: 1920x1080 pixels, 123456 base64 chars"
# Extract the base64 and create an image block for Qwen3.5
base64_img = extract_base64_from_result(result)  # Agent's parsing logic
image_block = {
    "type": "image",
    "source": {
        "type": "base64",
        "media_type": "image/png",
        "data": base64_img
    }
}
```

## Example Usage Patterns

### Pattern 1: Opening an Application

```
User: "Open Finder and navigate to Downloads folder"

Agent thought process:
1. Get screen resolution: get_screen_resolution()
2. Take screenshot: screenshot()
3. Analyze: Find Finder icon in Dock (usually at bottom)
4. Action: click(dock_x, dock_y) or key_combination("cmd+space") then type_text("Finder")
5. Verify: Wait and screenshot to confirm Finder opened
```

### Pattern 2: Web Browser Navigation

```
User: "Go to google.com in Safari"

Agent thought process:
1. Check if Safari is open: screenshot()
2. If not open: open Finder, type "Safari", press Enter
3. Once Safari open: key_combination("cmd+l") to focus address bar
4. type_text("google.com")
5. key_combination("enter")
6. screenshot() to verify page loaded
```

### Pattern 3: File Operations

```
User: "Create a new folder called 'test' on Desktop"

Agent thought process:
1. screenshot() to see current Desktop
2. key_combination("cmd+shift+g") to open Go to Folder
3. type_text("~/Desktop")
4. key_combination("enter")
5. click(empty space on Desktop)
6. Right-click context menu: click(x, y, button="right")
7. Wait for menu: wait(0.5)
8. Click "New Folder": click(new_folder_x, new_folder_y)
9. type_text("test")
10. key_combination("enter")
```

## Keyboard Shortcuts Reference

### macOS Modifier Keys
- Use `cmd` for Command key (not `ctrl` or `super`)
- Use `shift` for Shift
- Use `option` for Option/Alt
- Use `ctrl` for Control

### Common Shortcuts

| Shortcut | Action |
|----------|--------|
| `cmd+c` | Copy |
| `cmd+v` | Paste |
| `cmd+x` | Cut |
| `cmd+z` | Undo |
| `cmd+shift+z` | Redo |
| `cmd+a` | Select all |
| `cmd+s` | Save |
| `cmd+n` | New window |
| `cmd+w` | Close window |
| `cmd+q` | Quit app |
| `cmd+h` | Hide app |
| `cmd+tab` | Switch app |
| `cmd+` (backtick) | Switch window |
| `cmd+space` | Spotlight |
| `cmd+shift+3` | Full screenshot |
| `cmd+shift+4` | Selection screenshot |
| `cmd+f` | Find |
| `cmd+shift+:` | Send in chat |

## Tips for Better Results

1. **Start with a screenshot**: Always see the current state before acting
2. **Use `wait()` for slow UI**: Some apps need time to respond
3. **Be specific with coordinates**: "Click at (500, 300)" is more reliable than "Click the button"
4. **Verify each step**: Take screenshots after major actions
5. **Handle errors gracefully**: If an action fails, screenshot and try an alternative

## Troubleshooting

### "pyautogui not installed"
```bash
pip install pyautogui pillow
```

### Screen capture fails
- macOS may require accessibility permissions for pyautogui
- Go to System Settings > Privacy & Security > Accessibility
- Add your terminal/IDE to the allowed list

### Model doesn't understand screenshots
- Ensure you're using a vision-capable model (Qwen3.5-VL, GPT-4V, etc.)
- Provide clear instructions in your prompt about analyzing the screenshot

## Limitations

- **No built-in element detection**: Unlike OpenComputerUse's Selenium approach, pyautogui requires coordinate-based input
- **No DOM access**: Can't read webpage HTML, must rely on visual analysis
- **Resolution dependent**: Coordinates are specific to screen resolution
- **No built-in accessibility API**: May require manual permission setup

## Future Enhancements

- Add OCR-based text extraction from screenshots
- Add auto-generated coordinate suggestions based on UI patterns
- Add support for multi-monitor setups with better resolution detection
- Add integration with macOS Accessibility API for element identification
