# Computer Use with Vision Support

Enable SoloCoder to control your Mac using vision-based AI interaction with Qwen3.5-35B-A3B.

## Prerequisites

- **macOS** - All tools are optimized for macOS
- **Qwen3.5-35B-A3B** (native multimodal model with built-in vision)
- **LM Studio** running the model with OpenAI-compatible API
- **pyautogui** for mouse and keyboard control

## Important: All Qwen3.5 Models Have Built-in Vision

Unlike other model families where vision models are separate (e.g., "LLaVA" vs "LLaMA"), **all Qwen3.5 variants have native multimodal (vision) capabilities**. Qwen3.5-35B-A3B, Qwen3.5-27B, and other Qwen3.5 models all have integrated vision - there is no separate "-VL" variant.

## Installation

```bash
uv sync --extra computer-use
```

Or with pip:
```bash
pip install -e ".[computer-use]"
```

## How Vision Works

Qwen3.5-35B-A3B has **native multimodal capabilities** - it can natively understand images. The implementation uses:

1. **ImageBlock** - A new content type for base64-encoded images
2. **OpenAI-compatible format** - `{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}`
3. **Base64 data URIs** - Images are encoded and sent directly to the model

## Example Usage

### Basic Screenshot Analysis

```bash
python cli_coder.py --model qwen3.5-35b-a3b --base-url http://localhost:1234/v1
```

Then in the interactive session:

```
"Take a screenshot and tell me what you see"
"Open Safari and navigate to example.com"
"Click on the button in the top right corner"
```

### Tool Usage

| Tool | Description | Example |
|------|-------------|---------|
| `screenshot()` | Capture full screen | `screenshot()` |
| `screenshot(return_base64=True)` | Get raw base64 data | `screenshot(return_base64=True)` |
| `screenshot(region=(x,y,w,h))` | Capture region | `screenshot(region=(100, 200, 500, 300))` |
| `click(x, y)` | Click coordinates | `click(500, 300)` |
| `key_combination("cmd+c")` | Keyboard shortcuts | `key_combination("cmd+c")` |
| `type_text("hello")` | Type text | `type_text("Hello World")` |

## Workflow Example

```
User: "Open Safari and go to google.com"

Agent thought process:
1. Open Spotlight: key_combination("cmd+space")
2. Type "Safari": type_text("Safari")
3. Press Enter: key_combination("enter")
4. Wait for app to open: wait(1.0)
5. Focus address bar: key_combination("cmd+l")
6. Type URL: type_text("google.com")
7. Navigate: key_combination("enter")
8. Verify with screenshot: screenshot()
9. Analyze UI: Identify clickable elements
10. Continue interaction
```

## macOS Keyboard Shortcuts

Use `"cmd"` for Command key:

| Shortcut | Action |
|----------|--------|
| `cmd+space` | Spotlight |
| `cmd+c` | Copy |
| `cmd+v` | Paste |
| `cmd+x` | Cut |
| `cmd+z` | Undo |
| `cmd+tab` | Switch app |
| `cmd+q` | Quit app |
| `cmd+w` | Close window |
| `cmd+l` | Focus address bar (browser) |
| `cmd+shift+3` | Full screenshot |
| `cmd+shift+4` | Selection screenshot |

## Accessing the Tools

The computer use tools are exported from `openagent.tools`:

```python
from openagent.tools import (
    screenshot,
    click,
    key_combination,
    type_text,
    move_mouse,
    scroll,
    double_click,
    get_screen_resolution,
    wait,
)
```

## Important Notes

### Accessibility Permissions

For macOS, you may need to grant your terminal/IDE **Accessibility permissions**:

1. Go to **System Settings** > **Privacy & Security** > **Accessibility**
2. Add your terminal/IDE to the allowed list
3. Restart the application

### Vision Limitations

- Images are limited to ~224x224px for token efficiency
- The agent must explicitly call `screenshot(return_base64=True)` to get image data
- Base64 strings can be large; consider region-based screenshots for efficiency

## Troubleshooting

### "pyautogui not installed"

```bash
pip install pyautogui pillow
```

### "Screenshot returned but model can't see it"

Ensure you're using `return_base64=True`:
```
screenshot(return_base64=True)
```

### Model doesn't understand screenshots

- Verify you're using Qwen3.5-35B-A3B (has native multimodal vision)
- Check LM Server is running with the correct model loaded
- Ensure screenshot is captured with `return_base64=True`
- Try with a simpler prompt first: "Take a screenshot"

## Technical Details

### ImageFormat

Images use the OpenAI-compatible format:
```json
{
  "type": "image_url",
  "image_url": {
    "url": "data:image/png;base64,{base64_data}"
  }
}
```

### Message Flow

1. User asks a task
2. Agent takes screenshot via `screenshot(return_base64=True)`
3. Image data added as ImageBlock to user message
4. Message sent to Qwen3.5-VL via convert_messages()
5. Model analyzes image and responds with coordinates/actions
6. Agent executes tool calls (click, type, etc.)
7. Repeat until task complete

## Future Enhancements

- Add OCR-based text extraction from screenshots
- Auto-generated coordinate suggestions based on UI patterns
- Better handling of multi-monitor setups
- Integration with macOS Accessibility API for element identification
