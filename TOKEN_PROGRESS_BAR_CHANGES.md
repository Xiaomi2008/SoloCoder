# Token Progress Bar Feature

## Overview
Added a visual progress bar to show current token usage vs total context window length in the SoloCoder CLI interface.

## Changes Made

### 1. New Function: `display_token_progress_bar` (openagent/core/display.py)
- Displays a colored progress bar showing token consumption
- Color-coded based on usage level:
  - **Green**: Low usage (<50%)
  - **Yellow**: Moderate usage (50%-80%)
  - **Red**: High usage (>80%, approaching compaction threshold)
- Formats large numbers with K/M suffixes for readability
- Returns formatted string that can be printed directly

### 2. Updated CLI Interface (cli_coder.py)
- Progress bar displayed before each user prompt
- Enhanced `/context` command shows detailed token usage with status indicators:
  - Warning when approaching compaction threshold
  - Info message at moderate usage levels
  - Success indicator for healthy context usage
- Shows progress bar after auto-compaction to demonstrate reduced token count

### 3. Auto-Compaction Notification (openagent/core/agent.py)
- Displays warning and current token count before auto-compacting
- Helps users understand when the system is managing their context window

## Usage Examples

```python
from openagent import display_token_progress_bar

# Display progress bar for current session
print(display_token_progress_bar(current_tokens, max_context_tokens))
```

### Output Format
```
Context: ██████████░░░░░░░░░░░░░░░░░░░░░░░░░░ 25% (32.0k/128.0k)
```

## Features
- **Visual Feedback**: Clear visual indicator of context window usage
- **Color Coding**: Intuitive color scheme for quick status assessment
- **Smart Formatting**: Automatically formats large numbers (e.g., "64.0k", "1.0M")
- **Threshold Awareness**: Highlights when approaching compaction threshold
- **Non-Intrusive**: Only displays during interactive sessions

## Integration Points
1. **Main Loop**: Displayed before each user prompt in `run_interactive_session()`
2. **Context Command**: Enhanced `/context` command with detailed status
3. **Auto-Compaction**: Shows current usage before automatic compaction
4. **Manual Compaction**: Displays reduced token count after `/compact`

## Testing
Run the following to test:
```bash
python -c "from openagent import display_token_progress_bar; print(display_token_progress_bar(64000, 128000))"
```

Expected output (with colors in TTY):
```
Context: ████████████████████░░░░░░░░░░░░░░░░░░░░ 50% (64.0k/128.0k)
```

## Benefits
- Users can now see at a glance how much of their context window is being used
- Helps understand when the system will auto-compact
- Provides better visibility into conversation history growth
- Makes token management more transparent and user-friendly
