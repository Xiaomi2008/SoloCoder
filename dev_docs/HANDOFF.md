# HANDOFF — resumable state after restart

## Where we are

**Phase 3 in progress: the app runs.** The Python service, the Swift client, the
event-stream state machine and a SwiftUI shell (chat, tool timeline, permission
sheet, model picker, session browser) are done and green, and the app spawns its
own service and drives real runs. What is left in Phase 3 is packaging: a frozen
sidecar binary inside a `.app`.

- Branch: `feat/computer-use-macos`
- Python suite: **575 passed** (`.venv/bin/python -m pytest tests -q`)
- Swift suite: **235 checks passed** (`swift run agent-checks`), **246** with
  `AGENT_REPO_ROOT` (the app-model checks against a real sidecar); add
  `AGENT_LIVE_MODEL` for a real streamed run with thinking off, a real thinking
  run, and the app model doing two runs on one connection
- Working tree: the phase 3d view work (markdown, tool output, scrolling, the
  reasoning control, the target split) is uncommitted

## Phase 3b+: the app on screen, and why the stream felt slow

The app was invisible: a SwiftPM executable launched from a shell starts
`.prohibited` — no Dock tile, no menu bar, window behind everything.
`setActivationPolicy(.regular)` in `applicationDidFinishLaunching` fixes it. The
enum is `0=regular, 1=accessory, 2=prohibited`, the reverse of what the numbers
suggest.

The streamed answer felt slow. Measured, in order of size:

| What | Cost |
|---|---|
| Model first token (`lan/swift-1.5-iq3_s`) | 18–97 ms |
| Model generation | 8–60 tok/s |
| One turn, real request shape | 1.4–4.4 s |
| Real request shape | 8,461-char system prompt + 19 tool schemas ≈ 18k chars, re-sent every turn |
| Folding one event into `Transcript` | 6.5 µs |
| One published write | a SwiftUI invalidation of every view observing `AppModel` |

The model streams ~1.5 characters per chunk, so a 2k-character answer is ~1300
deltas, and each one invalidated the whole view graph — toolbar, model picker,
session menu, every visible line — while a selectable streaming `Text` rebuilt
its text-kit view per character. Fixed by buffering deltas and publishing on a
60 ms cadence (200 deltas → 4 publishes, checked), making each row its own
`Equatable` view, and turning text selection off mid-stream
(`TranscriptLine.streaming`). `swift run ui-cost` reproduces the fold numbers.

The sidecar no longer outlives the app. Four orphaned services had accumulated
from killed app instances: `willTerminateNotification` does not fire on `kill`,
and nothing fires on SIGKILL. `Sidecar` now hands the service a pipe on stdin and
`AgentService.install_stdin_watchdog()` exits on EOF — the one parent-death
signal that cannot be missed. Verified: `kill -9` the app, the sidecar exits and
unlinks its socket. `/dev/null` stdin is not a parent (checked), so a detached
service still starts.

## Phase 3c+: the ten seconds of nothing

"TG feels slow" was not generation speed. Measured on `swift-1.5-iq3_s` over the
LAN server: first reasoning token at 55–311 ms, **first answer character at
9.5–10.5 s**, ~7,500 chars of private reasoning before ~1,300 chars of answer,
and 150–330 tok/s once the answer starts — matching the server's own benchmark.
The provider read only `delta.content`, so the app showed a frozen spinner for
the whole thinking phase. One default run produced **686,439 characters of
reasoning and no answer at all**.

- `ProviderReasoningDelta` → runtime `reasoning_delta` → protocol event →
  `ReasoningLine` in the timeline: open while streaming, collapsed when the
  answer starts, never joined to the answer, never sent back to the model.
- `run.start` takes `reasoning_effort` (`none|minimal|low|medium|high`), per-run,
  applied to every turn; the toolbar exposes it. `none` answers in 1.1 s.
- The drift test caught the new event being registered in the protocol but
  missing from the `RuntimeEvent` union; the Swift checks caught it missing from
  `Wire.eventTypes` and from the handshake comparison. Each of those is a
  cross-language contract check doing its job.

Publishes in a real tool loop run ~50/s, not the 17/s the 60 ms cadence implies:
every non-delta event flushes the delta buffer to preserve order, and a tool loop
is mostly non-delta events. The synthetic 200-deltas-→-4-publishes number is the
no-interleaving case.

## Phase 3c+: the thinking path was the slow one

"Token generation gets very slow once it's in thinking" was not the model. The
60 ms buffer covered `message_delta` and not `reasoning_delta`, so every thinking
token was a published write — and the thinking stream is the long one: ~7,500
chars before ~1,300 chars of answer, one run at 686,439 chars. The answer-path fix
covered the short half of the run, which is why a run felt fast while answering
and slow while thinking. Three costs, all on that path:

| What | Before | After |
|---|---|---|
| 400 reasoning deltas | 400 publishes | 1 publish |
| Folding a 480k-char thinking stream | 267 ms, per-delta cost 0.4 µs → 5.8 µs | 49 ms, 0.4 µs → 0.5 µs |
| Streaming `Text` of the whole thinking | O(n) layout per publish, O(n²) per run | a 1,200-char tail, constant |

- `AppModel.isDelta` covers both delta kinds: one buffer, one cadence.
- `Transcript` keeps a **tail** of the thinking (`reasoningKeep` 8k, trimmed at
  16k) instead of the whole stream. The thinking is never sent back to the model,
  never part of the answer, never saved as history, and the view shows its tail;
  the answer stays uncapped, because it is the product.
- The copy-on-write trap, measured: appending to a string read out of an array
  element copies the whole string, because the element still shares it. The
  take-out-and-put-back idiom that fixes this under `-O` is **worse** in a debug
  build — 253 ms against 7 ms for 80k appends — because the `let` keeps the string
  shared. Bounding the string is the fix that holds in both builds.
- Checks: 400 reasoning deltas → 1 publish; a 36k-char thinking stream is bounded
  (12,870 chars kept) and keeps its **end**, not its beginning; the live app check
  now runs a *thinking* run on a real model. It did not before — the live run
  asked for `reasoning_effort: none`, so the suite measured the fast half of a
  reasoning model and stayed green while the slow half got slow.

## Phase 3d: the views

The views were the only code no check reached, and the only way to see them was
to launch the app. Four gaps, and a way to look.

- **The answer is markdown.** `Markdown.blocks` (in `AgentAppCore`, 52 checks)
  turns an answer into headings, paragraphs, fenced code, both list kinds, a
  quote, a pipe table and a rule; `MarkdownView` draws them, with inline runs
  left to `AttributedString(markdown:)` on a block's text — never on the whole
  answer, because a fenced block's `**` would be eaten by an inline parser. A
  streaming answer is **not** parsed: O(n) per publish on a growing string is
  O(n²) per run, the thinking stream's trap again. The parser's rule is that no
  input line is ever dropped.
- **The tool row kept its own preview.** `Transcript.preview(of:)` folded
  newlines and cut at 240 characters, so expanding a row showed the arguments
  and never the output. The row keeps the result now — bounded at the **head**
  (8k, and it says so), where the thinking stream is bounded at its **tail**:
  a file starts at the top, and the part of the thinking that matters is the
  part that is arriving.
- **Scrolling followed the line count.** A 2k-character answer is one line, so
  its 1,300 deltas changed no count and the view sat still while the answer ran
  off the bottom; and it scrolled even when the user had scrolled up to read
  something. The signal is now the last line's *value*, acted on only while
  pinned, and `LazyVStack` gives the pin for free through the last line's
  `onAppear`/`onDisappear`. A "Latest" button appears when the user is away.
- **The reasoning control did not exist.** HANDOFF said "the toolbar exposes
  `reasoning_effort`"; no view set it, so the one control that turns the
  ten-seconds-of-nothing into 1.1 s was unreachable. It is in the toolbar now.
  The same bug was in the live check: `model.reasoningEffort = .none` is
  `Optional.none` — nil, "model decides" — so the run the check called the fast
  one was asking for the server default. The compiler warns and picks the wrong
  one; the case is spelled in full now.
- **The views are a library, and there is a preview.** `AgentAppUI` is a target;
  `AgentApp` is the `@main` that opens a window on it; `swift run ui-preview`
  feeds a scripted event stream through the real `AppModel` and opens the real
  `RootView`. No service, no model, no working-directory panel, and it can show
  a failed tool, a refused permission, a 40-line result and a table. Every
  screenshot in this phase came from it.
- **A toolbar group wider than the window overflows into a `>>` chevron**, and
  the window looks fine while the controls are gone. The model picker, the
  Thinking picker, the segmented permission control, the Session menu and the
  window title together exceeded 900 points; the working-directory control moved
  to the status bar, which already shows the path.

## What Phase 2 built

| File | What it is |
|---|---|
| `dev_docs/agent-service-protocol.md` | The contract: NDJSON JSON-RPC over a Unix socket, 16 methods, 19 events, error codes, ordering guarantees, the permission round-trip. The Swift app is written against this document. |
| `openagent/service/protocol.py` | The contract in code: envelope, framing (8 MiB cap), event codec, `EVENT_TYPES` registry, `RpcError` + `ErrorCode`. |
| `openagent/service/server.py` | `AgentService` + `Connection`. One run per connection, reader loop and run task separate, writes serialized, disconnect cancels the run, provider closed after every run, permission timeout denies. |
| `openagent/service/client.py` | `ServiceClient`: handshake, response/event demux, `start_run`, `respond_permission`, `events()`. |
| `openagent/service/cli.py` | `openagent serve` and `openagent tui` (headless client, interactive permission prompts). |
| `openagent/service/scope.py` | `working_dir` validation: absolute, existing, not `/`, inside `--root`. |
| `openagent/service/sessions.py` | Session ids (path-traversal-safe), save/load/list under `~/.openagent/sessions`. |
| `openagent/runtime/runner.py` | `AgentRunner`: the streaming, tool-capable run loop that **emits events instead of printing**. The CLI's `run_loop` prints to a terminal and cannot be a sidecar engine. |
| `openagent/runtime/permissions.py` | Risk classification + gates (`AllowAll`, `ReadOnly`, `Risk` with once/session/never scopes). Unknown tools classify as exec — fail closed. |
| `openagent/runtime/events.py` | Added `permission_requested`, `permission_resolved` (with `reason`), `AgentResult.stop_reason`. |
| `tests/support/streaming_provider.py` | `ScriptedStreamProvider` (turn scripts, tool calls, mid-stream failure, hang) and `HangingProvider`. |
| `tests/service/` | 81 contract tests over a **real socket**, plus fixtures for the Swift package. |
| `scripts/smoke_service.py` | End-to-end smoke over a real socket, sandboxed HOME. |
| `scripts/gen_protocol_fixtures.py` | Regenerates `tests/service/fixtures/{protocol,events}.json` from the Python types. |

## What Phase 2c built — the Swift client

| File | What it is |
|---|---|
| `macapp/AgentClient/Sources/AgentClient/Wire.swift` | Envelope, NDJSON `FrameDecoder` (8 MiB cap), `ErrorCode`, `JSONValue` (the only place that touches `Any`), the shared `convertFromSnakeCase` decoder. |
| `macapp/AgentClient/Sources/AgentClient/AgentEvent.swift` | Every event type 1:1 with the Python dataclasses, `.unknown` for events this client has never heard of. |
| `macapp/AgentClient/Sources/AgentClient/AgentServiceClient.swift` | Actor over `AF_UNIX`: handshake, request/response futures, event stream, `startRun`, `respondPermission`, model/tool/session/auth/config calls. |
| `macapp/AgentClient/Sources/AgentClient/ContractFixtures.swift` | Loads `tests/service/fixtures/*.json` — the fixtures the Python side generates. |
| `macapp/AgentClient/Sources/AgentClientChecks/` | `swift run agent-checks`: the contract suite that runs without Xcode. Spawns the real service and drives it. |
| `macapp/AgentClient/Tests/AgentClientTests/` | The same assertions as a Swift Testing suite, for when the package is opened in Xcode. |
| `macapp/AgentClient/Sources/AgentAppCore/Transcript.swift` | The event stream folded into the timeline a view renders: assistant lines, tool rows, permission prompts, compaction, run state. |
| `macapp/AgentClient/Sources/AgentAppCore/AppModel.swift` | The app's state: runs, permission answers, models, sessions, the session id taken from `run.start`. |
| `macapp/AgentClient/Sources/AgentAppCore/Sidecar.swift` | Spawns the service from the repo's venv in development; logs to a file, not a pipe. |
| `macapp/AgentClient/Sources/AgentApp/` | The SwiftUI shell: `solocoder`. |
| `macapp/README.md` | How to run it, what it covers, and the traps. |

### Gates passed

- `pytest tests -q` → 569 passed.
- `swift run agent-checks` → **165 checks passed**; with
  `AGENT_REPO_ROOT` + `AGENT_LIVE_MODEL=lan/swift-1.5-iq3_s` → **180**: a real
  streamed run (tool calls, ~130 deltas, `run_completed`) folded through
  `Transcript` (8 lines, state "Done"), and the real `AppModel` + `Sidecar`
  finishing a run on a real model (7 lines, session id from `run.start`).
- `swift run solocoder` → the app opens, spawns the service, binds the socket,
  connects (verified by `lsof` on the socket pair; the window is the user's).
- `scripts/smoke_service.py` → `SMOKE OK` (handshake, run, permission round-trip,
  tool execution, streamed deltas, session persistence, reconnect).
- **Real model, real socket** (`lan/swift-1.5-iq3_s` @ 192.168.4.34:8080):
  - streamed run that called `glob`, read the result, answered;
  - `--permission-mode ask`: the TUI was prompted for `bash` and `write`,
    answered `y`, the file was really written, `read` ran unprompted (read-only);
  - a second run on the same `--session` remembered the file the first created
    (resume over a reconnecting client).
- `openagent serve --help` / `openagent tui --help` wired into `cli_coder.py`.

### Phase 1 gates still open (user action)

- `auth login anthropic_oauth` — needs an interactive browser.
- LM Studio API server on `:1234` — not enabled.
- `models use lan/swift-1.5-iq3_s` — left to the user. The router picks
  `lmstudio/qwen3-coder-32b` (unreachable) under `local_first`, so runs need an
  explicit `-m lan/swift-1.5-iq3_s` until a default is chosen.

## Commands

```bash
cd /Users/taozeng/Projects/SoloCoder
.venv/bin/python -m pytest tests -q                 # 569 passed
.venv/bin/python scripts/smoke_service.py           # SMOKE OK
.venv/bin/python cli_coder.py serve --socket /tmp/oa/e2e.sock
.venv/bin/python cli_coder.py tui --socket /tmp/oa/e2e.sock \
    -p "…" -w /tmp/oa/demo -m lan/swift-1.5-iq3_s --permission-mode ask
.venv/bin/python cli_coder.py models list --discover
.venv/bin/python scripts/gen_protocol_fixtures.py   # after any protocol/event change

cd macapp/AgentClient
swift run agent-checks                              # 235 checks passed
AGENT_REPO_ROOT=.. swift run agent-checks           # + the app-model checks
AGENT_REPO_ROOT=.. AGENT_LIVE_MODEL=lan/swift-1.5-iq3_s \
    swift run agent-checks                          # 180, real model
swift run solocoder                                 # the app
swift run ui-preview                                # the views, no service, no model
UI_PREVIEW_SHORT=1 swift run ui-preview             # …top of the timeline
```

## Next: Phase 3b — packaging the sidecar

The app runs from a checkout (`Sidecar` spawns `.venv/bin/python cli_coder.py
serve`). A shipped app embeds a frozen build of the service, and that decision
drags in the rest:

1. **PyInstaller (or similar) build** of `cli_coder.py serve` — the entry point
   exists (`openagent serve`), so this is a spec file plus the hidden imports
   the providers need.
2. **Socket path inside the container**: `~/Library/Application Support/…` or an
   app group, kept short (AF_UNIX ~104 bytes).
3. **Launch/teardown**: the app owns the sidecar's life; SIGTERM cancels the run
   and closes the provider (already true), and `Sidecar.stop()` waits.
4. **Keychain access from a sandboxed app** — the model-server keys live in the
   login keychain; a sandboxed `.app` needs the entitlement, or the config moves.
5. **Code signing + notarization**, icon, `Info.plist`, `.app` bundle assembly
   (SwiftPM executables are not bundles).

## Next: the views, with a screen

Phase 3d did markdown rendering, tool output beyond the preview, and scrolling.
What is left, worth doing with `swift run ui-preview` open:

- the permission sheet's argument rendering — a `bash` prompt shows a JSON
  object, not the command; a `write` shows the whole content blob
- what a long session does to a `LazyVStack` (a 200-line transcript, a 40k-char
  answer, the `Equatable` row skip under real history)
- the composer: `Return` submits a multi-line field, and `Send` is disabled with
  no reason shown
- a copy-answer affordance (`Transcript.finalText` exists; no view uses it)
- the session browser: ids, no titles, no delete

## Things to know (verified the hard way)

- **A `body` containing a `switch` must live in the struct, not an extension.**
  The result-builder transform is applied to a `body` in the type's own
  declaration only; in an extension the branches fail to unify ("branches have
  mismatching types", for branches that are all `View`s).
- **A published write is the unit of SwiftUI work, not a state change.** Folding
  a delta costs 6.5 µs; publishing it costs a view-graph re-render. Buffer the
  flood and publish on a cadence.
- **A selectable `Text` cannot be laid out incrementally.** Text selection is
  off while a line streams.
- **A killed app leaves its sidecar** unless the service watches stdin. Socket
  paths are pid-derived, so the orphan is invisible in the next launch's logs.

- **`ConfigManager.get_config_path()` respects `XDG_CONFIG_HOME`** — tests that
  touch config/auth must isolate `HOME` *and* `XDG_CONFIG_HOME`, and must not
  monkeypatch `ConfigManager._path`. `tests/conftest.py::isolated_config` does this.
- **`RpcError` must not be a frozen dataclass.** Python 3.14's `contextlib`
  assigns `__traceback__` on the way out of `asyncio.timeout`/`suppress`, and a
  frozen exception raises `FrozenInstanceError` *from inside the exception
  handler*. `tests/service/test_protocol.py::test_rpc_error_survives_contextlib_and_asyncio_timeout`
  pins it.
- **`Session.__len__` makes an empty session falsy.** `session or Session()`
  silently discards a caller's fresh session. `AgentRunner` compares to `None`;
  `tests/runtime/test_runner.py::test_runner_preserves_the_callers_session` pins it.
- **`Server.wait_closed()` waits for connection handlers.** A sidecar's clients
  stay connected for the life of the app, so `AgentService.stop()` cancels the
  connection tasks *before* awaiting `wait_closed()`, and background tasks
  (model discovery) are tracked separately from client tasks.
- **macOS `AF_UNIX` paths cap at ~104 bytes.** pytest's `tmp_path` is too long
  for a socket; `tests/service/test_server.py::Harness.sock_dir` uses a short
  `tempfile.mkdtemp(prefix="oa")` and skips if the platform refuses.
- **The service tests isolate `HOME`/`XDG_CONFIG_HOME`** (autouse fixture) —
  every run saves a session, and the suite must not litter `~/.openagent`.
- **`working_dir` is real.** `ToolContext.working_dir` is applied as the cwd of
  `bash` and resolved against `read`/`write`/`edit`/`glob`/`grep`/`apply_patch`.
  The model is given absolute paths; it cannot escape the session directory.
- **`run.start` answers before the first token**, and `permission_requested` is
  emitted *before* the engine blocks on the answer. Both are load-bearing for a
  UI; the contract tests assert them.
- **A denied tool emits no `tool_call_started`.** The refusal goes back to the
  model as an errored tool result, and `permission_resolved.reason` says why
  (user refused / timed out).
- **`--session NAME` creates as well as resumes.** Naming a session for its
  first run is the common case; `session.load` stays strict so a typo is not
  silently blank.
- **Model discovery is a startup task, not a blocking call.** A model on a
  user-added server is only in the catalog after `discover()`; `_resolve_model`
  discovers on a miss so `run.start(model=…)` never needs a prior `model.list`.
- **LM Studio is `:1234`.** `:1235` is a different server; `:8080` is
  llama.cpp/mlx; `:11434` ollama; `:8000` vllm.
- **The LAN model server** `lan` → `http://192.168.4.34:8080/v1`, key in the
  macOS Keychain, model `lan/swift-1.5-iq3_s` (tool-capable, 32k ctx).
- **`auth status` / `models list` are the state checks.** No API keys, no
  subscription sign-ins; `lan` is the only registered server.
- **`swift test` needs Xcode.** XCTest and Swift Testing ship inside
  `Xcode.app`; a Command Line Tools install has the compiler and not the test
  libraries, so `swift test` cannot build a suite here. `swift run agent-checks`
  is the gate that runs everywhere, and `Tests/AgentClientTests/` mirrors it for
  the day the package lands in Xcode.
- **`NSNumber` bridges to `Bool` for 1 and 0.** A JSON `1` answers `as? Bool`
  and becomes `true` — that turned `protocol_version` into a flag. Booleans are
  identified by identity against the `kCFBoolean` singletons before any numeric
  cast, in `JSONValue.fromJSON`, the only function allowed to see `Any`.
- **Every wire decoder uses `.convertFromSnakeCase`; `AgentEvent` does not.**
  The wire is snake_case by design, so result structs are camelCase. Event
  payloads are `JSONValue` dictionaries whose keys are snake_case literals — the
  strategy would rewrite them.
- **Raw values are wire literals.** `PermissionMode.allowReadonly` must be
  `"allow_readonly"`. `JSONEncoder` emits the raw value, and the server answers
  `permission_mode must be one of allow_all, allow_readonly, ask`. The live
  check caught this; a unit test against the fixtures would not have.
- **`config.get` takes `keys` (a list) and answers `{"config": {…}}`**, narrowed
  to the sections asked for; `config.set` takes `values` and only accepts
  `policy`, `default_model`, `roles`, `provider`. Credentials are not settable
  over the socket — they go through the CLI, where a human types them.
- **Session saves are atomic.** `sessions.save` serializes to a sibling temp file
  and `os.replace`s it. A plain `write_text` truncates first, so a SIGTERM
  mid-write replaces a conversation with an empty file — one was found on disk,
  written by a suite that kills its own server. Pinned by
  `tests/service/test_sessions.py::test_a_save_killed_midwrite_leaves_the_previous_session_intact`.
- **`AGENT_LIVE_MODEL` opts out of the sandboxed `HOME`.** A real run routes to
  the developer's model servers, whose config and Keychain keys live in the real
  `HOME`; a sandboxed one has nothing to route to, and the run writes a session
  into `~/.openagent/sessions`.
- **The event stream is the connection's, not the run's.** The Swift client used
  to `finish()` its stream at the first terminal event, which left the app with
  no events for its second run on the same connection — every run after the
  first. The stream now ends when the connection does; one-shot consumers break
  on `isTerminal`, and `Transcript` closes each run. Found by the app-model
  check, invisible to the protocol tests.
- **`AppModel` and `Sidecar` live in `AgentAppCore`, not in the executable
  target.** A target a test cannot import is a target a test cannot check. The
  views are the only part left unchecked, which is why they are thin.
- **`Sidecar` redirects the service's stderr to a file, not a `Pipe`.** A full
  pipe buffer blocks the writer: a service that blocks on its own stderr is a
  service that stops serving.
- **A SwiftPM executable's types cannot be imported.** That is why `AgentAppCore`
  is a library, and why the views moved to `AgentAppUI`: with them in the
  executable, nothing — not a check, not a preview — could put them on screen.
- **`.none` on an optional enum property is `nil`, not the enum's `.none` case.**
  `model.reasoningEffort = .none` compiles, warns, and means "model decides".
- **`hello` reports `terminal_event_types`** and a client ends a *run* on the
  union of its own set and the server's, so a terminal event added in a later
  protocol version closes an older client's timeline instead of leaving it open.

## Git

Ahead of `origin/feat/computer-use-macos` by the phase 0–3 commits (nothing
pushed), newest first:
`phase3a: Transcript — the event stream as UI state, checked headless`,
`phase2c: Swift AgentClient — protocol codec, framing, live contract checks`,
`phase2b: Unix-socket agent service, client, CLI, contract tests`,
`phase2a: wire protocol + streaming tool loop (AgentRunner)`,
`phase1+: working_dir was decorative — tools wrote into the process CWD`,
`phase1+: user-added model servers, discovery diagnostics, test isolation`,
`phase1: provider registry, model catalog, router, auth broker`,
`phase0: foundation cleanup for the Mac general-agent app`.
