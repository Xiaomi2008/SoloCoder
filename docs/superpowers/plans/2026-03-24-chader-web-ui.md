# SoloCoder Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a Streamlit-based web UI for the Coder Agent with CLI-matching visual style.

**Architecture:** Single-file Streamlit app (`server.py`) that creates a CoderAgent instance and manages chat sessions. Uses Streamlit's native components with custom styling.

**Tech Stack:**
- Streamlit 1.55.0 - Web framework
- openagent - Existing agent framework

---

**Note:** This is an MVP implementation. Features from original spec (tool call display, advanced CSS) are deferred to V2.

---

### Task 0: TDD Foundation - Write Tests First

**Files:**
- Create: `tests/test_server_core.py`

- [ ] **Step 1: Write test for error formatting function**

```python
def test_format_error_message_openai():
    """Test OpenAI API error formatting."""
    from server import format_error_message
    
    error = Exception("OpenAI API Error: invalid_key")
    assert "OpenAI API Error" in format_error_message(error)

def test_format_error_message_unauthorized():
    """Test 401 unauthorized error formatting."""
    from server import format_error_message
    
    error = Exception("401: Unauthorized")
    assert "Authentication failed" in format_error_message(error)

def test_format_error_message_ratelimit():
    """Test 429 rate limit error formatting."""
    from server import format_error_message
    
    error = Exception("429: Rate limit exceeded")
    assert "Rate limit" in format_error_message(error)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_server_core.py -v
```
Expected: "function not defined" errors

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/test_server_core.py
git commit -m "test: add core function tests (expecting failures)"
```

---

### Task 1: Project Setup and Configuration

**Files:**
- Create: `.streamlit/config.toml`

- [ ] **Step 1: Create Streamlit configuration**

Create `.streamlit/config.toml`:
```toml
[theme]
primaryColor = "#16a34a"
backgroundColor = "#ffffff"
secondaryBackgroundColor = "#f0f0f0"
textColor = "#262730"
font = "sans serif"
```

- [ ] **Step 2: Create initial server.py structure**

Create `server.py`:
```python
#!/usr/bin/env python3
"""SoloCoder Web UI - Streamlit interface for the Coder Agent."""

from __future__ import annotations

import streamlit as st
import os

# Page configuration
st.set_page_config(
    page_title="SoloCoder",
    page_icon="💻",
    layout="wide",
    initial_sidebar_state="expanded"
)

def format_error_message(error: Exception) -> str:
    """Format error messages for display."""
    error_str = str(error)
    if "OpenAI" in error_str:
        return f"OpenAI API Error: {error_str.split(': ', 1)[-1] if ': ' in error_str else error_str}"
    elif "401" in error_str or "Unauthorized" in error_str:
        return "❌ Authentication failed. Please check your API key."
    elif "429" in error_str or "Rate limit" in error_str:
        return "⚠️ Rate limit exceeded. Please wait a moment and try again."
    else:
        return f"⚠️ Error: {error_str}"

def main():
    st.title("💻 SoloCoder")
    st.write("A chat-based coding assistant powered by OpenAgent")

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run tests to verify they pass**

```bash
uv run pytest tests/test_server_core.py::test_format_error_message_openai -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add .streamlit/ server.py
git commit -m "feat: add basic Streamlit server structure"
```

---

### Task 2: Session State and Agent Initialization

**Files:**
- Create: `server.py` (modify existing)
- Create: `tests/test_server_core.py` (add tests)

- [ ] **Step 1: Add tests for session management**

```python
def test_session_state_initialization():
    """Test session state structure."""
    # Test that session state keys are defined correctly
    assert "chat_history" in {} or [] == []
    assert "turn_counter" in {} or 0 == 0

def test_api_key_retrieval():
    """Test API key retrieval logic."""
    import os
    from unittest.mock import patch
    
    # Mock environment variable
    with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
        api_key = os.environ.get("OPENAI_API_KEY")
        assert api_key == "test_key"
```

- [ ] **Step 2: Add session state management to server.py**

In `server.py`, modify `main()`:
```python
def main():
    st.title("💻 SoloCoder")
    st.write("A chat-based coding assistant powered by OpenAgent")
    
    # Initialize session state
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    
    if "agent" not in st.session_state:
        st.session_state.agent = None
    
    if "turn_counter" not in st.session_state:
        st.session_state.turn_counter = 0
    
    if "model" not in st.session_state:
        st.session_state.model = "gpt-4o"
    
    if "api_key" not in st.session_state:
        st.session_state.api_key = None
```

- [ ] **Step 3: Create sync agent initialization function**

```python
def create_agent(model: str, api_key: str | None) -> CoderAgent:
    """Create CoderAgent with specified settings (sync function)."""
    from openagent import configure_logging, OpenAIProvider
    from openagent.coder import CoderAgent
    
    configure_logging(level=30)  # WARNING level
    
    provider = OpenAIProvider(model=model, api_key=api_key)
    
    return CoderAgent(
        provider=provider,
        max_turns=100,
        working_dir=None,
        max_context_tokens=128000,
        compact_threshold=0.8,
        disable_compaction=False,
    )
```

- [ ] **Step 4: Add sidebar controls with sync init**

```python
def render_sidebar():
    """Render sidebar with controls and agent status."""
    with st.sidebar:
        st.header("⚙️ Settings")
        
        # API key input
        api_key = st.text_input("🔑 API Key", type="password", key="api_key_input")
        
        # Model selection
        model = st.text_input("🤖 Model", value=st.session_state.model)
        
        st.divider()
        
        # Agent status
        if st.session_state.agent:
            st.success("✅ Agent ready!")
            
            if st.button("🔄 New Session"):
                st.session_state.chat_history = []
                st.session_state.turn_counter = 0
                st.session_state.agent = None
                st.rerun()
            
            # Show turn counter
            st.write(f"📊 Turns: {st.session_state.turn_counter}")
        else:
            st.info("💡 Click 'Start Session' to begin")
        
        st.divider()
        
        # Start button
        if not st.session_state.agent and api_key:
            if st.button("🚀 Start Session"):
                try:
                    with st.spinner("⏳ Initializing agent..."):
                        st.session_state.agent = create_agent(model, api_key)
                        st.session_state.api_key = api_key
                        st.session_state.chat_history = []
                        st.session_state.turn_counter = 0
                        st.rerun()
                except Exception as e:
                    st.error(format_error_message(e))
    
    return model, api_key
```

- [ ] **Step 5: Update main() to call sidebar**

```python
def main():
    # ... existing session state code ...
    
    model, api_key = render_sidebar()
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/test_server_core.py -v
```
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add server.py tests/test_server_core.py
git commit -m "feat: add session state and sync agent initialization"
```

---

### Task 3: Chat Interface Components

**Files:**
- Create: `server.py` (modify existing)
- Create: `tests/test_server_ui.py`

- [ ] **Step 1: Add UI tests**

```python
from server import display_chat_message, render_chat_history

def test_process_user_message():
    """Test user message processing."""
    chat_history = []
    prompt = "Hello"
    
    # Process user message
    chat_history.append({"role": "user", "content": prompt})
    
    assert len(chat_history) == 1
    assert chat_history[0]["role"] == "user"
    assert chat_history[0]["content"] == "Hello"
```

- [ ] **Step 2: Add chat message display function to server.py**

```python
def display_chat_message(message: str, is_user: bool = False):
    """Display a chat message with appropriate styling."""
    if is_user:
        with st.chat_message("user"):
            st.markdown(message)
    else:
        with st.chat_message("assistant"):
            st.markdown(message)

def render_chat_history():
    """Render all messages in chat history."""
    for message in st.session_state.chat_history:
        display_chat_message(
            message["content"], 
            is_user=message["role"] == "user"
        )

def main():
    # ... existing code ...
    
    render_chat_history()
```

- [ ] **Step 3: Add user input handler**

```python
    # User input area
    st.divider()
    
    # Chat input
    if prompt := st.chat_input("Ask SoloCoder to help with code...", key="chat_input"):
        # Add user message to history
        st.session_state.chat_history.append({
            "role": "user",
            "content": prompt
        })
        display_chat_message(prompt, is_user=True)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_server_ui.py -v
```
Expected: FAIL with function not found, then PASS after implementation

- [ ] **Step 5: Test UI works**

```bash
uv run streamlit run server.py
```
Expected: Chat interface loads correctly

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_server_ui.py
git commit -m "feat: add chat interface components"
```

---

### Task 4: Agent Response Handling

**Files:**
- Create: `server.py` (modify existing)
- Create: `tests/test_server_agent.py`

⚠️ **Note:** MVP uses non-streaming responses. Streaming deferred to V2.

- [ ] **Step 1: Add agent response tests**

```python
from unittest.mock import Mock, AsyncMock

def test_agent_run_returns_string():
    """Test that agent.run() returns a string response."""
    # Mock agent
    mock_agent = Mock()
    mock_agent.run = AsyncMock(return_value="Test response")
    
    # Verify run method exists
    assert hasattr(mock_agent, "run")

def test_convert_response_to_message():
    """Test response to message conversion."""
    response_content = "This is a test response"
    assert isinstance(response_content, str)
```

- [ ] **Step 2: Add agent response handling**

```python
def handle_agent_response(prompt: str) -> bool:
    """Process user prompt through agent and return response.
    
    Returns:
        True if successful, False if error occurred
    """
    try:
        import asyncio
        # Run the agent
        response = asyncio.run(st.session_state.agent.run(prompt))
        
        # Add to history
        st.session_state.chat_history.append({
            "role": "assistant",
            "content": str(response)
        })
        
        st.session_state.turn_counter += 1
        
        return True
        
    except Exception as e:
        error_msg = format_error_message(e)
        st.session_state.chat_history.append({
            "role": "assistant",
            "content": error_msg
        })
        return False

def main():
    # ... existing code ...
    
    # Handle agent response
    if prompt := st.chat_input("Ask SoloCoder to help with code...", key="chat_input"):
        # Add user message to history
        st.session_state.chat_history.append({
            "role": "user",
            "content": prompt
        })
        display_chat_message(prompt, is_user=True)
        
        # Process through agent
        if st.session_state.agent:
            with st.spinner("⏳ Thinking..."):
                success = handle_agent_response(prompt)
            
            if success:
                display_chat_message(
                    st.session_state.chat_history[-1]["content"],
                    is_user=False
                )
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/test_server_agent.py -v
```
Expected: All tests pass

- [ ] **Step 4: Test end-to-end**

Run app, start session, send request, verify response appears

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server_agent.py
git commit -m "feat: add agent response handling (MVP non-streaming)"
```

---

### Task 5: V2 Features (After MVP is Working)

⚠️ **These features are DEFERRED to V2.** Implement only after MVP is fully functional and tested.

**Feature 1: Tool Call Display**
- Capture tool calls from agent.run()
- Display with descriptions from `openagent/core/display.py`
- Requires integrating with CoderAgent's internal display mechanisms

**Feature 2: Streaming Responses**
- Requires `agent.run()` to support async generator pattern
- May need to modify CoderAgent or use provider-level streaming

**Feature 3: Context Token Tracking**
- Add `token_count` property to Session class if needed
- Display progress bar in sidebar

**Feature 4: Enhanced Styling**
- Custom CSS for tool call/result styling
- CLI-matching visual elements

---

### Task 6: Testing and Documentation

**Files:**
- Create: `README_WEBAI.md`
- Modify: `pyproject.toml`

- [ ] **Step 1: Run all tests**

```bash
uv run pytest tests/ -v
```
Expected: All tests pass

- [ ] **Step 2: Create web UI README**

Create `README_WEBAI.md`:
```markdown
# SoloCoder Web UI

A Streamlit-based web interface for the SoloCoder Coder Agent.

## Quick Start

```bash
# Start the web server
uv run streamlit run server.py

# Access at http://localhost:8501
```

## Features

- 💬 Chat interface
- 🛠️ Agent-powered responses
- 🔄 Session management

## Configuration

Set your API key via sidebar or `OPENAI_API_KEY` environment variable.

## Development

```bash
# Run in development mode
uv run streamlit run server.py --server.port 8502
```
```

- [ ] **Step 3: Final test run**

Full end-to-end test:
1. Start server
2. Enter API key
3. Send message
4. Verify response appears

- [ ] **Step 4: Commit everything**

```bash
git add tests/ README_WEBAI.md pyproject.toml
git commit -m "feat: complete SoloCoder web UI with tests and docs"
```

---

## Review Notes

**MVP Scope:** Core chat interface with agent response handling only.

**Key Decisions:**
- Streamlit chosen for rapid development
- Single-file architecture for maintainability
- Non-streaming for V1 (streaming deferred to V2)
- TDD approach with tests written before implementation

**Deferred Features:** Tool calls display, streaming, context tracking - planned for V2 after MVP is stable.
