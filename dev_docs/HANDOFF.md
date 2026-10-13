# HANDOFF — resumable state after restart

## Where we are

**Phase 2 complete on the Python side.** The agent service, its wire protocol,
the client, the CLI entry points, and the contract tests are done and green;
the next piece is the Swift `AgentClient` package.

- Branch: `feat/computer-use-macos`
- Test suite: **551 passed** (`.venv/bin/python -m pytest tests -q`)
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
| `tests/service/` | 33 contract tests over a **real socket** + fixtures for the Swift package. |
| `scripts/smoke_service.py` | End-to-end smoke over a real socket, sandboxed HOME. |
| `scripts/gen_protocol_fixtures.py` | Regenerates `tests/service/fixtures/{protocol,events}.json` from the Python types. |

### Gates passed

- `pytest tests -q` → 551 passed.
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
.venv/bin/python -m pytest tests -q                 # 551 passed
.venv/bin/python scripts/smoke_service.py           # SMOKE OK
.venv/bin/python cli_coder.py serve --socket /tmp/oa/e2e.sock
.venv/bin/python cli_coder.py tui --socket /tmp/oa/e2e.sock \
    -p "…" -w /tmp/oa/demo -m lan/swift-1.5-iq3_s --permission-mode ask
.venv/bin/python cli_coder.py models list --discover
.venv/bin/python scripts/gen_protocol_fixtures.py   # after any protocol/event change
```

## Next: Phase 2b — the Swift `AgentClient`

1. `macapp/AgentClient/` as a SwiftPM package (no Xcode project yet).
2. `AgentEvent` as a Swift enum mirroring `protocol.py`'s event registry 1:1;
   decode the fixtures in `tests/service/fixtures/events.json` in a Swift test —
   the fixtures are generated from the Python types, so a drift fails a test.
3. NDJSON framing over `NWConnection`/`FileHandle` on the Unix socket, JSON-RPC
   envelope, `convertFromSnakeCase` (wire fields are snake_case by design).
4. Shared contract tests: the Swift suite spawns `python -m openagent.service.cli serve`
   on a temp socket and drives the same scenarios as `tests/service/test_server.py`.

## Next: Phase 3 — app shell

Chat view over the event stream, tool timeline, permission sheet (the
`permission_requested` → `permission.respond` round-trip), model picker
(`model.list` / `model.select`), session browser (`session.list` / `session.load`).

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

## Git

Ahead of `origin/feat/computer-use-macos` by the phase 0–2 commits (nothing
pushed), newest first:
`phase2b: Unix-socket agent service, client, CLI, contract tests`,
`phase2a: wire protocol + streaming tool loop (AgentRunner)`,
`phase1+: working_dir was decorative — tools wrote into the process CWD`,
`phase1+: user-added model servers, discovery diagnostics, test isolation`,
`phase1: provider registry, model catalog, router, auth broker`,
`phase0: foundation cleanup for the Mac general-agent app`.
