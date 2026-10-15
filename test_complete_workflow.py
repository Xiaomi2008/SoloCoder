"""Complete workflow demonstration for computer use coordinate system.

This script demonstrates the correct way to use screenshot-based coordinates
for GUI automation, showing how image coordinates are automatically scaled
to screen coordinates.
"""
import sys
sys.path.insert(0, '/Users/taozeng/Projects/SoloCoder')

from openagent.tools import computer_use as cu
import pyautogui

def test_workflow():
    """Test the complete workflow for clicking on UI elements."""
    
    print("=" * 70)
    print("COMPLETE WORKFLOW DEMONSTRATION")
    print("=" * 70)
    
    # Reset state
    cu._last_screenshot_size = None
    cu._original_screenshot_size = None
    
    # Step 1: Get screen information
    print("\n📋 STEP 1: Screen Information")
    print("-" * 70)
    screen_w, screen_h = pyautogui.size()
    print(f"Actual screen resolution: {screen_w}x{screen_h}")
    
    # Step 2: Take screenshot (establishes coordinate mapping)
    print("\n📸 STEP 2: Take Screenshot")
    print("-" * 70)
    result = cu.screenshot(return_base64=False)
    print(f"{result}")
    
    img_w, img_h = cu._last_screenshot_size
    orig_w, orig_h = cu._original_screenshot_size
    
    # Step 3: Get screenshot info (shows coordinate mapping)
    print("\n📊 STEP 3: Screenshot Info")
    print("-" * 70)
    info = cu.get_screenshot_info()
    print(info)
    
    scale_x = orig_w / img_w
    scale_y = orig_h / img_h
    print(f"\nScale factors:")
    print(f"  X: {scale_x:.2f}x")
    print(f"  Y: {scale_y:.2f}x")
    
    # Step 4: Test clicking at various positions using IMAGE coordinates
    print("\n🖱️ STEP 4: Click Tests (using IMAGE coordinates)")
    print("-" * 70)
    
    test_cases = [
        ("Screen Center", img_w // 2, img_h // 2),
        ("Top-Left Corner", 10, 10),
        ("Bottom-Right Corner", img_w - 10, img_h - 10),
        ("Chrome Icon (dock)", 350, 278),
    ]
    
    for name, img_x, img_y in test_cases:
        print(f"\n{name}:")
        print(f"  Image coordinates: ({img_x}, {img_y})")
        
        # Calculate expected screen position
        scaled_x, scaled_y = cu._scale_from_image_to_screen(img_x, img_y)
        print(f"  Expected screen: ({scaled_x}, {scaled_y})")
        
        # Get mouse position before click
        pos_before = pyautogui.position()
        
        # Perform click
        result = cu.click(img_x, img_y)
        print(f"  Click result: {result}")
        
        # Verify mouse moved to expected position
        pos_after = pyautogui.position()
        if pos_after.x == scaled_x and pos_after.y == scaled_y:
            print(f"  ✅ PASS: Mouse at correct position")
        else:
            error_x = abs(pos_after.x - scaled_x)
            error_y = abs(pos_after.y - scaled_y)
            print(f"  ❌ FAIL: Expected ({scaled_x}, {scaled_y}), got {pos_after}")
            print(f"     Error: {error_x}px X, {error_y}px Y")
    
    # Step 5: Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("""
Key Points:
1. Always call screenshot() first to establish coordinate mapping
2. Use IMAGE coordinates (0-511 for X, 0-height for Y), NOT screen coordinates
3. The system automatically scales image coordinates to screen coordinates
4. Use get_screenshot_info() to see current dimensions and scale factors

Example Workflow:
  img_base64 = screenshot(return_base64=True)  # Step 1: Capture
  click(350, 278)  # Step 2: Click using IMAGE coordinates!
  # Automatically scaled from (350, 278) to actual screen position

For your screen ({screen_w}x{screen_h}):
  - Image size: {img_w}x{img_h}
  - Scale factor: ~{scale_x:.2f}x
  - Chrome icon at image (350, 278) → screen ({chrome_screen_x}, {chrome_screen_y})
    """.format(
        screen_w=screen_w,
        screen_h=screen_h,
        img_w=img_w,
        img_h=img_h,
        scale_x=scale_x,
        chrome_screen_x=int(350 * scale_x),
        chrome_screen_y=int(278 * scale_y)
    ))
    
    print("=" * 70)

if __name__ == "__main__":
    test_workflow()
