"""Test clicking on Chrome icon with correct coordinate scaling."""
import sys
sys.path.insert(0, '/Users/taozeng/Projects/SoloCoder')

from openagent.tools import computer_use as cu
import pyautogui

# Reset state
cu._last_screenshot_size = None
cu._original_screenshot_size = None

print("=== Chrome Icon Click Test ===\n")

# Get screen dimensions
screen_w, screen_h = pyautogui.size()
print(f"Screen resolution: {screen_w}x{screen_h}")

# Take a screenshot
result = cu.screenshot(return_base64=False)
print(f"\nScreenshot info:\n  {result}")

img_w, img_h = cu._last_screenshot_size
orig_w, orig_h = cu._original_screenshot_size
print(f"  Image size: {img_w}x{img_h}")
print(f"  Original (screen) size: {orig_w}x{orig_h}")

# Calculate scale factors
scale_x = orig_w / img_w
scale_y = orig_h / img_h
print(f"\nScale factors:")
print(f"  X: {scale_x:.2f}")
print(f"  Y: {scale_y:.2f}")

# Test Chrome icon position (from our manual testing)
print("\n--- Chrome Icon Position ---")
chrome_img_x, chrome_img_y = 350, 278

scaled_x, scaled_y = cu._scale_from_image_to_screen(chrome_img_x, chrome_img_y)

print(f"Chrome in image: ({chrome_img_x}, {chrome_img_y})")
print(f"Scaled to screen: ({scaled_x}, {scaled_y})")
print(f"Expected actual click: (~{int(chrome_img_x * scale_x)}, ~{int(chrome_img_y * scale_y)})")

# Get current mouse position before click
pos_before = pyautogui.position()
print(f"\nMouse before click: {pos_before}")

# Click on Chrome
result = cu.click(chrome_img_x, chrome_img_y)
print(f"Click result: {result}")

pos_after = pyautogui.position()
print(f"Mouse after click: {pos_after}")

# Verify the click was at expected position
if pos_after.x == scaled_x and pos_after.y == scaled_y:
    print("\n✓ PASS: Mouse is at expected screen position!")
else:
    error_x = abs(pos_after.x - scaled_x)
    error_y = abs(pos_after.y - scaled_y)
    print(f"\n✗ FAIL: Expected ({scaled_x}, {scaled_y}), got {pos_after}")
    print(f"  Error: {error_x}px X, {error_y}px Y")

print("\n=== Test Complete ===")
