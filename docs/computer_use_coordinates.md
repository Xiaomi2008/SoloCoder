# Computer Use Coordinate System Guide

## Overview

The computer use tools in SoloCoder use a **screenshot-based coordinate system** that automatically scales coordinates from the resized image to actual screen positions. This design ensures consistent behavior across different screen resolutions and HiDPI displays.

## How It Works

### 1. Screenshot Capture

When you call `screenshot(return_base64=True)`:
- The full screen is captured at native resolution (e.g., 3008x1692 on your display)
- The image is resized to **512px width** for token efficiency (maintaining aspect ratio)
- For a 3008x1692 screen, the resized image would be approximately 512x288

### 2. Coordinate Mapping

The system maintains scaling factors:
```python
scale_x = original_screen_width / resized_image_width
scale_y = original_screen_height / resized_image_height
```

For a 3008x1692 screen with 512x288 image:
- `scale_x = 3008 / 512 ≈ 5.88`
- `scale_y = 1692 / 288 ≈ 5.88`

### 3. Clicking Workflow

**Step 1**: Take a screenshot
```python
img_base64 = screenshot(return_base64=True)
# Returns base64 image and sets up coordinate mapping
```

**Step 2**: Analyze the image to find target position
- Look at the resized image (512x288)
- Identify coordinates in **image space**, not screen space
- Example: Chrome icon might be at (350, 278) in the image

**Step 3**: Click using image coordinates
```python
click(350, 278)  # Image coordinates!
# Automatically scaled to screen: (2056, 1633)
```

## Important Rules

### ✅ DO:
- Always call `screenshot()` before clicking/moving mouse
- Use **image coordinates** (0-511 for X, 0-287 for Y typically)
- Call `get_screenshot_info()` to see current dimensions and scale factors
- Test clicks in small increments when learning a new screen layout

### ❌ DON'T:
- Don't use actual screen coordinates (e.g., don't click at x=2056, y=1633)
- Don't assume fixed image size - always check with `get_screenshot_info()`
- Don't click without taking a screenshot first

## Example: Opening Chrome

```python
from openagent.tools import computer_use as cu

# Step 1: Take screenshot and analyze
img_base64 = cu.screenshot(return_base64=True)
info = cu.get_screenshot_info()
print(info)
# Output: "Screenshot: 512x288 (from 3008x1692). Scale: 5.88x. Origin: (0, 0)"

# Step 2: Analyze image to find Chrome icon position
# Looking at the dock in the resized image, Chrome is approximately at:
chrome_x = 350  # X coordinate in 512px wide image
chrome_y = 278  # Y coordinate in 288px tall image

# Step 3: Click using image coordinates
result = cu.click(chrome_x, chrome_y)
print(result)
# Output: "Clicked at (2056, 1633) with left button"
```

## Coordinate Ranges

| Screen Resolution | Image Size | Scale Factor | X Range | Y Range |
|------------------|------------|--------------|---------|---------|
| 1920x1080        | 512x288    | ~3.75x       | 0-511   | 0-287   |
| 2560x1440        | 512x288    | ~5.0x        | 0-511   | 0-287   |
| 3008x1692        | 512x288    | ~5.88x       | 0-511   | 0-287   |
| 3840x2160        | 512x288    | ~7.5x        | 0-511   | 0-287   |

**Note**: The image is always resized to 512px width, so coordinate ranges are consistent regardless of screen resolution!

## Debugging Tips

### Check Current State
```python
info = cu.get_screenshot_info()
print(info)
# Shows: image size, original size, scale factor, and origin
```

### Verify Click Position
```python
import pyautogui

# Before click
pos_before = pyautogui.position()
print(f"Before: {pos_before}")

cu.click(350, 278)

# After click
pos_after = pyautogui.position()
print(f"After: {pos_after}")
```

### Manual Coordinate Testing
```python
# Test clicking at image center (should be screen center)
img_center_x = 512 // 2  # 256
img_center_y = 288 // 2  # 144
cu.click(img_center_x, img_center_y)

# Test clicking near dock (bottom of screen)
dock_x = 350
dock_y = 278  # Near bottom of image
cu.click(dock_x, dock_y)
```

## Common Issues

### Issue: Click is in wrong location
**Solution**: Make sure you're using **image coordinates**, not screen coordinates. Always call `screenshot()` first.

### Issue: Coordinates out of range error
**Solution**: Check your coordinate values are within image bounds (0-511 for X, 0-height for Y). Use `get_screenshot_info()` to verify.

### Issue: Click doesn't work after multiple screenshots
**Solution**: The coordinate mapping is updated with each screenshot. Make sure you're using coordinates from the most recent screenshot.

## Technical Details

The scaling function `_scale_from_image_to_screen()`:
1. Validates coordinates are within image bounds
2. Applies scale factor: `screen_x = image_x * (original_width / image_width)`
3. Adds origin offset if capturing a region
4. Clamps to avoid screen corners (pyautogui fail-safe)

This ensures clicks always land where you expect them, regardless of screen resolution or HiDPI settings.

## Best Practices

1. **Always screenshot first**: Never click without taking a screenshot first
2. **Use relative positions**: Instead of absolute coordinates, think in terms of "bottom 10% of image" for dock items
3. **Test incrementally**: Start with obvious targets (center, corners) to verify scaling works
4. **Check info regularly**: Call `get_screenshot_info()` when debugging coordinate issues
5. **Account for UI changes**: Window positions change - retake screenshots after major UI changes

## Reference

- `screenshot(region=None, return_base64=False)` - Capture screen
- `click(x, y, button="left")` - Click at image coordinates
- `double_click(x, y)` - Double-click at image coordinates  
- `move_mouse(x, y)` - Move mouse to image coordinates
- `get_screenshot_info()` - Get current coordinate mapping info
- `get_screen_resolution()` - Get actual screen resolution
