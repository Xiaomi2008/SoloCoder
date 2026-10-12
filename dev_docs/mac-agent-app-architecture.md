# SoloCoder → Mac General Agent App

**Status:** proposal, v1
**Goal:** turn SoloCoder from a local coding CLI into a **general-purpose Mac agent** (coding is one capability among many) that runs on **local LLMs** and **subscription cloud models** (ChatGPT Plus/Pro, Claude Pro/Max), with API keys as the officially supported fallback.

---

## 1. Assets we already have (and keep)

| Asset | LOC | Reuse verdict |
|---|---|---|
| `openagent/core/` — Agent loop, types, `@tool` registry, Session, task/skill manager, memory+learning, retry | 4,617 | **Keep.** This is the engine. |
| `openagent/provider/` — BaseProvider + openai / anthropic / google / ollama | 1,090 | **Refactor.** Needs a registry, auth modes, model catalog. |
| `openagent/tools/builtin.py` — 26 tools (read/write/edit/glob/grep/bash/awk/sed/web_search/web_fetch/plan/todo/recall/skill) | 1,899 | **Keep.** Already general, not coding-only. |
| `openagent/tools/computer_use.py` — screenshot/click/type/key/scroll (pyautogui, vision coords) | ~600 | **Replace** with native macOS Accessibility. |
| `openagent/infrastructure/mcp.py` — MCP stdio/SSE client | 16 | **Keep + grow.** MCP is the plugin surface. |
| `openagent/apps/solocoder/` — app-as-a-package pattern | 241 | **Keep the pattern.** Add sibling apps. |
| `frontend/` — React 19 + Vite + Tailwind + WebSocket chat UI | ~12 files | **Retire.** v1 UI is 100% native SwiftUI (§2.1). Keep the React app as a dev-only reference for one phase, then delete. |
| `cli_coder.py`, `server.py` (Streamlit) | 41K / 7K | Keep CLI as the headless/power-user surface. |
| Tests | 282 passing, 12.7s | **Baseline.** Every phase must keep this green. |

**Known rot to fix in Phase 0:** two `Agent` classes (`core/agent.py` + `runtime/agent.py`), duplicate provider imports in `openagent/__init__.py`, `pyproject.toml` author = "Anthropic", README clone URL `github.com/your-repo/SoloCoder`, `datetime.utcnow()` (gone in 3.16), `asyncio.iscoroutinefunction` (gone in 3.16), Pydantic class-based `Config`, `SecretManager` writing plaintext keys into `./secrets/secrets.json`.

---

## 2. Target architecture — three layers

```
┌──────────────────────────────────────────────────────────────┐
│  macapp/  SwiftUI app (macOS 14+, universal)                 │
│  menu bar · windows · notifications · App Intents/Shortcuts  │
│  Keychain · TCC permission prompts · notifications           │
│  100% native SwiftUI UI — no WKWebView, no web assets        │
│  native tools: Accessibility, CGEvent, AppleScript, AppKit   │
└───────────────┬──────────────────────────────────────────────┘
                │  JSON-RPC 2.0 + JSONL streaming
                │  over Unix domain socket (launchd-spawned)
┌───────────────▼──────────────────────────────────────────────┐
│  openagent/service/  agent service (Python, bundled)         │
│  sessions · runs · event stream · permission broker          │
├──────────────────────────────────────────────────────────────┤
│  openagent/provider/  ProviderRegistry + ModelCatalog        │
│   local:  LM Studio · Ollama · MLX-LM · llama.cpp · vLLM     │
│   cloud:  api_key  |  oauth_subscription (ChatGPT, Claude)   │
├──────────────────────────────────────────────────────────────┤
│  openagent/core/ + tools/ + apps/   (existing engine)        │
└──────────────────────────────────────────────────────────────┘
```

### Why SwiftUI shell + Python sidecar
- The engine is Python and 100% reusable; rewriting it in Swift is a 6-month detour.
- A Mac app needs things Python cannot do well: **Accessibility API** (real computer-use, not pyautogui), **Keychain**, **TCC permission prompts** (Screen Recording, Accessibility, Automation), **notifications**, **menu bar**, **App Intents / Shortcuts / Spotlight**, notarization + App Store entitlements.
- **The UI is native SwiftUI end to end.** No WKWebView, no bundled web bundle, no JS bridge. Every surface — transcript, tool calls, diffs, terminal, settings, permission prompts — is SwiftUI/AppKit, so the app inherits system fonts, dark mode, SF Symbols, VoiceOver, keyboard navigation, Services menu, and full-screen/Stage Manager behavior for free.
- Alternative considered: **Tauri 2** (Rust shell + sidecar). Faster to scaffold, cross-platform, but weaker on Accessibility/App Intents/Keychain integration — the exact things that make this a *Mac* agent. Chosen: SwiftUI.

### Wire protocol
`openagent/service/` exposes JSON-RPC 2.0 over a Unix socket, one JSON object per line:

```
→ run.start {session_id, input, model_ref, tools, mode}
← event  run.started / message.started / message.delta / tool_call.started
         tool_call.completed / message.completed / run.completed / run.failed
         permission.request / context.compaction.started
→ run.cancel {run_id}   ·   permission.respond {request_id, decision}
→ model.list · model.probe {model_ref} · auth.status · auth.login {provider}
→ session.list/load/save · config.get/set
```
The event names map 1:1 to the existing `openagent/runtime/events.py` dataclasses — the runtime already emits this shape, so the service layer is mostly a serializer + dispatcher.

---

## 2.1 Native UI layer (SwiftUI)

**Stack:** Swift 6 · SwiftUI · AppKit where SwiftUI has gaps · macOS 14+ · universal2 · SwiftPM packages, no Electron, no WebView, no Node in the shipped app.

**Module layout**
```
macapp/
├── App/            @main, WindowGroup/Window, MenuBarExtra, Settings,
│                   Commands (⌘K palette), AppDelegate, TCC + Keychain, notifications
├── AgentClient/    SwiftPM package: JSON-RPC + JSONL over Unix socket →
│                   AsyncThrowingStream<AgentEvent>; reconnect, sidecar lifecycle
├── Core/           @Observable stores: TranscriptStore, RunStore, SessionStore,
│                   ModelStore, PermissionStore, CostMeter
├── UI/             views (§below)
└── NativeKit/      Accessibility/CGEvent/ScreenCaptureKit/AppleScript/EventKit bridges
```

**Data flow (native streaming, no bridge)**
```
socket JSONL → AgentClient (AsyncThrowingStream<AgentEvent>)
            → TranscriptStore (@Observable)  ← coalesces deltas to ~60fps frames
            → SwiftUI views re-render; tool calls render as their own rows
```
Cancellation is structured concurrency: closing the view / ⌘. / app suspend sends `run.cancel`; the sidecar kills the run and its child processes.

**Screens & components (all native)**
| Surface | Native implementation |
|---|---|
| Chat transcript | `ScrollView` + `LazyVStack`, `MarkdownUI`, streaming text, tool-call rows with live status, collapsible reasoning, token/latency footers |
| Code blocks | SwiftTree-sitter syntax highlighting, copy/insert buttons, language chip, wrap toggle |
| Diff viewer | Custom `DiffView` (side-by-side / unified), per-hunk **Accept / Reject** → drives the write/edit permission flow |
| Embedded terminal | **SwiftTerm** for `bash` tool output + an interactive shell tab (real PTY, not a mock) |
| File browser | native `NSOpenPanel`/`NSBrowser` workspace tree, drag-and-drop files/folders into the composer |
| Composer | `TextEditor` with `@` file references, `/` slash commands, model chip, mode switch (plan/act), mic button (native dictation) |
| Inspector | trailing panel: live todos, tool timeline, context-window meter, cost/duration, sub-agent tree |
| Session browser | sidebar, search, resume, fork, export (Markdown/JSON), per-session model + privacy-lock badge |
| Model & auth settings | native `Settings` scenes: providers, local server auto-detect (LM Studio/Ollama/MLX), **Sign in with Claude / Sign in with ChatGPT** buttons, API keys → Keychain, per-role router, cost caps |
| Permission prompts | real macOS sheets/notifications (allow once · allow for session · always allow), never an in-chat text question |
| Computer-use overlay | borderless transparent window outlining the element the agent is targeting, with a **Stop** button |
| Menu bar | `MenuBarExtra` quick capture (⌥Space), active-run progress, one-tap resume, notification on completion |
| Command palette | ⌘K (SwiftUI `Commands` + `.keyboardShortcut`) |

**Native-macOS behaviors we commit to**
- Full keyboard control + **VoiceOver** labels on every interactive element (accessibility is a launch requirement, not a follow-up).
- System appearance, Dynamic Type, SF Symbols, system accent color, focus states.
- Finder **Quick Actions** ("Open with Agent"), Services menu, `agent://` URL scheme, drag-and-drop, Quick Look previews.
- **App Intents / Shortcuts** — agent invokable from Shortcuts, Spotlight, and Siri; dictation via the Speech framework; `AVSpeechSynthesizer` for spoken replies.
- Menu-bar background runs + `UserNotifications` on completion; launchd jobs for scheduled runs.
- Multi-window sessions, tab bar, `⌘\` inspector toggle, state restoration.
- iPhone/iPad companion is a later phase (SwiftUI code is portable; the sidecar is not).

**Third-party Swift deps (few, all native):** `swift-markdown-ui`, `SwiftTerm`, `SwiftTreeSitter` + grammars, `KeyboardShortcuts`, `Sparkle`, `LaunchAtLogin`, `KeychainAccess`.

---

## 3. Model access layer (the core of this request)

### 3.1 `ModelRef` — one address for every model
```python
@dataclass
class ModelRef:
    id: str                  # "claude-opus-4-5", "qwen3.5-35b-a3b"
    provider: str            # anthropic | openai | openai_codex | ollama | lmstudio | mlx | llama.cpp | gemini
    auth: AuthMode           # local | api_key | oauth_subscription
    base_url: str | None     # http://localhost:1234/v1, http://localhost:8080
    caps: Capabilities       # tools, vision, reasoning, streaming, json_mode
    context_window: int
    cost: CostHint           # $0 local | subscription (rate-limited) | per-token
```
`ModelCatalog` = built-in defaults + user-added endpoints + **live discovery** (`GET /v1/models`, Ollama `/api/tags`, MLX `/v1/models`) with capability probing (send a 1-token tool call to learn whether the endpoint really does tools — local servers lie about this constantly).

### 3.2 Local LLMs on Apple Silicon
| Server | Endpoint | Notes |
|---|---|---|
| **LM Studio** | `http://localhost:1234/v1` | current default in SoloCoder; keep |
| **Ollama** | `/api/chat` + `/v1` | native + OpenAI-compat; existing `OllamaProvider` |
| **MLX-LM Server** | `mlx_lm.server` (OpenAI-compat) | **the Apple Silicon path** — MLX is Apple's own framework (see WWDC26 "Run local agentic AI on the Mac"); best memory bandwidth use on unified memory |
| **llama.cpp / llama-server** | `/v1` | GGUF, Metal |
| **vLLM-MLX / Rapid-MLX** | OpenAI **and Anthropic** compat | newer servers speak Anthropic Messages too → the `AnthropicProvider` can drive local models, which matters because tool-calling prompts trained on Claude formatting behave far better behind that API |

**Adapter rule:** one adapter per *wire API* (`openai-chat`, `openai-responses`, `anthropic-messages`, `ollama-native`, `gemini`), one *endpoint descriptor* per vendor. Today's code conflates the two (`OpenAIProvider` = "GPT models, LM Studio"), which is why adding providers has been painful.

**Local-model reality:** tool-calling reliability is the whole ballgame. Mitigations in core: strict tool-JSON repair + one-shot reformat retry, per-model prompt-format templates, a "local mode" system prompt that is more prescriptive, and automatic **escalation to a cloud model** when a local model fails the same tool call twice.

### 3.3 Subscription cloud (OAuth) — verified endpoints

**Claude Pro/Max**
- Authorize `https://claude.ai/oauth/authorize`, PKCE S256, loopback redirect `http://localhost:53692/callback`
- Token `https://console.anthropic.com/v1/oauth/token` (also served at `platform.claude.com/v1/oauth/token`), `grant_type=authorization_code|refresh_token`, scope includes `offline_access`
- Call `https://api.anthropic.com/v1/messages` with `Authorization: Bearer <access_token>` **plus `anthropic-beta: oauth-2025-04-20`** (required for Bearer tokens; add `anthropic-dangerous-direct-browser-access` only if the request originates in the webview)
- JWT carries the account identity; store refresh token in **Keychain**, auto-refresh on 401 once.

**ChatGPT Plus/Pro (Codex path)**
- Authorize `https://auth.openai.com/oauth/authorize`, PKCE S256, `client_id=app_EMoamEEZ73f0CkXaXp7hrann`, `audience=https://api.openai.com/v1`, scope `openid profile email offline_access`
- Token `https://auth.openai.com/oauth/token`
- **Must call `https://chatgpt.com/backend-api/codex/responses`** (Responses API). `api.openai.com/v1/chat/completions` rejects the subscription JWT with 401 — only platform API keys work there.
- Access token JWT has `https://api.openai.com/auth` → `chatgpt_account_id`; send it in the `OpenAI-Organization` / `OpenAI-Project` headers.

**API-key path (officially supported, first-class):** Anthropic `sk-ant-*`, OpenAI `sk-*`, Gemini, plus any OpenAI-compatible base URL.

> **Honest risk note:** subscription OAuth tokens are issued for Anthropic's and OpenAI's own clients. Third-party apps reusing them is a grey area, can break without notice, and can trigger account rate-limits or enforcement. Ship it as an explicit, labelled **"Sign in with your subscription (unofficial, at your own risk)"** option, keep API-key auth as the supported default, and make the auth layer pluggable so a vendor change is a one-file patch.

### 3.4 Router
```
policy: local-first | cloud-first | cheapest-capable | privacy-lock
per-role overrides: { chat: claude-opus, coding: gpt-5-codex, fast: qwen3.5-35b-a3b-local, vision: <multimodal>, embed: nomic-embed }
guards: context-window budget → compact (existing ContextCompaction events) ·
        max cost/run · privacy-lock = local-only for a session ·
        auto-escalate local→cloud on repeated tool-call failure (with a visible banner)
```

---

## 4. General-agent capability layer

Existing: files, shell, grep/glob, web search/fetch, plan mode, todos, memory/recall, skills, MCP, sub-agents.

**Add for "general agent on a Mac":**
1. **Native computer-use** — Swift `AXUIElement` tree (element addressing, not pixel guessing) + `CGEvent` input + `ScreenCaptureKit` screenshots. Exposed to Python as tools over the socket: `get_ui_tree`, `ax_click`, `ax_set_value`, `press`, `type`, `app_launch`, `list_windows`. Keep pyautogui path as a fallback for non-native apps.
2. **Apple apps via AppleScript/JXA + EventKit/Contacts/EventStore** — Calendar, Reminders, Notes, Mail, Contacts, Messages, Finder.
3. **App Intents + Shortcuts** — expose the agent as a Shortcut action; let Shortcuts trigger the agent. This is the distribution story on macOS.
4. **Browser** — Playwright for Chromium automation; Safari via `SFSafariServices`/AppleScript for logged-in sessions.
5. **Voice** — Speech framework dictation input, `AVSpeechSynthesizer` output.
6. **Background runs** — menu-bar agent, notifications on completion, launchd jobs for scheduled runs (e.g. the daily market routine in `~/Projects/stocks`).
7. **Permission broker** — the Mac-app differentiator. Per-tool, per-scope grants with "allow once / allow for session / always allow", persisted to Keychain/config; TCC prompts surfaced in-app. Every privileged tool call routes through it.
8. **Sandbox policy** — default working-directory allowlist, deny-list (`~/.ssh`, Keychain, `.aws`, browser profiles), network egress policy per model (privacy-lock = local only).

---

## 5. Packaging
- Bundle **python-build-standalone** (uv-managed, universal2) inside `macapp/Contents/Resources/runtime/`; first-run creates the venv, "Check for updates" re-resolves deps.
- Entitlements: `com.apple.security.cs.allow-unsigned-executable-memory` (needed by some Python extensions), hardened runtime, **no App Store sandbox** for v1 (a bundled Python + Accessibility is not App-Store-viable) → **Developer ID + notarization + Sparkle** auto-updates, direct download.
- CI: existing Python CI (3.11/3.12) + new macOS job that builds the app, runs `xcodebuild test`, and notarizes on tag.

---

## 6. Roadmap

**Phase 0 — Foundation (1–2 days).** Fix rot: dedupe the two `Agent` classes, clean `openagent/__init__.py`, `pyproject` metadata, README URL, `datetime.utcnow()` → `datetime.now(UTC)`, `asyncio.iscoroutinefunction` → `inspect.`, Pydantic `ConfigDict`. Move `SecretManager` off plaintext `secrets/secrets.json` → `keyring` (macOS Keychain backend). Gate: 282 tests still green.

**Phase 1 — Provider registry + auth (3–5 days).** `ModelRef`/`ModelCatalog`/`ProviderRegistry`; split adapters by wire API; `AuthBroker` (keychain, api_key, oauth); **ChatGPT + Claude OAuth** with PKCE loopback + refresh; live model discovery + capability probe; router with local-first and privacy-lock. CLI gains `agent auth login anthropic-oauth`, `agent models`, `agent use <ref>`. Gate: integration test per auth mode against recorded fixtures; a real run on LM Studio *and* on Claude Max OAuth.

> **Phase 1 status: built** (`openagent/provider/`: `model_ref`, `auth/`, `registry`, `catalog`, `router`, `responses`, `cli`; 428 tests).
>
> Deviations found while building, in rough order of how much they cost to discover:
>
> 1. **ChatGPT subscription tokens do not work on `api.openai.com`.** The subscription backend is the Codex **Responses** API at `chatgpt.com/backend-api/codex`; `/v1/chat/completions` rejects the JWT. This forced a new `openai-responses` adapter (`WireAPI.OPENAI_RESPONSES`) and a `bearer_token` credential concept distinct from `api_key` — an API-key-shaped credential cannot express "this is a Bearer JWT for a different host".
> 2. **Anthropic OAuth is not an API key with a different base URL.** It needs `Authorization: Bearer`, the `anthropic-beta: oauth-2025-04-20` header, and (when the JWT carries it) the `anthropic-account-id` header — decoded from the JWT, not from an endpoint.
> 3. **Keychain account names are a namespace.** `auth:anthropic` (subscription) and `anthropic` (API key) must not collide, and `AuthBroker` must not inherit the legacy plaintext-migration path: reading `auth:anthropic` used to pick up a bare `anthropic` key and rewrite it as a JSON credential blob under a new name, silently moving the user's key.
> 4. **`openagent.providers.ProviderError` is a stream *event*, not an exception.** Raising it raises a dataclass. Registry dependency errors are now `ProviderDependencyError`.
> 5. **A falsy store is still a store.** `store or SecretManager()` made an empty dict-backed test fake fall through to the **real macOS Keychain**, so the suite wrote credentials to the user's machine. `is None` + a guard test that fails loudly on any real-Keychain write.
> 6. **The OAuth loopback listener must bind IPv4 *and* IPv6 loopback.** Binding `::1` only means the browser's redirect to `127.0.0.1:port` never arrives. It must also refuse to bind a non-loopback host: the listener receives the user's access token.
> 7. **`BaseProvider` had no `close()`.** Every adapter owns a client; a long-lived app that builds a provider per turn leaks a connection pool per turn. Added to the base class (sync/async `close` both handled).
> 8. **`ollama` / `google-genai` are extras.** A missing SDK now surfaces as `pip install openagent[ollama]`, not a stack trace.
> 9. **OpenAI-compatible discovery URLs already end in `/v1`.** Joining a `/v1/models` path produced `/v1/v1/models`, which 404s against every real server. Pinned by a test over all endpoints.
> 10. **CLI handlers must be awaitable.** `cli_coder.main()` is async, so a handler calling `asyncio.run()` dies with "cannot be called from a running event loop". Handlers are `handle_*_async`, with sync wrappers for the standalone CLI.
>
> **Gate results.** 428 tests pass, zero warnings. Live gate ran against a real OpenAI-compatible HTTP server on LM Studio's port: discovery listed the served model, `models probe` round-tripped a tool call (233 ms), and a provider built from a `ModelRef` completed a text turn and a tool-call turn, then closed cleanly. LM Studio's own API server was not running during this session, so the literal "run on LM Studio" and "run on Claude Max OAuth" checks are **outstanding** — they need the LM Studio server enabled and a Claude subscription sign-in, respectively.

**Phase 2 — Agent service (3–4 days).** `openagent/service/` JSON-RPC + JSONL event stream over a Unix socket; session CRUD, run cancel, permission-request channel; `openagent serve` command. Gate: a headless TUI client (`openagent tui`) drives a full run through the socket, and a Swift `AgentClient` test-harness passes the same contract tests (stream, cancel, permission round-trip, reconnect).

**Phase 3 — Native Mac app shell (1 week).** SwiftUI app skeleton: windows, `MenuBarExtra`, `Settings`, commands, sidecar lifecycle (spawn/health/restart), Keychain, TCC prompts, notifications, `agent://`. `AgentClient` SwiftPM package consuming the Phase 2 socket. Gate: a native SwiftUI window (no web assets) completes a streamed run on a local model, with cancel working.

**Phase 3.5 — Native UI feature parity (1–2 weeks).** Transcript + streaming, tool-call rows, code blocks (tree-sitter), diff viewer with accept/reject, SwiftTerm terminal, composer (`@` files, `/` commands, dictation), inspector (todos/context/cost), session browser, model & auth settings incl. OAuth sign-in, permission sheets, ⌘K palette. Gate: daily-driver usable — the React UI is deleted from the repo at the end of this phase.

**Phase 4 — Native capabilities (2 weeks).** Accessibility computer-use tools replacing pyautogui; Calendar/Reminders/Notes/Mail; browser; permission broker wired to every privileged tool. Gate: "add a calendar event for X and email me the summary" end-to-end on a local model.

**Phase 5 — General-agent polish (ongoing).** App Intents/Shortcuts, scheduled runs, voice, multi-window sessions, cost meter, per-role model routing UI, plugin docs.

---

## 7. Naming
"SoloCoder" says *coding, local, single machine*. For a general Mac agent, keep `openagent` as the Python package name (it is already accurate) and give the **app** a product name; the coding app stays as `openagent/apps/solocoder/` — one app among several.

## 8. Top risks
1. **Subscription OAuth ToS/breakage** — mitigated by §3.3 labelling + pluggable auth + first-class API keys.
2. **Local model tool-calling quality** — the real limiter; needs §3.2 mitigations and honest per-model capability flags.
3. **Bundled Python + macOS sandbox** — resolved by Developer ID/notarization instead of App Store.
4. **Accessibility API complexity** — element addressing is a real project; keep pyautogui fallback so we never regress.
5. **Two Agent classes** — if not deduped in Phase 0, every later phase pays for it.
6. **Native UI is a bigger lift than a WebView** — accepted deliberately. Mitigations: the socket contract is finished before any UI is written (Phase 2 gates it with a TUI + Swift harness), the component list in §2.1 is fixed scope, and mature native libraries cover markdown/terminal/highlighting. The payoff is the part a WebView never gets: Accessibility, VoiceOver, Keychain, permission sheets, App Intents, and the look/feel that makes it a Mac app.
