# Web UI Live Session History Design

## Goal

Fix the Streamlit web UI so it remembers conversation history for the current live tab session.
This does not require persistence across browser refreshes, browser restarts, or across users.

## Problem

The current implementation in `server.py` keeps two separate representations of the same
conversation:

- `st.session_state.chat_history` drives what the UI renders.
- `st.session_state.agent.session` drives what the agent actually remembers.

That split creates drift risk. A turn can appear in the UI but not in the agent session, or
vice versa, depending on where the update happens. For an in-memory Streamlit session, the
most reliable fix is to make one store authoritative instead of manually keeping two copies
aligned.

## Recommended Approach

Use the agent session as the single source of truth once an agent exists.

Design decisions:

- Keep `st.session_state.agent` alive for the current Streamlit session.
- Render chat history from `st.session_state.agent.session.messages` instead of from a separate
  `chat_history` list.
- Remove duplicate assistant-history writes from UI-only state.
- Keep only minimal UI state in Streamlit: agent instance, turn counter, model, and API key.
- Preserve the existing `New Session` behavior by clearing the agent and counters.

## Alternatives Considered

### 1. Keep dual state with sync helpers

This would add helper functions that write each turn into both `chat_history` and
`agent.session`.

Why not recommended:

- still duplicates state
- easier to regress later
- requires every message path to stay perfectly synchronized

### 2. Serialize and rehydrate agent session inside `st.session_state`

This would snapshot the agent conversation after each turn and recreate the agent from that
serialized state if needed.

Why not recommended:

- more code than needed for live-tab-only memory
- useful for refresh/reopen persistence, which is explicitly out of scope

## Detailed Design

### Session initialization

Keep Streamlit session initialization focused on these keys:

- `agent`
- `turn_counter`
- `model`
- `api_key`

Do not treat `chat_history` as the canonical conversation store.

### Rendering

Add a helper that converts `Session.messages` into renderable chat entries for the web UI.

Rules:

- preserve message order exactly as stored in the session
- ignore `system` messages entirely
- for string-backed messages, render the string content for `user` and `assistant` roles
- for block-backed messages, collect only `TextBlock` content in order and join those text blocks
  with two newlines
- if a block-backed message has no text blocks, render nothing for that message
- ignore tool-use and tool-result blocks in the MVP UI

This keeps the UI aligned with what the model actually sees.

### User turn handling

When the user submits a prompt:

- do not append the prompt to a separate `chat_history` structure
- call `agent.run(prompt)` immediately; `Agent.run()` already appends the user message into the
  authoritative session before the provider call
- rely on the normal Streamlit rerender to show the updated conversation from session-backed data

If the UI wants the user message to appear before the model finishes, it may render the current
submitted prompt optimistically for that render pass only, but it must not store that optimistic
display in a second canonical history structure.

### Assistant turn handling

`handle_agent_response()` should rely on the agent's own session updates for successful turns.

For error cases, the UI should append a plain assistant text message directly into
`st.session_state.agent.session`. That keeps the failure visible in the same authoritative
conversation that the user sees for the rest of the live session. The UI should not maintain a
separate error-only display path.

### New session reset

The `New Session` button should:

- drop the current agent instance
- reset the turn counter
- clear stored API-key/session linkage as it does today

After reset, the next started agent begins with a fresh empty session.

## Testing

Add regression tests before implementation.

Target coverage:

- a test that proves rendered chat entries can be derived from a `Session`
- a test that verifies a successful turn stores history in the agent session rather than only in
  UI state
- a test that verifies `New Session` semantics still clear active conversation state

The tests should fail against the current split-state design for the behaviors being changed,
then pass after the implementation.

## Risks and Mitigations

- Session contains non-chat messages: filter rendering to user/assistant text messages only.
- Error-path inconsistency: keep one explicit path for assistant-visible errors and test it.
- Streamlit rerun behavior: rely on the existing `st.session_state.agent` object for live-tab
  continuity rather than adding new persistence layers.

## Out of Scope

- persistence across refresh/reopen
- multi-user shared history
- tool-call transcript rendering improvements
- long-term session save/load UI
