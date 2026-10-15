# React Web UI Live-Tab History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the React web UI remember chat history only during the current open tab session and start empty after reload.

**Architecture:** The fix stays frontend-focused in `frontend/src/contexts/AgentContext.tsx`. We remove message persistence to browser storage so the React context remains the only source of truth during a mounted app session, while the existing websocket/session flow continues to handle live in-tab chat updates.

**Tech Stack:** React 19, TypeScript, Vite 6, React Context, WebSocket, Vitest or existing frontend test runner if present

---

## File Structure

- `frontend/src/contexts/AgentContext.tsx` - owns chat state initialization, websocket lifecycle, and any message persistence side effects
- `frontend/src/types/agent.ts` - shared frontend message and state types, only if a test helper or typing adjustment is needed
- `frontend/src/contexts/__tests__/AgentContext.test.tsx` or nearest existing frontend test location - regression tests for live-tab-only state behavior
- `frontend/package.json` - only if a frontend test script/dependency is needed to run the new tests cleanly

## Workspace Setup

This plan is for the React UI repository at `/Users/taozeng/Projects/SoloCoder_react`, not the main
Streamlit repository checkout. Execute it in a clean isolated worktree from `feature/react-ui`
because the existing React checkout is dirty.

### Task 0: Create a clean isolated workspace

**Files:**
- Verify: `.worktrees/`
- Verify: `frontend/package.json`
- Verify: `backend/main.py`

- [ ] **Step 1: Create a clean worktree from `feature/react-ui`**

Run: `git worktree add .worktrees/react-live-tab-history feature/react-ui`
Expected: a clean checkout is created for the React UI work.

- [ ] **Step 2: Verify you are in the React repository worktree**

Run: `pwd && ls frontend backend`
Expected: the working directory is the new React worktree and both `frontend/` and `backend/` exist.

- [ ] **Step 3: Verify the frontend manifest exists**

Run: `read frontend/package.json`
Expected: package manifest for the Vite React app is present.

### Task 1: Add failing tests for live-tab-only initialization

**Files:**
- Create: `frontend/src/contexts/__tests__/AgentContext.test.tsx`
- Reference: `frontend/src/contexts/AgentContext.tsx`
- Modify: `frontend/package.json` (only if a missing test script blocks execution)

- [ ] **Step 1: Write a failing test proving stored messages are ignored on provider initialization**

```tsx
it('starts with empty messages even when storage contains prior chat', () => {
  window.localStorage.setItem(
    'solocoder-messages',
    JSON.stringify([{ id: '1', role: 'user', content: 'old message' }]),
  )

  render(
    <AgentProvider>
      <TestConsumer />
    </AgentProvider>,
  )

  expect(screen.getByTestId('message-count')).toHaveTextContent('0')
})
```

- [ ] **Step 2: Write a failing test proving a fresh provider mount behaves like a reload and starts empty**

```tsx
it('starts fresh after remount instead of restoring prior messages', () => {
  window.localStorage.setItem(
    'solocoder-messages',
    JSON.stringify([{ id: '1', role: 'assistant', content: 'persisted' }]),
  )

  const { unmount } = render(
    <AgentProvider>
      <TestConsumer />
    </AgentProvider>,
  )
  unmount()

  render(
    <AgentProvider>
      <TestConsumer />
    </AgentProvider>,
  )

  expect(screen.getByTestId('message-count')).toHaveTextContent('0')
})
```

- [ ] **Step 3: Run the targeted frontend test command and verify it fails for the intended reason**

Run: `npm test -- AgentContext.test.tsx`
Expected: FAIL because `AgentContext` still restores messages from storage.

- [ ] **Step 4: Commit the RED tests**

```bash
git add frontend/src/contexts/__tests__/AgentContext.test.tsx frontend/package.json
git commit -m "test: cover live-tab-only chat initialization"
```

### Task 2: Add coverage for in-tab message accumulation without persistence coupling

**Files:**
- Modify: `frontend/src/contexts/__tests__/AgentContext.test.tsx`
- Reference: `frontend/src/contexts/AgentContext.tsx`

- [ ] **Step 1: Write a test proving messages still accumulate in memory during the current mounted session**

```tsx
it('keeps messages in memory during the current tab session', async () => {
  render(
    <AgentProvider>
      <InteractiveTestConsumer />
    </AgentProvider>,
  )

  await userEvent.click(screen.getByRole('button', { name: /send test message/i }))

  expect(screen.getByTestId('message-count')).toHaveTextContent('1')
  expect(screen.getByTestId('latest-message')).toHaveTextContent('hello')
})
```

- [ ] **Step 2: Ensure the test does not assert on `localStorage` writes as part of success behavior**

Keep the assertion focused on `AgentContext` state exposed through the test consumer. Do not encode storage writes into the expected behavior.

- [ ] **Step 3: Run the targeted frontend test command and record the current result**

Run: `npm test -- AgentContext.test.tsx`
Expected: this accumulation test may already PASS before the fix; the important requirement is that it
still passes after persistence is removed.

- [ ] **Step 4: Commit the additional coverage**

```bash
git add frontend/src/contexts/__tests__/AgentContext.test.tsx
git commit -m "test: cover in-tab chat state behavior"
```

### Task 3: Implement live-tab-only AgentContext behavior

**Files:**
- Modify: `frontend/src/contexts/AgentContext.tsx`
- Modify: `frontend/src/contexts/__tests__/AgentContext.test.tsx` (only for minimal harness alignment)
- Modify: `frontend/package.json` (only if required to support test execution)

- [ ] **Step 1: Remove message restore logic from provider initialization**

```tsx
const DEFAULT_STATE: AgentState = {
  messages: [],
  currentTurn: { inProgress: false, toolCalls: [] },
  error: null,
}

const [state, setState] = useState<AgentState>(DEFAULT_STATE)
```

- [ ] **Step 2: Remove message-save side effects tied to `state.messages`**

Delete the effect that writes chat messages to browser storage on every message update.

- [ ] **Step 3: Keep the existing live websocket/session flow intact for the current mounted session**

Preserve:
- message append behavior for user sends
- streamed assistant updates
- tool-call updates
- current turn progress state

Do not add new persistence mechanisms.

- [ ] **Step 4: Make the smallest test-harness adjustments needed so the RED tests can observe context state cleanly**

If needed, add tiny test-only consumers in the test file rather than changing production components.

- [ ] **Step 5: Run the targeted frontend tests and verify they pass**

Run: `npm test -- AgentContext.test.tsx`
Expected: PASS

- [ ] **Step 6: Commit the GREEN implementation**

```bash
git add frontend/src/contexts/AgentContext.tsx frontend/src/contexts/__tests__/AgentContext.test.tsx frontend/package.json
git commit -m "fix: keep React chat history live-tab only"
```

### Task 4: Verify the React web UI slice

**Files:**
- Verify: `frontend/src/contexts/AgentContext.tsx`
- Verify: `frontend/src/contexts/__tests__/AgentContext.test.tsx`
- Verify: `frontend/package.json`

- [ ] **Step 1: Run the full relevant frontend verification command**

Run: `npm test`
Expected: all frontend tests PASS.

- [ ] **Step 2: Optionally run the frontend build to catch integration issues**

Run: `npm run build`
Expected: PASS

- [ ] **Step 3: Inspect the final diff for scope control**

Run: `git diff -- frontend/src/contexts/AgentContext.tsx frontend/src/contexts/__tests__/AgentContext.test.tsx frontend/package.json`
Expected: only the planned live-tab-history changes and test support changes are present.

- [ ] **Step 4: Commit the final implementation state**

```bash
git add frontend/src/contexts/AgentContext.tsx frontend/src/contexts/__tests__/AgentContext.test.tsx frontend/package.json
git commit -m "fix: remove React chat reload persistence"
```
