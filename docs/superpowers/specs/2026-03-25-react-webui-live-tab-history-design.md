# React Web UI Live-Tab History Design

## Goal

Fix the React web UI so chat history is remembered only during the current open tab session.
Refreshing or reopening the page should start with an empty conversation.

## Problem

The current React implementation in `frontend/src/contexts/AgentContext.tsx` mixes two different
behaviors:

- in-memory React state for the current live tab session
- browser storage restore/save for messages across page reloads

That means the UI can restore old chat bubbles after reload even though the backend websocket uses
a fresh `sessionId` on page load and starts a new agent conversation. The result is misleading:
the frontend appears to remember history longer than the actual live session does.

## Recommended Approach

Make the React context state live-tab-only by removing browser-storage persistence for messages.

Design decisions:

- keep `AgentContext` as the authoritative source of visible chat state during the current tab
- keep websocket-driven streaming and in-memory `messages` accumulation unchanged for live usage
- stop restoring messages from `localStorage` on initialization
- stop writing messages to `localStorage` when state changes
- keep the existing per-load websocket `sessionId` behavior so a reload naturally creates a fresh
  session

## Alternatives Considered

### 1. Clear storage on page load

This would preserve the persistence layer but empty it immediately during initialization.

Why not recommended:

- keeps unnecessary persistence code around
- invites future regressions because storage still appears to be part of the design
- more confusing than simply not persisting at all

### 2. Reconcile restored frontend history with backend session recovery

This would make reload persistence actually work by stabilizing `sessionId` and restoring backend
conversation state too.

Why not recommended:

- directly conflicts with the requested behavior
- adds backend/session complexity that is out of scope

## Detailed Design

### Frontend state initialization

`AgentContext` should initialize with the default empty state for messages and current turn data.

Rules:

- do not load saved messages from browser storage
- do not treat browser storage as a fallback source of truth
- keep only in-memory state for the current mounted app instance

### Frontend state updates

During a normal open-tab chat session:

- user messages continue to append into in-memory `messages`
- websocket events continue to append and stream assistant responses into in-memory `messages`
- tool-call display state continues to derive from the current in-memory conversation state

No change is needed to the current live streaming flow beyond removing persistence side effects.

### Reload behavior

On refresh or reopen:

- the provider should start from the default empty state
- a fresh websocket `sessionId` should be used as it is today
- the visible chat area should begin empty until the user starts a new conversation

This aligns the frontend behavior with the actual backend session lifecycle.

### Backend behavior

No functional backend change is required for this fix.

The backend already behaves like a live-session service through websocket `session_id` usage. The
bug is that the frontend currently implies longer-lived memory than the backend actually provides.

Backend cleanup is out of scope unless a tiny change is required to keep API labels or responses
from contradicting the new frontend behavior.

## Testing

Add regression coverage before implementation.

Target coverage:

- context initialization starts with empty messages even if storage contains prior messages
- message updates during the current mounted session still accumulate correctly in memory
- reload-style reinitialization starts fresh instead of restoring prior messages

Tests should fail against the current persisted-message behavior, then pass after persistence is
removed.

## Risks and Mitigations

- Existing code may rely on storage helper utilities: keep any shared utility in place if other
  consumers still use it, but remove `AgentContext` dependence on it.
- Users may expect reload persistence from previous behavior: the new behavior is intentional and
  should be reflected in tests, not hidden behind partial persistence.
- Dirty worktree risk on `feature/react-ui`: implement in a clean isolated workspace so unrelated
  frontend/backend edits are not mixed with this fix.

## Out of Scope

- restoring backend session state across reloads
- stable cross-reload session IDs
- multi-tab synchronization
- redesigning websocket or REST chat APIs
- broader cleanup of unrelated dirty files in the existing React worktree
