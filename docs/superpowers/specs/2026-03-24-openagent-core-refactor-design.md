# OpenAgent Core Refactor Design

## Goal

Refactor the repository into a library-first architecture where the reusable `openagent`
core is clear, clean, easy to read, and easy to extend, while the SoloCoder CLI becomes
a thin product layer built on top of that core.

This refactor is allowed to be a clean break. Internal and public APIs may change if that
produces a meaningfully better architecture. The core must support both streaming and
non-streaming execution through the same runtime model.

## Why This Refactor Is Needed

The current codebase has several boundary problems that make the core harder to understand
 and reuse than it should be.

- `openagent/core/agent.py` mixes runtime orchestration with CLI-style presentation and
  tool result rendering.
- `openagent/core/session.py` is both a conversation store and a context-compaction policy
  engine that calls providers directly.
- `openagent/tools/builtin.py` mixes unrelated concerns: file tools, shell tools, web tools,
  task tools, planning tools, skills, and orchestration placeholders.
- `cli_coder.py` duplicates provider/bootstrap logic and reaches directly into agent internals.
- manager-backed behavior is partially hidden behind global singleton lookups rather than
  explicit runtime dependencies.

The result is a framework that works, but whose core logic is harder than necessary to
follow, test, and evolve.

## Design Principles

- Library-first: optimize for a reusable `openagent` core, with SoloCoder as an app layer.
- One responsibility per unit: state, orchestration, tool execution, provider I/O, and UI
  rendering must be separate.
- Explicit dependencies over hidden globals.
- Streaming is a first-class runtime capability, not an afterthought.
- Behavior should be organized around typed runtime events rather than direct terminal output.
- Preserve the strongest existing seams where possible, especially the canonical message model.
- Refactor in phases so the riskiest areas are isolated before larger moves happen.

## Intended Public API

Because this is a library-first refactor, the supported public surface must be explicit.

Target public API categories:

- stable public core
  - canonical model types
  - provider base interfaces
  - runtime `Agent` entrypoints
  - tool declaration and registration APIs
  - session state APIs
- public but product-specific app surface
  - SoloCoder app composition entrypoints such as `CoderAgent`
  - CLI entrypoints and prompt configuration where intentionally exposed
- internal-only implementation details
  - runtime event translation internals
  - compaction internals
  - provider converter internals
  - infrastructure adapters and process managers unless explicitly promoted

Planning should treat the stable public core as the main compatibility target after the clean
break. Internal package layout may evolve as long as the new public surface remains deliberate
and documented.

Concrete public contracts to define in planning and implement in the refactor:

- `Agent`
  - `run(...) -> AgentResult`
  - `stream(...) -> AsyncIterator[AgentEvent]`
- `AgentResult`
  - final assistant-facing result data for a completed run
  - references the final assistant message produced by the run
- `AgentEvent`
  - normalized runtime event union used by CLI/app integrations
- provider base interfaces
  - non-streaming `chat(...)`
  - streaming `stream(...)`
- tool declaration and registration APIs
- `Session` public mutation/read APIs

The root `openagent` package should export only deliberate public contracts. Internal
implementation helpers, provider converters, display formatting helpers, and process-management
details should not be treated as part of the supported core API unless explicitly promoted.

## Target Architecture

### Layer 1: Model

This layer contains canonical data structures only.

Responsibilities:

- `Message`
- `TextBlock`
- `ToolUseBlock`
- `ToolResultBlock`
- `ToolDef`
- small, local serialization helpers if needed

Non-responsibilities:

- provider SDK calls
- token counting
- compaction policy
- CLI display logic
- subprocess management

This layer should remain simple and stable. It is already one of the cleaner parts of the
codebase and should be preserved as the foundation of the runtime contract.

### Layer 2: Runtime

This layer is the actual agent engine.

Responsibilities:

- request lifecycle orchestration
- tool dispatch coordination
- streaming and non-streaming execution
- context window management through injected collaborators
- runtime event emission
- retry and loop control behavior

Key design rule:

The runtime must not print, know about Claude Code-style formatting, or depend on a specific
product shell.

### Layer 3: Providers

This layer adapts concrete LLM SDKs and APIs into the canonical runtime contract.

Responsibilities:

- convert canonical messages into provider-native requests
- convert provider responses into canonical messages or stream events
- expose a uniform chat/stream interface to the runtime

The provider layer should not mutate session state or know about product-specific UX.

### Layer 4: Infrastructure

This layer contains technical adapters and process-facing implementations.

Responsibilities:

- shell/session process management
- MCP integration
- filesystem guard helpers
- web backends if they are not pure utility functions
- any concrete services used by runtime or app layers

These components are replaceable implementation details. They should not define the mental
model of the framework.

### Layer 5: Product/App

This layer contains SoloCoder-specific behavior.

Responsibilities:

- `CoderAgent` composition
- CLI REPL
- Claude Code-style rendering
- prompt packs
- local-model defaults and coding-agent UX
- product-specific workflow helpers

This layer depends on the reusable runtime. The runtime must not depend on this layer.

## Proposed Module Boundaries

The exact filenames may vary, but the architecture should converge on boundaries like these.

### `openagent.model`

Contains canonical data only.

- messages and blocks
- tool definitions
- helper constructors

### `openagent.tools`

Split by responsibility.

- `tools.registry` or `tools.schema`
  - `@tool`
  - schema generation
  - registry definition
- `tools.executor`
  - tool execution against an explicit runtime context
  - sync/async dispatch
  - error wrapping
- `tools.builtin.files`
- `tools.builtin.shell`
- `tools.builtin.web`

There should be a clear distinction between reusable built-in tools and SoloCoder-specific
workflow tools.

Decision:

- reusable core built-ins stay in `openagent.tools.builtin.*`
- workflow/product-oriented tools such as planning mode, task UX, skill loading, and similar
  coding-agent affordances move to the SoloCoder app layer rather than remaining in reusable
  core

That means the first-pass core built-in split is:

- `openagent.tools.builtin.files`
- `openagent.tools.builtin.shell`
- `openagent.tools.builtin.web`

and workflow/app tools belong under the SoloCoder product package.

### `openagent.runtime`

- `agent.py`
  - top-level orchestration only
- `memory.py`
  - token counting, compaction policy, summarization orchestration
- `context.py`
  - runtime dependency container
- `events.py`
  - typed runtime events for streaming and observability

### `openagent.providers`

- base provider interface
- converters
- concrete provider adapters for OpenAI-compatible APIs, Anthropic, Google, Ollama

### `openagent.infrastructure`

- bash/session manager
- MCP client and transports
- other concrete adapters with process or system concerns

### `solocoder` or `openagent.apps.solocoder`

- `CoderAgent`
- CLI entrypoints
- terminal rendering
- prompt configuration
- product defaults

The final packaging decision can happen after the structural refactor is underway, but the
dependency direction must already follow this split.

## Extension Model

Product-layer behavior must plug into the runtime through explicit extension points instead of
reintroducing hidden coupling.

Required extension mechanism:

- the runtime owns generic tool registration and execution
- app/product layers can register additional tools, services, and event consumers during agent
  composition
- app-only services are exposed through explicit dependency injection during agent/app
  composition, not through global lookups
- reusable core runtime code must depend only on core interfaces, never on SoloCoder-specific
  services

This is the sanctioned way for SoloCoder workflow tools and app behaviors to plug into the
runtime after they move out of reusable core.

Chosen injection model:

- core runtime receives only core dependencies through a typed runtime context
- app/product compositions may wrap or extend that context when registering app-only tools
- app-only tools receive their app services from composition-time binding or explicit wrappers,
  not from hidden singleton lookup and not from implicit ambient state

## Runtime Object Design

### Agent

`Agent` should become a thin coordinator.

Responsibilities:

- accept user input
- append it to session state
- invoke provider chat/stream behavior
- pass tool calls to the tool executor
- continue until the run completes

Non-responsibilities:

- printing or terminal formatting
- direct shell management knowledge
- embedded compaction logic
- constructor-time async side effects

### Session

`Session` should become a pure conversation state container.

Responsibilities:

- append messages
- expose messages and recent slices
- save/load history

Non-responsibilities:

- token counting
- compaction decisions
- provider calls for summarization
- policy-driven mutation of its own contents

### Memory Manager

Move context-window policy out of `Session` into a dedicated service.

Responsibilities:

- estimate token usage
- decide when compaction is needed
- generate summaries or reduced history views
- request session updates through explicit APIs

This isolates the current `compact_context`, `check_compaction_needed`, and token-counting
behavior into a swappable policy unit.

Authoritative state rule:

- `Session` remains the authoritative conversation store
- `MemoryManager` never mutates private session internals directly
- if compaction is chosen, `MemoryManager` returns an explicit compaction result or rewrite plan
  and the runtime applies it through `Session`'s public APIs

Required session APIs for compaction:

- append/add message
- append tool-result message
- read current ordered message list
- replace history with a validated compacted history artifact
- persist/load compacted histories without special-case behavior in callers

Persisted compaction artifact shape:

- compacted history must remain ordinary session history, not a side channel
- the compaction result is represented as an explicit summary/system message followed by the kept
  recent messages
- persisted session data must therefore remain replayable and understandable without hidden
  metadata requirements

Canonical model requirement:

- the canonical message model must continue to support `system` role messages as first-class
  persisted history entries
- compaction does not require a new message role in the first pass

Persistence and replay rule:

- if a session is compacted, the compacted state becomes the persisted conversational state for
  continued runtime operation
- compaction must be represented explicitly in session history as a summary/system artifact so
  later replay is understandable
- if full-fidelity transcript preservation is desired later, that should be a separate archival
  concern, not hidden inside `Session`

Streaming interaction rule:

- compaction must not occur in the middle of an active streamed assistant message
- compaction can occur only at stable boundaries between turns or between completed tool/result
  cycles
- compaction events must be emitted so the app layer can render or log the transition

Summarization dependency contract:

- `MemoryManager` depends on a narrow summarizer interface rather than calling the runtime loop
  directly
- the summarizer interface is implemented by a provider-backed summarization adapter in the
  runtime/infrastructure boundary
- this keeps compaction policy separate from provider SDK details while avoiding recursive use of
  the main agent loop

### Tool Registry

Keep this as a pure capability catalog.

Responsibilities:

- register tools
- expose tool definitions/schemas
- look up tools by name

It should not be responsible for hidden runtime service lookup.

### Tool Executor

Introduce a dedicated execution component.

Responsibilities:

- execute tool calls against a registry
- inject runtime context/services explicitly
- handle sync and async tools uniformly
- return canonical tool results
- support parallel execution where appropriate

This is the right place to replace hidden singleton behavior with explicit execution context.

### Runtime Context

Introduce a typed runtime context object.

Possible contents:

- working directory
- project root
- bash service
- logger or event sink

This becomes the single source of truth for execution-scoped dependencies and replaces the
current partial reliance on `get_bash_manager()`, `get_task_manager()`, and
`get_skill_manager()`.

Boundary rule:

- core runtime context may contain only reusable execution dependencies needed by the library
  runtime itself or by reusable core tools
- product/workflow services such as task-tracking UX, skill systems, and slash-command helpers
  belong in the SoloCoder app layer or in app-specific extension contexts
- if a capability cannot be justified outside the coding-agent product, it should not be a core
  runtime dependency

This means task and skill services should not be treated as core runtime dependencies by
default. They are product-layer extensions unless a narrower reusable contract is defined later.

### Event Sink and Runtime Events

The runtime should emit structured events instead of printing directly.

The CLI can render these in Claude Code-style formatting. Tests can assert on them without
needing terminal output. Library users can ignore them or plug in their own observers.

Required event contract:

- every event must have a stable `type`
- every event must carry a `run_id`
- message-related events must carry a stable `message_id`
- tool-related events must carry the provider/tool `tool_call_id`
- ordering must be stable within a run: emitted events form the authoritative timeline for a
  single execution
- `run_completed` is the only event that closes a run successfully
- `run_failed` should exist for fatal runtime failures
- `run_cancelled` should exist for explicit cancellation paths

Normalized event taxonomy for the first pass:

- `run_started`
- `message_started`
- `message_delta`
- `message_completed`
- `message_failed`
- `tool_call_started`
- `tool_call_completed`
- `tool_call_failed`
- `context_compaction_started`
- `context_compaction_completed`
- `context_compaction_failed`
- `run_completed`
- `run_failed`
- `run_cancelled`

Required payload expectations for run lifecycle events:

- `run_started`
  - `run_id`
  - user request metadata or invocation metadata
- `run_completed`
  - `run_id`
  - final result metadata
  - final assistant message identity when applicable
- `run_failed`
  - `run_id`
  - normalized error details
- `run_cancelled`
  - `run_id`
  - cancellation reason/source when available

Required payload expectations for message lifecycle events:

- `message_started`
  - `run_id`
  - `message_id`
  - speaker role
- `message_failed`
  - `run_id`
  - `message_id`
  - normalized error details

Required payload expectations:

- `message_delta`
  - incremental assistant text
  - `run_id`
  - `message_id`
  - delta text payload
- `message_completed`
  - final assembled assistant message content for that message
  - `run_id`
  - `message_id`
- `tool_call_started`
  - `run_id`
  - `message_id`
  - `tool_call_id`
  - tool name
  - normalized arguments
- `tool_call_completed`
  - `run_id`
  - `message_id`
  - `tool_call_id`
  - canonical tool result
- `tool_call_failed`
  - same identity fields plus normalized error details
- compaction events
  - `run_id`
  - compaction reason
  - before/after token counts when available

`AgentResult` in non-streaming mode should be derived from the same internal event stream. In
other words, `run()` is a convenience wrapper over the same execution model used by `stream()`.
This guarantees one authoritative runtime contract instead of separate streaming and
non-streaming architectures.

Normative event lifecycle:

- run lifecycle
  - `run_started`
  - zero or more message/tool/compaction events
  - exactly one terminal event: `run_completed`, `run_failed`, or `run_cancelled`
- assistant message lifecycle
  - `message_started`
  - zero or more `message_delta`
  - optional tool call events if the provider response requests tools
  - exactly one terminal event for that message: `message_completed` or `message_failed`
- tool lifecycle
  - `tool_call_started`
  - exactly one terminal event: `tool_call_completed` or `tool_call_failed`
- compaction lifecycle
  - `context_compaction_started`
  - exactly one terminal event: `context_compaction_completed` or
    `context_compaction_failed`

This lifecycle is normative for planning and implementation. Event families are not merely
illustrative.

Message continuity rule for tool-interleaved runs:

- if a provider emits assistant text and then requests tools, that assistant output is treated as
  one completed assistant message
- any assistant output produced after tool execution resumes as a new assistant message with a
  new `message_id`
- the runtime must not treat pre-tool and post-tool output as a single message in the first pass

This avoids ambiguity in streaming, persistence, CLI rendering, and event correlation.

## Streaming Design

Streaming must be a first-class runtime feature.

### Unified Runtime Contract

The runtime should expose both:

- `run(...) -> AgentResult` for non-streaming usage
- `stream(...) -> AsyncIterator[AgentEvent]` for streaming usage

These must use the same underlying execution model rather than separate logic paths.

### Provider Contract

Providers should support:

- full-response chat
- native streaming when available
- fallback streaming that emits a single final chunk when native streaming is unavailable

This keeps the runtime contract uniform even across providers with different SDK capabilities.

Provider-facing runtime contract:

- providers return canonical assistant outputs to the runtime, not app-level rendering events
- non-streaming path
  - `chat(...) -> Message`
- streaming path
  - `stream(...) -> AsyncIterator[ProviderStreamEvent]`

`ProviderStreamEvent` is a provider-to-runtime internal contract, not the app-facing
`AgentEvent` contract. It should cover only provider output semantics such as:

- `provider_message_started`
- `provider_text_delta`
- `provider_message_completed`
- `provider_tool_call`
- `provider_error`

Required provider stream rules:

- ordering is authoritative within a single provider response stream
- exactly one terminal provider event closes a provider response: `provider_message_completed` or
  `provider_error`
- tool call discovery events must carry enough data for the runtime to build canonical
  `ToolUseBlock`s without provider-specific logic leaking upward
- providers must not emit app-level concepts such as CLI rendering events

The runtime consumes `ProviderStreamEvent`s and translates them into public `AgentEvent`s.
This keeps provider adapters focused on model I/O and keeps app-facing event semantics owned by
the runtime.

### Tool Calls During Streaming

Tool-interleaved runs should remain observable in real time.

Required behavior:

- partial assistant text can stream before a tool call
- runtime emits tool-start and tool-finish events
- assistant output can resume after tool execution
- final completion is represented as an event, not implicit print behavior

This makes streaming suitable for CLI rendering and future UI integrations.

Parallel tool execution rule:

- the runtime may execute multiple tool calls in parallel only after all calls for a given
  assistant message are fully known
- emitted events remain ordered as a single authoritative timeline by emission time
- each tool event must carry `message_id` and `tool_call_id` so concurrent activity can be
  correlated without ambiguity
- assistant message progression does not resume until the tool-result set for that message is
  complete

This preserves a stable run timeline even when tool execution is concurrent.

Cancellation semantics:

- cancellation is initiated by the runtime or app layer through an explicit cancellation signal
  on the active run
- the runtime propagates cancellation to provider streaming, in-flight tool execution, and any
  active compaction work where possible
- runtime emits `run_cancelled` only after cancellation propagation reaches a stable boundary
- best-effort cleanup is required for background infrastructure such as shell sessions or MCP
  calls that cannot stop instantaneously

### Streaming and Testing

Streaming needs dedicated tests for:

- pure text streaming
- fallback streaming behavior
- tool-call interleaving
- provider differences behind a common runtime contract

Without this, streaming will become a second hidden architecture and drift from the main run
loop.

## Dependency Rules

Dependencies must flow inward.

- app/product -> runtime -> model and provider/tool interfaces
- infrastructure implements services consumed by runtime or app
- runtime does not import CLI rendering, REPL code, or product prompt assets

This rule is the main architectural constraint that prevents boundary drift from returning.

## Compatibility Strategy

This refactor is explicitly allowed to be a clean break.

Allowed changes:

- import path changes
- public API cleanup for `Agent`, `CoderAgent`, and provider construction
- CLI behavior cleanup where current behavior is tightly coupled or awkward

Important behavior to preserve at the product level:

- local-model friendly operation
- coding-agent workflow
- tool-driven execution
- context compaction
- background shell support
- streaming output

## What Not To Refactor In Pass 1

To keep the first major pass focused, avoid these distractions unless they are required by
the new boundaries.

- Do not redesign the canonical message model unless necessary.
- Do not expand product scope during the refactor.
- Do not attempt equal-depth provider cleanup from the start; first stabilize the shared
  provider contract and the best-covered adapters.
- Do not let branding/package renaming dominate the structural work.

## Migration Strategy

### Phase 1: Establish New Seams and Streaming Contract

Goals:

- extract runtime events
- extract a memory/context manager from `Session`
- extract a dedicated tool executor from `Agent`
- define the unified `run()`/`stream()` execution contract and event model
- preserve behavior through temporary adapters where needed

Why first:

This creates stable boundaries before larger file and package moves happen, and it ensures the
streaming contract is foundational rather than retrofitted.

### Phase 2: Split Built-In Tools and Remove Global Coupling

Goals:

- split `openagent/tools/builtin.py` by domain
- introduce explicit runtime context
- stop tool execution paths from depending on `get_*_manager()` globals

Why second:

It reduces one of the largest readability and reuse problems without requiring immediate app
layer redesign.

### Phase 3: Move Product Concerns Out of Core

Goals:

- move CLI rendering out of runtime
- centralize provider/model creation logic
- make `CoderAgent` a composition layer over reusable runtime pieces

Why third:

Once runtime seams exist, product logic can move outward cleanly.

### Phase 4: Expand Native Streaming Coverage

Goals:

- update providers to conform to the shared event-driven model
- update the CLI to render from runtime events rather than runtime print calls

Why fourth:

By this phase, the runtime contract already supports streaming. This phase focuses on broad
provider adoption, richer UX, and removal of temporary compatibility bridges.

### Phase 5: Package Identity Cleanup

Goals:

- decide final app packaging (`solocoder` vs `openagent.apps.solocoder`)
- align docs, defaults, and exports with the new structure

Why last:

The correct naming and packaging become much easier to decide once the boundaries are real.

## Testing Strategy

Refactoring should reorganize the test suite around the new seams.

Recommended test groups:

- model tests
- runtime orchestration tests
- memory/compaction policy tests
- tool registry and tool executor tests
- provider contract tests
- infrastructure adapter tests where feasible
- app/CLI integration tests for SoloCoder behavior
- streaming event tests across runtime and providers

Special attention is needed for currently risky or weakly covered areas:

- manager-backed tools
- bash/session infrastructure
- CLI/bootstrap behavior
- Anthropic/Google providers
- MCP integration

## Expected Outcome

After the refactor:

- the reusable core runtime is understandable without reading CLI code
- streaming and non-streaming share one execution model
- session state, memory policy, tool execution, provider I/O, and UI rendering are clearly
  separated
- adding providers, tools, or alternate UIs becomes straightforward
- the SoloCoder product story sits cleanly on top of the library rather than inside it

## Open Questions To Resolve During Planning

- final package layout for the SoloCoder app layer
- how much temporary compatibility shimming is worth carrying during migration
- how to stage bash/session infrastructure cleanup relative to streaming rollout

These are implementation-planning questions, not blockers to the design itself.
