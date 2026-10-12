# Handoff — resuming work on the Mac general-agent app

Read this first after a restart. Design rationale lives in
[`mac-agent-app-architecture.md`](mac-agent-app-architecture.md); this file is
*state*: what exists, what is verified, what is outstanding, what to do next.

_Last updated: Phase 1 + post-gate fixes committed, 463 tests green._

---

## Resume in 30 seconds

```bash
cd /Users/taozeng/Projects/SoloCoder
git status --short && git log --oneline -4     # clean tree expected
.venv/bin/python -m pytest tests -q            # 463 passed, zero warnings
.venv/bin/python cli_coder.py auth status      # credentials + model servers
.venv/bin/python cli_coder.py models list --discover
.venv/bin/python cli_coder.py models probe lan/swift-1.5-iq3_s
```

Branch: `feat/computer-use-macos`. Venv: `.venv/bin/python` (3.14). No untracked
work in progress — every phase is committed.

---

## Where we are

| Commit | What |
| --- | --- |
| `2433af5` | **Phase 0** — foundation cleanup: deduped the two `Agent` classes, shared run-loop policy (`runtime/loop.py`), secrets → macOS Keychain, Py3.16 deprecations, Pydantic `ConfigDict`, metadata rot. 320 tests. |
| `dd2477c` | **Phase 1** — provider registry, `ModelRef`, model catalog + capability probe, router, `AuthBroker` (API key + OAuth PKCE loopback for ChatGPT/Claude), `openai-responses` adapter, `auth`/`models` CLI. 428 tests. |
| `85a2cc8` | **Phase 1+** — user-added model servers (`models add-endpoint`), discovery sends the key and explains silence, config isolation for tests. 451 tests. |
| `852f920` | **Phase 1+** — `working_dir` was decorative; tools wrote into the process CWD. Added `ToolContext`. 463 tests. |

### Gates that actually ran (not mocked)

- **Synthetic OpenAI-compatible server** on LM Studio's port 1234: discovery,
  probe (233 ms), text turn, tool-call turn, clean `close()`.
- **Real LAN model server** `http://192.168.4.34:8080/v1` (the user's box, key
  `auth:lan` in the Keychain, registered as custom endpoint `lan`, marked
  `--local`): discovery listed `lan/swift-1.5-iq3_s`, probe proved tool calling
  (1.3–1.7 s), and a full `CoderAgent` run with real tools created and read
  files **inside the session directory**. This gate is what found the
  `working_dir` bug.

### Gates still outstanding

1. **Claude Max OAuth sign-in** — needs an interactive browser:
   `.venv/bin/python cli_coder.py auth login anthropic_oauth`, then verify
   token storage, refresh, and a real Claude Max turn.
2. **LM Studio's own server** — port 1234 was closed all session (only the
   user's Codex/LM Studio Responses proxy listens on 1235). Re-run the gate
   above once the LM Studio API server is enabled.
3. **Default model is unset** (`models.default_model == ""`). The router picks
   `lan/swift-1.5-iq3_s` under `local_first`; persisting a default is a one-liner
   (`models use lan/swift-1.5-iq3_s`) and was deliberately left to the user.

---

## What exists, by file

**Engine (Python, ~9.2k LOC in `openagent/`)** — unchanged in intent, reusable
by the Swift app over a socket.

- `openagent/provider/model_ref.py` — `ModelRef` (the single model address:
  provider/model, `AuthMode`, `WireAPI`, `Capabilities`, `CostHint`, base_url),
  `AuthMode`, `WireAPI`, `Capabilities`, `CostHint`.
- `openagent/provider/registry.py` — endpoint table (LM Studio, Ollama, MLX,
  llama.cpp, vLLM, Anthropic-compatible local, Anthropic, Anthropic OAuth,
  OpenAI, OpenAI OAuth, Gemini) + user `custom_endpoints`; `build()/_build()`
  split translating missing SDKs into `ProviderDependencyError`;
  `spec_from_config`, `add_endpoint_config`, `remove_endpoint_config`,
  `BUILTIN_PROVIDERS`.
- `openagent/provider/catalog.py` — built-in defaults, live discovery (sends
  stored keys), capability probe + cache, `discovery_errors` diagnostics.
- `openagent/provider/router.py` — `RoutingPolicy` (`fixed`, `local_first`,
  `cloud_first`, `cheapest_capable`, `privacy_lock`), role overrides, probed
  facts outrank declared claims.
- `openagent/provider/auth/` — `tokens.py` (`TokenSet`, `Credentials`,
  `AuthStatus`, `AuthRequired`), `spec.py` (Anthropic + OpenAI specs, env
  overrides), `pkce.py`, `flow.py` (authorize URL, token exchange, refresh,
  JWT claims, loopback listener), `broker.py` (`AuthBroker`: Keychain-backed,
  refresh-if-needed, status).
- `openagent/provider/responses.py` — `OpenAIResponsesProvider` for the ChatGPT
  subscription backend `https://chatgpt.com/backend-api/codex/responses`.
- `openagent/provider/cli.py` — `auth status|login|logout`, `models
  list|probe|use|add-endpoint|remove-endpoint`; async handlers
  (`handle_auth_async`, `handle_models_async`) + sync wrappers.
- `openagent/core/tool.py` — `ToolContext` (session scope injected into tools),
  `ToolRegistry`, schema builder that hides injected params.
- `openagent/core/agent.py` — canonical tool-capable `Agent`; owns
  `working_dir`, passes `tool_context` at the execute site.
- `openagent/runtime/loop.py` — shared run-loop policy (`LoopSettings`,
  `EmptyResponseGuard`, compaction hook, id generator). `RuntimeAgent` is the
  canonical runtime class; `openagent.runtime.Agent` is a compat alias.
- `openagent/core/config.py` — `ModelConfig` incl. `policy`, `default_model`,
  `roles`, `custom_endpoints`; `reload_config()`.
- `openagent/core/memory.py` `SecretManager` — macOS Keychain via
  `/usr/bin/security`, `0600` JSON fallback off-macOS, legacy plaintext
  migration.

**Tests** — `tests/providers/` (registry, catalog, router, auth broker, OAuth
flow, responses, provider CLI, close, custom endpoints),
`tests/runtime/test_loop.py`, `tests/test_config_secrets.py`,
`tests/test_public_api.py`, `tests/test_working_dir_scope.py`.
`tests/conftest.py` sandboxes the config file; provider tests guard the real
Keychain.

---

## Machine facts (verified this session)

- Config: `~/.openagent/config.json` — `search` (tavily) + `models`
  (`policy: local_first`, `default_model: ""`, custom endpoint `lan`).
- Keychain service `openagent`, accounts: `tavily_api_key`, `auth:lan`.
  Deliberately absent: `auth:anthropic`, `auth:openai`, `auth:anthropic_oauth`,
  `auth:openai_oauth` (no subscription sign-in yet).
- Ports: `1234` LM Studio **closed**, `1235` **LISTEN** (user's
  `~/.codex/lmstudio-proxy/proxy.py`), `11434`/`8080`/`8000` closed.
  LAN server: `192.168.4.34:8080` (OpenAI-compat, requires key).
- Optional extras not installed: `ollama`, `google-genai`
  (`pip install openagent[ollama]` / `[google]`).
- Repo: 518 MB after pruning stale `.claude/worktrees/*`.

---

## Traps already paid for — do not re-discover

1. `openagent.providers.ProviderError` is a **stream event dataclass**, not an
   exception. Dependency failures use `ProviderDependencyError`.
2. `store or SecretManager()` let a falsy test store fall through to the **real
   macOS Keychain**. Falsy is still a store: `SecretManager() if store is None else store`.
3. Keychain account names are a namespace: `auth:anthropic` (API key) vs
   `auth:anthropic_oauth` (subscription). `AuthBroker` must not inherit the
   legacy plaintext→Keychain migration for `auth:*`.
4. ChatGPT subscription JWTs are **rejected by `api.openai.com`**; the
   subscription path is the Codex **Responses** backend.
5. Anthropic OAuth is not "an API key with another base URL": needs
   `Authorization: Bearer`, `anthropic-beta: oauth-2025-04-20`, and
   `anthropic-account-id` decoded from the JWT.
6. The OAuth loopback listener must bind **IPv4 and IPv6** loopback (browsers
   redirect to `127.0.0.1`) and must refuse non-loopback hosts — it receives
   the access token.
7. OpenAI-compat base URLs already end in `/v1`; discovery paths are relative,
   else `/v1/v1/models`.
8. `cli_coder.main()` is async: CLI handlers must be awaitable, never
   `asyncio.run()` inside it.
9. `BaseProvider.close()` is part of the contract (long-lived app builds
   providers per turn).
10. `security add-generic-password -w` reads the password **twice** from stdin,
    and `-w` must be the last argument.
11. `ToolContext` is injected by the harness and **must never appear in the
    model-facing schema** — a model that can set it escapes the session scope.
12. Tests must not read the user's real config (custom endpoints live there) and
    must never write the real Keychain.

---

## Next: Phase 2 — agent service

Contract first, then code. The Swift app is a client of this socket; the event
schema is the stable boundary that lets a pure-Swift engine replace the Python
one later with zero UI change.

1. `openagent/service/protocol.py` — JSON-RPC envelope + event names
   (`run.start`, `run.started`, `message.started/delta/completed`,
   `tool_call.started/completed`, `run.completed/failed/cancel`,
   `permission.request/respond`, `context.compaction.started`,
   `model.list/probe`, `auth.status/login`, `session.list/load/save`,
   `config.get/set`).
2. Unix-socket server, NDJSON framing, one run per connection, cancellation,
   permission round-trip channel.
3. **`run.start` carries the session scope** (`working_dir`) → engine maps it to
   `ToolContext`. The app picks the project; the model only ever writes paths.
4. `openagent serve` + headless `openagent tui` client driving a full run.
5. `macapp/AgentClient/` SwiftPM package: `AsyncThrowingStream<AgentEvent>`,
   Codable event types, **shared contract tests** run against the Python socket
   (same fixture files on both sides).
6. Define the Swift `AgentEvent` Codable schema in this phase, not Phase 3.

**Gate:** headless TUI completes a streamed run through the socket (stream,
cancel, permission round-trip, reconnect) and the Swift harness passes the same
tests.

Then Phase 3 (SwiftUI shell + sidecar lifecycle + TCC), Phase 3.5 (native UI
parity → delete the React frontend), Phase 4 (Accessibility computer-use,
Calendar/Reminders/Notes/Mail, permission broker).

---

## Decisions not to relitigate

SwiftUI/AppKit shell + Python sidecar over a Unix socket; **no WebView, no
Electron, no Node in the shipped app**; Python stays the engine for v1
(~10.4k LOC, and `mcp`/`openai`/`anthropic`/MLX are load-bearing); `openagent`
stays the package name, SoloCoder is one app under `openagent/apps/solocoder/`;
API keys are the officially supported cloud path, subscription OAuth is
supported but labelled unofficial/at-your-own-risk; local servers are
*optimistic until probed* (declared = claim, probed = fact); `ModelRef` is the
only model address the app sees. Full reasoning:
[`mac-agent-app-architecture.md`](mac-agent-app-architecture.md) §2.1, §3.2,
§3.3, §6 (roadmap + Phase 1 deviations), §8 (risks).
