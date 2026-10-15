# Web UI Live Session History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Streamlit web UI remember conversation history for the current live tab session by using the agent session as the authoritative history source.

**Architecture:** `server.py` will stop treating `st.session_state.chat_history` as canonical state once an agent exists. The UI will render from `st.session_state.agent.session.messages`, and error messages will be appended into that same session so the displayed transcript matches what the agent remembers during the live tab session.

**Tech Stack:** Python 3.11+, Streamlit, pytest, OpenAgent `Session`/`Agent` types

---

## File Structure

- `server.py` - Streamlit UI flow, session initialization, history rendering, and response handling
- `tests/test_server_ui.py` - regression tests for session-backed chat rendering and error handling
- `tests/test_server_core.py` - keep existing core coverage untouched unless a small helper belongs there

### Task 1: Add failing tests for session-backed rendering

**Files:**
- Modify: `tests/test_server_ui.py`
- Reference: `server.py`

- [ ] **Step 1: Replace the placeholder UI test with a failing test for session-derived chat entries**

```python
from openagent.core.session import Session
from server import session_messages_to_chat_history


def test_session_messages_to_chat_history_filters_and_flattens_text():
    session = Session()
    session.add("system", "hidden")
    session.add("user", "Hello")
    session.add("assistant", "Hi there")

    history = session_messages_to_chat_history(session.messages)

    assert history == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]
```

- [ ] **Step 2: Add a failing test for block-based assistant messages**

```python
from openagent.core.types import Message, TextBlock, ToolUseBlock


def test_session_messages_to_chat_history_joins_text_blocks_only():
    messages = [
        Message(
            role="assistant",
            content=[
                TextBlock(text="First"),
                ToolUseBlock(id="1", name="read", arguments={"file": "x"}),
                TextBlock(text="Second"),
            ],
        )
    ]

    history = session_messages_to_chat_history(messages)

    assert history == [{"role": "assistant", "content": "First\n\nSecond"}]
```

- [ ] **Step 3: Add a failing test proving block-only non-text messages are skipped**

```python
from openagent.core.types import Message, ToolUseBlock


def test_session_messages_to_chat_history_skips_messages_without_text_blocks():
    messages = [
        Message(
            role="assistant",
            content=[ToolUseBlock(id="1", name="read", arguments={"file": "x"})],
        )
    ]

    assert session_messages_to_chat_history(messages) == []
```

- [ ] **Step 4: Run the UI tests to verify they fail**

Run: `uv run pytest tests/test_server_ui.py -v`
Expected: FAIL because `session_messages_to_chat_history` does not exist yet.

- [ ] **Step 5: Commit the failing tests**

```bash
git add tests/test_server_ui.py
git commit -m "test: cover session-backed web UI history"
```

### Task 2: Add failing regression tests for live-session behavior

**Files:**
- Modify: `tests/test_server_ui.py`
- Reference: `server.py`

- [ ] **Step 1: Add a failing test proving successful turns update the authoritative agent session**

```python
def test_handle_agent_response_keeps_history_in_agent_session(monkeypatch):
    class FakeAgent:
        def __init__(self):
            self.session = Session()

        async def run(self, prompt: str):
            self.session.add("user", prompt)
            self.session.add("assistant", "Done")
            return "Done"

    # Intentionally omit chat_history to prove the implementation no longer depends on it.
    fake_state = SimpleNamespace(agent=FakeAgent(), turn_counter=0)
    monkeypatch.setattr(server.st, "session_state", fake_state)

    assert handle_agent_response("Hello") is True
    assert session_messages_to_chat_history(fake_state.agent.session.messages) == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Done"},
    ]
```

- [ ] **Step 2: Add a failing test proving error messages are written into the agent session**

```python
def test_handle_agent_response_appends_formatted_error_to_agent_session(monkeypatch):
    class FakeAgent:
        def __init__(self):
            self.session = Session()

        async def run(self, prompt: str):
            self.session.add("user", prompt)
            raise Exception("401: Unauthorized")

    fake_state = SimpleNamespace(agent=FakeAgent(), turn_counter=0)
    monkeypatch.setattr(server.st, "session_state", fake_state)

    assert handle_agent_response("Hello") is False
    assert session_messages_to_chat_history(fake_state.agent.session.messages) == [
        {"role": "user", "content": "Hello"},
        {
            "role": "assistant",
            "content": "❌ Authentication failed. Please check your API key.",
        },
    ]
```

- [ ] **Step 3: Add a failing test for New Session reset semantics**

```python
def test_reset_session_state_clears_agent_and_turn_counter():
    state = SimpleNamespace(agent=object(), turn_counter=3, api_key="secret")

    reset_session_state(state)

    assert state.agent is None
    assert state.turn_counter == 0
    assert state.api_key is None
```

- [ ] **Step 4: Add any missing test imports and helpers**

Add imports such as `types.SimpleNamespace`, `server`, `Session`, `handle_agent_response`, and a
small reset helper target from `server.py`. Keep the tests behavioral and isolated from Streamlit UI rendering internals.

- [ ] **Step 5: Run the targeted tests to verify they fail for the intended reasons**

Run: `uv run pytest tests/test_server_ui.py -v`
Expected: FAIL because the new live-session behavior and reset helper are not implemented yet.

- [ ] **Step 6: Commit the failing regression tests**

```bash
git add tests/test_server_ui.py
git commit -m "test: add live session history regressions"
```

### Task 3: Implement session-backed chat rendering in `server.py`

**Files:**
- Modify: `server.py`
- Test: `tests/test_server_ui.py`

- [ ] **Step 1: Add the minimal helper that converts session messages into renderable chat entries**

```python
def session_messages_to_chat_history(messages: list[Message]) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for message in messages:
        if message.role not in {"user", "assistant"}:
            continue
        if isinstance(message.content, str):
            text = message.content
        else:
            text_parts = [block.text for block in message.content if isinstance(block, TextBlock)]
            if not text_parts:
                continue
            text = "\n\n".join(text_parts)
        history.append({"role": message.role, "content": text})
    return history
```

- [ ] **Step 2: Update `render_chat_history()` to render from the agent session when available**

```python
def render_chat_history():
    if st.session_state.agent:
        history = session_messages_to_chat_history(st.session_state.agent.session.messages)
    else:
        history = []

    for message in history:
        display_chat_message(message["content"], is_user=message["role"] == "user")
```

- [ ] **Step 3: Add a tiny reset helper for New Session behavior**

```python
def reset_session_state(state):
    state.agent = None
    state.turn_counter = 0
    state.api_key = None
```

- [ ] **Step 4: Use the reset helper from the New Session button path**

Replace the inline reset block with a call to `reset_session_state(st.session_state)`.

- [ ] **Step 5: Remove the manual user-message append from the chat input path**

```python
if prompt := st.chat_input("Ask SoloCoder to help with code...", key="chat_input"):
    if st.session_state.agent:
        with st.spinner("⏳ Thinking..."):
            success = handle_agent_response(prompt)
```

- [ ] **Step 6: Update `handle_agent_response()` to rely on agent session for success cases**

```python
def handle_agent_response(prompt: str) -> bool:
    try:
        asyncio.run(st.session_state.agent.run(prompt))
        st.session_state.turn_counter += 1
        return True
    except Exception as e:
        error_msg = format_error_message(e)
        st.session_state.agent.session.add("assistant", error_msg)
        return False
```

- [ ] **Step 7: Remove obsolete `chat_history` initialization/reset logic if no longer needed**

Delete the `chat_history` session-state initialization and reset writes from `server.py` so the UI no longer maintains a second canonical conversation store.

- [ ] **Step 8: Run the targeted UI tests to verify they pass**

Run: `uv run pytest tests/test_server_ui.py -v`
Expected: PASS

- [ ] **Step 9: Commit the rendering fix**

```bash
git add server.py tests/test_server_ui.py
git commit -m "fix: render web UI history from agent session"
```

### Task 4: Verify the full server test slice

**Files:**
- Verify: `server.py`
- Verify: `tests/test_server_core.py`
- Verify: `tests/test_server_ui.py`

- [ ] **Step 1: Run the broader server test suite**

Run: `uv run pytest tests/test_server_core.py tests/test_server_ui.py -v`
Expected: PASS

- [ ] **Step 2: Manually inspect the final diff**

Run: `git diff -- server.py tests/test_server_ui.py tests/test_server_core.py`
Expected: only the planned session-history changes and tests are present.

- [ ] **Step 3: Commit the final implementation state**

```bash
git add server.py tests/test_server_ui.py tests/test_server_core.py
git commit -m "fix: keep live web UI session history in agent state"
```
