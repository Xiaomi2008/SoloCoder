# HANDOFF — resumable state after restart

## Where we are

**Phase 3 in progress: the app runs.** The Python service, the Swift client, the
event-stream state machine and a SwiftUI shell (chat, tool timeline, permission
sheet, model picker, session browser) are done and green, and the app spawns its
own service and drives real runs. What is left in Phase 3 is packaging: a frozen
sidecar binary inside a `.app`.

- Branch: `feat/computer-use-macos`
- Python suite: **569 passed** (`.venv/bin/python -m pytest tests -q`)
- Swift suite: **165 checks passed** (`swift run agent-checks`), **180** with
  `AGENT_REPO_ROOT` + `AGENT_LIVE_MODEL` (a real streamed run, and the app model
  doing two runs on one connection with a real model)
- Working tree: clean

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
swift run agent-checks                              # 165 checks passed
AGENT_REPO_ROOT=.. swift run agent-checks           # + the app-model checks
AGENT_REPO_ROOT=.. AGENT_LIVE_MODEL=lan/swift-1.5-iq3_s \
    swift run agent-checks                          # 180, real model
swift run solocoder                                 # the app
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

## Next: Phase 3c — the views, once they have a screen

The views are the only code no automated check reaches. Worth doing with the
window open: markdown rendering for assistant text, tool output beyond the
one-line preview, the permission sheet's argument rendering, scrolling behaviour,
and what a long session does to a `LazyVStack`.

## Things to know (verified the hard way)

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
