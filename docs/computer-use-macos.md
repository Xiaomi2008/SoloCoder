# Computer Use with Qwen3.5-35B-A3B (Text-Only Model)

> **Important**: Qwen3.5-35B-A3B is a **text-only** model and cannot natively understand images.
> This guide explains practical workarounds for computer use with your current setup.

## Options for Computer Use

### Option A: Upgrade to Qwen3.5-VL (Best Experience)

For full vision-based computer control, download **Qwen3.5-VL-32B** in LM Studio:

1. Open LM Studio
2. Search for `Qwen3.5-VL-32B`
3. Download Q4_K_M quantization (~20GB)
4. Load and use with SoloCoder

### Option B: Hybrid Approach with Qwen3.5-35B-A3B (Current Setup)

Use text-only model with **structured screen analysis**:

```
Agent workflow:
1. Get screen resolution: get_screen_resolution()
2. Take screenshot and save: screenshot()
3. User/external tool analyzes screenshot → reports back coordinates
4. Agent executes actions at those coordinates: click(x, y), type_text(text)
```

### Option C: Heuristic-Based Automation

Use predictable UI patterns and fixed coordinates:

```python
# Example: Open Safari
key_combination("cmd+space")   # Open Spotlight
type_text("Safari")           # Type app name
key_combination("enter")      # Open app

# Example: Click Safari icon (known position)
click(50, 950)  # Typically in dock

# Example: Navigate to URL
key_combination("cmd+l")        # Focus address bar
type_text("github.com")         # Type URL
key_combination("enter")        # Navigate
```

## Practical Workaround: Manual Screen Annotation

For best results with text-only model:

1. **Take screenshot**: `screenshot()`
2. **Save and analyze externally**:
   ```bash
   # Save screenshot to file
   python -c "import base64; open('screen.png', 'wb').write(base64.b64decode('...'))"
   ```
3. **Manually identify coordinates**: Use Preview.app or similar to find exact click positions
4. **Run automation**: Provide coordinates to agent to execute

## Example: Automating a Known Task

Here's a complete example of automating something predictable:

```python
# Task: Open Safari and go to Google
# This uses known, predictable workflows

async def automate_safari_task():
    # Step 1: Open Spotlight search
    key_combination("cmd+space")
    await wait(0.3)

    # Step 2: Type Safari
    type_text("Safari")
    await wait(0.2)

    # Step 3: Press Enter
    key_combination("enter")
    await wait(0.5)

    # Step 4: Focus address bar
    key_combination("cmd+l")
    await wait(0.2)

    # Step 5: Type URL
    type_text("google.com")
    await wait(0.2)

    # Step 6: Navigate
    key_combination("enter")
    await wait(1.0)

    # Step 7: Verify with screenshot
    result = screenshot()
    print(f"Verification: {result[:100]}...")
```

## Key Workflows (Predictable Patterns)

### Opening Applications
| Action | Keys |
|--------|------|
| Open Spotlight | `cmd+space` |
| Type app name | `type_text("App Name")` |
| Launch | `key_combination("enter")` |

### Copy/Paste Operations
| Action | Keys |
|--------|------|
| Copy | `key_combination("cmd+c")` |
| Paste | `key_combination("cmd+v")` |
| Cut | `key_combination("cmd+x")` |

### Browser Navigation
| Action | Keys |
|--------|------|
| Focus address bar | `key_combination("cmd+l")` |
| Go back | `key_combination("cmd+[")` |
| Go forward | `key_combination("cmd+]")` |
| Reload | `key_combination("cmd+r")` |

## Limitations

1. **No automatic coordinate detection**: Cannot analyze screenshots to find button locations
2. **Manual intervention needed**: User must provide coordinates for UI elements
3. **Screen resolution dependent**: Coordinates vary with display settings
4. **No dynamic element recognition**: Cannot handle variable UI layouts

## When This Approach Works Well

✅ Automating repetitive, predictable tasks
✅ Working with known application layouts
✅ Batch operations (open app → type URL → navigate)
✅ Keyboard-heavy workflows (shortcuts, text entry)

## When This Approach Struggles

❌ Discovering unknown UI elements
❌ Navigating to random web pages and interacting
❌ Handling variable or animated UI elements
❌ Multi-step tasks requiring visual confirmation

## Recommendation

For **full computer use capabilities**, upgrade to **Qwen3.5-VL**. For now, use **keyboard shortcuts and predictable workflows** with the text-only model.

The `screenshot()` tool is still useful for:
- Saving visual records of what happened
- Manual analysis and coordinate identification
- Debugging automation issues
