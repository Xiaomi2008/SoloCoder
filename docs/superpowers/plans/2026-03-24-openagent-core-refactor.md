# OpenAgent Core Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `openagent` into a library-first runtime with explicit boundaries, first-class streaming, and a thinner SoloCoder app layer.

**Architecture:** The refactor keeps the canonical message model, extracts runtime seams around events, memory, and tool execution, moves product concerns out of core, and introduces a unified streaming/non-streaming contract. Work proceeds in behavior-preserving slices where possible, while allowing public API cleanup once the new boundaries are in place.

**Tech Stack:** Python 3.11+, pytest, pytest-asyncio, OpenAI/Anthropic/Google/Ollama provider adapters, uv

---

## Planned File Structure

### Create

- `openagent/runtime/__init__.py` - runtime public exports
- `openagent/runtime/agent.py` - event-driven runtime orchestrator
- `openagent/runtime/events.py` - `AgentEvent` and `AgentResult` runtime types
- `openagent/runtime/context.py` - typed runtime context and dependency containers
- `openagent/runtime/tool_executor.py` - explicit tool execution service
- `openagent/runtime/memory.py` - compaction policy, token counting orchestration, summarizer interface
- `openagent/providers/__init__.py` - new provider package exports
- `openagent/providers/base.py` - normalized provider interfaces
- `openagent/providers/events.py` - provider-internal streaming event types
- `openagent/providers/converter.py` - provider conversion abstractions if retained
- `openagent/providers/openai.py` - adapted OpenAI provider using new contracts
- `openagent/providers/anthropic.py` - adapted Anthropic provider using new contracts
- `openagent/providers/google.py` - adapted Google provider using new contracts
- `openagent/providers/ollama.py` - adapted Ollama provider using new contracts
- `openagent/model/__init__.py` - model exports, re-exporting canonical types during migration
- `openagent/tools/registry.py` - extracted tool registry/decorator logic
- `openagent/tools/executor.py` - compatibility export or runtime-facing wrapper if needed
- `openagent/tools/builtin/__init__.py` - built-in tool package exports replacing the monolithic module
- `openagent/tools/builtin/files.py` - file tools
- `openagent/tools/builtin/shell.py` - shell tools
- `openagent/tools/builtin/web.py` - web tools
- `openagent/infrastructure/__init__.py` - infrastructure exports
- `openagent/infrastructure/bash_manager.py` - shell/session infrastructure moved from core
- `openagent/infrastructure/fs_guard.py` - reusable filesystem guard helpers extracted from tools
- `openagent/infrastructure/web.py` - optional web/backend helpers if needed
- `openagent/infrastructure/mcp.py` - MCP integration aligned with the new boundaries
- `openagent/apps/solocoder/__init__.py` - SoloCoder app exports
- `openagent/apps/solocoder/agent.py` - `CoderAgent` product composition
- `openagent/apps/solocoder/display.py` - CLI rendering from runtime events
- `openagent/apps/solocoder/cli.py` - interactive CLI entrypoint logic
- `tests/runtime/test_events.py` - runtime event contract tests
- `tests/runtime/test_agent_runtime.py` - runtime orchestration tests
- `tests/runtime/test_tool_executor.py` - tool executor tests
- `tests/runtime/test_memory.py` - memory/compaction tests
- `tests/providers/test_provider_stream_contract.py` - shared provider stream tests
- `tests/providers/test_anthropic_provider.py` - Anthropic contract tests
- `tests/providers/test_google_provider.py` - Google contract tests
- `tests/infrastructure/test_bash_manager.py` - infrastructure shell manager tests
- `tests/infrastructure/test_fs_guard.py` - filesystem guard tests
- `tests/infrastructure/test_mcp.py` - MCP boundary tests
- `tests/apps/test_solocoder_cli.py` - SoloCoder app integration tests

### Modify

- `openagent/core/agent.py` - reduce to compatibility shim or redirect to runtime implementation
- `openagent/core/session.py` - narrow to authoritative session state and persistence APIs
- `openagent/core/types.py` - keep canonical model stable; add only minimum support for public exports if needed
- `openagent/core/utils.py` - move token counting responsibilities behind runtime memory service or add fallback for missing tokenizer
- `openagent/core/display.py` - migrate or deprecate in favor of app display layer
- `openagent/coder.py` - migrate to app-layer composition or compatibility wrapper
- `openagent/__init__.py` - define deliberate public exports only
- `openagent/tools/__init__.py` - re-export reorganized tool modules
- `openagent/tools/builtin.py` - replace by moving its contents into the new `openagent/tools/builtin/` package
- `openagent/mcp.py` - align with runtime/infrastructure boundaries if needed
- `cli_coder.py` - convert to thin wrapper around app CLI module
- `tests/test_agent.py` - adapt to runtime-driven public `Agent`
- `tests/test_tool.py` - point to extracted tool registry module if needed
- `tests/test_session.py` - adapt to narrowed session responsibilities and memory tests split
- `tests/test_ollama_provider.py` - align with new provider package/contract
- `tests/test_openai_provider.py` - align with new provider package/contract

## Task 0: Establish the Model Layer Public Contract

**Files:**
- Create: `openagent/model/__init__.py`
- Modify: `openagent/core/types.py`
- Modify: `openagent/__init__.py`
- Test: `tests/test_types.py`

- [ ] **Step 1: Write the failing model-layer export test**

```python
def test_model_package_exports_canonical_types():
    from openagent.model import Message, ToolUseBlock
    assert Message is not None
    assert ToolUseBlock is not None
```

- [ ] **Step 2: Run the model tests to verify they fail**

Run: `uv run pytest tests/test_types.py -v`
Expected: FAIL because `openagent.model` does not exist yet.

- [ ] **Step 3: Add the `openagent.model` package as the canonical model export surface**

```python
# openagent/model/__init__.py
from openagent.core.types import Message, TextBlock, ToolUseBlock, ToolResultBlock, ToolDef
```

- [ ] **Step 4: Run the model tests to verify they pass**

Run: `uv run pytest tests/test_types.py -v`
Expected: PASS.

## Task 1: Establish Full Runtime Event and Result Contracts

**Files:**
- Create: `openagent/runtime/events.py`
- Create: `openagent/runtime/__init__.py`
- Modify: `openagent/__init__.py`
- Test: `tests/runtime/test_events.py`

- [ ] **Step 1: Write the failing runtime event contract tests**

```python
from openagent.runtime.events import (
    RunStarted,
    RunCompleted,
    RunFailed,
    RunCancelled,
    MessageStarted,
    MessageDelta,
    MessageCompleted,
    ToolCallStarted,
    ToolCallCompleted,
    ContextCompactionStarted,
    ContextCompactionCompleted,
)


def test_event_types_expose_required_identity_fields():
    event = RunStarted(run_id="run-1")
    assert event.type == "run_started"
    assert event.run_id == "run-1"


def test_message_delta_has_message_identity_and_text_delta():
    event = MessageDelta(run_id="run-1", message_id="msg-1", delta="hi")
    assert event.message_id == "msg-1"
    assert event.delta == "hi"


def test_agent_result_references_final_message():
    result = RunCompleted(run_id="run-1", final_message_id="msg-2")
    assert result.final_message_id == "msg-2"


def test_terminal_events_cover_failure_and_cancellation_identity():
    assert RunFailed(run_id="run-1", error="boom").run_id == "run-1"
    assert RunCancelled(run_id="run-1", reason="user").reason == "user"


def test_tool_and_compaction_events_expose_required_ids():
    tool_event = ToolCallStarted(
        run_id="run-1", message_id="msg-1", tool_call_id="tool-1", tool_name="read", arguments={}
    )
    compact_event = ContextCompactionStarted(run_id="run-1", reason="threshold")
    assert tool_event.tool_call_id == "tool-1"
    assert compact_event.reason == "threshold"


def test_failure_events_exist_for_message_tool_and_compaction():
    assert MessageFailed(run_id="run-1", message_id="msg-1", error="boom").type == "message_failed"
    assert ToolCallFailed(run_id="run-1", message_id="msg-1", tool_call_id="tool-1", error="boom").type == "tool_call_failed"
    assert ContextCompactionFailed(run_id="run-1", reason="threshold", error="boom").type == "context_compaction_failed"


def test_run_has_terminal_event_identity_fields():
    event = RunCompleted(run_id="run-1", final_message_id="msg-9")
    assert event.run_id == "run-1"
    assert event.final_message_id == "msg-9"


def test_agent_result_exposes_final_message_identity_and_output_text():
    result = AgentResult(run_id="run-1", final_message_id="msg-9", output_text="done")
    assert result.output_text == "done"
```

- [ ] **Step 2: Run event tests to verify they fail**

Run: `uv run pytest tests/runtime/test_events.py -v`
Expected: FAIL because `openagent.runtime.events` and the event classes do not exist yet.

- [ ] **Step 3: Implement the minimal runtime event and result types**

```python
@dataclass
class RunStarted:
    run_id: str
    type: str = "run_started"


@dataclass
class MessageDelta:
    run_id: str
    message_id: str
    delta: str
    type: str = "message_delta"
```

- [ ] **Step 4: Export the new runtime event surface deliberately**

Update exports so the new runtime event types are reachable from `openagent.runtime` and only intentionally promoted symbols are re-exported from `openagent`.

- [ ] **Step 5: Run the event tests to verify they pass**

Run: `uv run pytest tests/runtime/test_events.py -v`
Expected: PASS.

## Task 2: Extract Session-State-Only APIs and Memory Service

**Files:**
- Create: `openagent/runtime/memory.py`
- Modify: `openagent/core/session.py`
- Modify: `openagent/core/utils.py`
- Test: `tests/runtime/test_memory.py`
- Test: `tests/test_session.py`

- [ ] **Step 1: Write failing tests for session narrowing and compaction orchestration**

```python
def test_session_can_replace_history_with_compacted_messages(session):
    compacted = [Message(role="system", content="Conversation summary")]
    session.replace_history(compacted)
    assert session.messages == compacted


def test_compacted_history_persists_as_system_summary_message(session):
    compacted = [Message(role="system", content="Conversation summary")]
    session.replace_history(compacted)
    assert session.messages[0].role == "system"


@pytest.mark.asyncio
async def test_memory_manager_returns_compaction_plan_without_mutating_session(session, summarizer):
    manager = MemoryManager(summarizer=summarizer)
    plan = await manager.compact(session, keep_recent=2)
    assert session.messages != plan.compacted_messages
```

- [ ] **Step 2: Run the session and memory tests to verify they fail**

Run: `uv run pytest tests/test_session.py tests/runtime/test_memory.py -v`
Expected: FAIL because `replace_history` and `MemoryManager` do not exist yet.

- [ ] **Step 3: Implement the minimal session replacement API and memory service**

```python
class Session:
    def replace_history(self, messages: list[Message]) -> None:
        self._messages = list(messages)


class MemoryManager:
    async def compact(self, session: Session, keep_recent: int = 5) -> CompactionPlan:
        ...
```

- [ ] **Step 4: Add tokenizer fallback handling while preserving current behavior where possible**

Ensure token counting does not crash when `tiktoken` is unavailable. Use a deterministic fallback counter so runtime and tests can proceed in environments without that optional dependency.

- [ ] **Step 5: Run the session and memory tests to verify they pass**

Run: `uv run pytest tests/test_session.py tests/runtime/test_memory.py -v`
Expected: PASS.

## Task 3: Split Built-In Tools and Remove Hidden Global Coupling Early

**Files:**
- Create: `openagent/tools/builtin/__init__.py`
- Create: `openagent/tools/builtin/files.py`
- Create: `openagent/tools/builtin/shell.py`
- Create: `openagent/tools/builtin/web.py`
- Modify: `openagent/tools/builtin.py` (move contents into package, then remove)
- Modify: `openagent/tools/__init__.py`
- Modify: `openagent/infrastructure/fs_guard.py`
- Test: `tests/test_builtin_tools.py`
- Test: `tests/infrastructure/test_fs_guard.py`

- [ ] **Step 1: Write failing tests for split modules and reusable filesystem guard behavior**

```python
def test_file_tools_are_importable_from_split_modules():
    from openagent.tools.builtin.files import read, write
    assert read is not None


def test_fs_guard_rejects_paths_outside_project_root(tmp_path):
    guard = FileSystemGuard(project_root=str(tmp_path))
    assert not guard.is_allowed("/tmp/outside")
```

- [ ] **Step 2: Run the tool and infrastructure tests to verify they fail**

Run: `uv run pytest tests/test_builtin_tools.py tests/infrastructure/test_fs_guard.py -v`
Expected: FAIL because the split modules and extracted guard do not exist yet.

- [ ] **Step 3: Implement the minimal split and extracted guard helpers**

```python
class FileSystemGuard:
    def is_allowed(self, path: str) -> bool:
        ...
```

- [ ] **Step 4: Move `openagent/tools/builtin.py` into the new package layout**

Create `openagent/tools/builtin/__init__.py`, move concrete implementations into the split modules, update imports to the package form, and then remove the old monolithic `openagent/tools/builtin.py` file so there is no file/package collision.

- [ ] **Step 5: Remove direct duplicated path-guard logic from built-in tools**

Move repeated project-root checks behind the extracted filesystem guard instead of repeating inline logic.

- [ ] **Step 6: Run the tool and infrastructure tests to verify they pass**

Run: `uv run pytest tests/test_builtin_tools.py tests/infrastructure/test_fs_guard.py -v`
Expected: PASS.

## Task 4: Extract Tool Registry and Explicit Tool Executor

**Files:**
- Create: `openagent/tools/registry.py`
- Create: `openagent/tools/executor.py`
- Create: `openagent/runtime/tool_executor.py`
- Modify: `openagent/core/tool.py`
- Modify: `openagent/tools/__init__.py`
- Test: `tests/runtime/test_tool_executor.py`
- Test: `tests/test_tool.py`

- [ ] **Step 1: Write failing tests for explicit runtime-context tool execution**

```python
@tool
def read_name(context, suffix: str) -> str:
    return context.project_name + suffix


@pytest.mark.asyncio
async def test_tool_executor_passes_runtime_context():
    registry = ToolRegistry()
    registry.register(read_name)
    executor = ToolExecutor(registry)
    result = await executor.execute(call, context=RuntimeContext(project_name="solo"))
    assert result.content == "solo!"
```

- [ ] **Step 2: Run the tool tests to verify they fail**

Run: `uv run pytest tests/test_tool.py tests/runtime/test_tool_executor.py -v`
Expected: FAIL because `ToolExecutor` and context-aware execution do not exist yet.

- [ ] **Step 3: Implement the minimal extracted registry/executor pair**

```python
class ToolExecutor:
    async def execute(self, tool_call: ToolUseBlock, context: RuntimeContext) -> ToolResultBlock:
        ...
```

- [ ] **Step 4: Keep compatibility imports working while new paths become primary**

Retain `openagent.core.tool` as a compatibility wrapper or re-export layer until the runtime and product code fully migrate.

- [ ] **Step 5: Run the tool tests to verify they pass**

Run: `uv run pytest tests/test_tool.py tests/runtime/test_tool_executor.py -v`
Expected: PASS.

## Task 5: Move Infrastructure Out of Core and Make Runtime Dependencies Explicit

**Files:**
- Create: `openagent/infrastructure/__init__.py`
- Create: `openagent/infrastructure/bash_manager.py`
- Create: `openagent/infrastructure/web.py`
- Create: `openagent/infrastructure/mcp.py`
- Modify: `openagent/core/bash_manager.py`
- Modify: `openagent/core/task_manager.py`
- Modify: `openagent/core/skill_manager.py`
- Modify: `openagent/core/agent.py`
- Modify: `openagent/coder.py`
- Modify: `openagent/mcp.py`
- Modify: `openagent/runtime/context.py`
- Test: `tests/infrastructure/test_bash_manager.py`
- Test: `tests/infrastructure/test_mcp.py`
- Test: `tests/test_tools_simple.py`
- Test: `tests/test_all_tools.py`
- Test: `tests/test_agent_with_tools.py`

- [ ] **Step 1: Write failing tests for infrastructure package boundaries**

```python
def test_bash_manager_is_importable_from_infrastructure_package():
    from openagent.infrastructure.bash_manager import BashManager
    assert BashManager is not None


def test_mcp_client_is_importable_from_infrastructure_package():
    from openagent.infrastructure.mcp import McpClient
    assert McpClient is not None
```

- [ ] **Step 2: Run the infrastructure tests to verify they fail**

Run: `uv run pytest tests/infrastructure/test_bash_manager.py tests/infrastructure/test_mcp.py -v`
Expected: FAIL because the infrastructure package paths do not exist yet.

- [ ] **Step 3: Implement the minimal infrastructure package and compatibility wrappers**

```python
from openagent.core.bash_manager import BashManager
```

- [ ] **Step 4: Make runtime dependencies flow through explicit runtime context objects**

Ensure the runtime gets infrastructure dependencies through typed context instead of singleton lookup in core execution paths.

- [ ] **Step 5: Remove task and skill service singleton dependence from core execution paths**

Update runtime/core composition so task and skill services are app-layer dependencies instead of ambient globals referenced by core execution.

- [ ] **Step 6: Run the infrastructure and affected legacy suites to verify they pass**

Run: `uv run pytest tests/infrastructure/test_bash_manager.py tests/infrastructure/test_mcp.py tests/test_tools_simple.py tests/test_all_tools.py tests/test_agent_with_tools.py -v`
Expected: PASS.

## Task 6: Implement the New Runtime Agent with Unified `run()` and `stream()`

**Files:**
- Create: `openagent/runtime/agent.py`
- Create: `openagent/runtime/context.py`
- Modify: `openagent/core/agent.py`
- Modify: `openagent/__init__.py`
- Test: `tests/runtime/test_agent_runtime.py`
- Test: `tests/test_agent.py`

- [ ] **Step 1: Write failing tests for unified streaming and non-streaming execution**

```python
@pytest.mark.asyncio
async def test_run_collects_same_result_as_stream(mock_provider):
    agent = Agent(provider=mock_provider())
    result = await agent.run("hello")
    events = [event async for event in agent.stream("hello")]
    assert result.output_text == "".join(e.delta for e in events if e.type == "message_delta")


@pytest.mark.asyncio
async def test_post_tool_output_starts_new_message(mock_provider):
    events = [event async for event in agent.stream("do work")]
    message_ids = [e.message_id for e in events if e.type == "message_started"]
    assert len(message_ids) == 2
    assert message_ids[0] != message_ids[1]


@pytest.mark.asyncio
async def test_parallel_tool_events_keep_stable_message_and_tool_identity(mock_provider):
    events = [event async for event in agent.stream("parallel")]
    tool_events = [e for e in events if e.type.startswith("tool_call_")]
    assert all(e.message_id for e in tool_events)
    assert all(e.tool_call_id for e in tool_events)
```

- [ ] **Step 2: Run the runtime agent tests to verify they fail**

Run: `uv run pytest tests/test_agent.py tests/runtime/test_agent_runtime.py -v`
Expected: FAIL because the new runtime `Agent` and stream contract do not exist yet.

- [ ] **Step 3: Implement the minimal event-driven runtime agent**

```python
class Agent:
    async def run(self, user_input: str, **kwargs) -> AgentResult:
        ...

    async def stream(self, user_input: str, **kwargs):
        yield RunStarted(...)
        ...
```

- [ ] **Step 4: Bridge `openagent.core.agent.Agent` to the new runtime implementation**

Keep import compatibility while moving the real logic into `openagent.runtime.agent`.

- [ ] **Step 5: Run the runtime agent tests to verify they pass**

Run: `uv run pytest tests/test_agent.py tests/runtime/test_agent_runtime.py -v`
Expected: PASS.

## Task 7: Add Streaming Edge-Case Tests Before Provider Migration

**Files:**
- Modify: `tests/runtime/test_agent_runtime.py`
- Modify: `tests/runtime/test_memory.py`
- Create: `tests/providers/test_provider_stream_contract.py`

- [ ] **Step 1: Add failing tests for fallback streaming, cancellation, and stable compaction boundaries**

```python
@pytest.mark.asyncio
async def test_fallback_stream_emits_terminal_message_completed_event():
    ...


@pytest.mark.asyncio
async def test_cancellation_emits_run_cancelled_after_cleanup_boundary():
    ...


@pytest.mark.asyncio
async def test_compaction_does_not_run_mid_message():
    ...
```

- [ ] **Step 2: Run the streaming tests to verify they fail**

Run: `uv run pytest tests/runtime/test_agent_runtime.py tests/runtime/test_memory.py tests/providers/test_provider_stream_contract.py -v`
Expected: FAIL until the runtime and provider streaming boundaries are implemented fully.

- [ ] **Step 3: Implement the minimum runtime hooks needed for these behaviors**

Keep this focused on test-enabling seams, not broad provider migration yet.

- [ ] **Step 4: Run the streaming tests to verify they pass**

Run: `uv run pytest tests/runtime/test_agent_runtime.py tests/runtime/test_memory.py tests/providers/test_provider_stream_contract.py -v`
Expected: PASS.

## Task 8: Move SoloCoder Rendering and Product Composition Out of Core

**Files:**
- Create: `openagent/apps/solocoder/__init__.py`
- Create: `openagent/apps/solocoder/agent.py`
- Create: `openagent/apps/solocoder/display.py`
- Create: `openagent/apps/solocoder/cli.py`
- Modify: `openagent/coder.py`
- Modify: `openagent/core/display.py`
- Modify: `cli_coder.py`
- Test: `tests/apps/test_solocoder_cli.py`

- [ ] **Step 1: Write the failing app-layer packaging and rendering tests**

```python
def test_solocoder_package_exports_coder_agent():
    from openagent.apps.solocoder import CoderAgent
    assert CoderAgent is not None


def test_renderer_formats_tool_events_without_runtime_prints(capsys):
    renderer = SoloCoderRenderer()
    renderer.handle(ToolCallStarted(...))
    captured = capsys.readouterr()
    assert "tool" in captured.out.lower()
```

- [ ] **Step 2: Run the SoloCoder app tests to verify they fail**

Run: `uv run pytest tests/apps/test_solocoder_cli.py -v`
Expected: FAIL because the new app-layer package and renderer do not exist yet.

- [ ] **Step 3: Create the app package and package exports**

Add `openagent/apps/solocoder/__init__.py` and re-home `CoderAgent` composition there.

- [ ] **Step 4: Implement the renderer and CLI module in small slices**

Move terminal rendering and interactive CLI logic out of runtime paths while keeping `cli_coder.py` as a thin wrapper.

- [ ] **Step 5: Run the SoloCoder app tests to verify they pass**

Run: `uv run pytest tests/apps/test_solocoder_cli.py -v`
Expected: PASS.

## Task 9: Normalize OpenAI and Ollama Provider Contracts First

**Files:**
- Create: `openagent/providers/base.py`
- Create: `openagent/providers/__init__.py`
- Create: `openagent/providers/events.py`
- Modify: `openagent/provider/__init__.py`
- Modify: `openagent/provider/base.py`
- Modify: `openagent/provider/converter.py`
- Modify: `openagent/provider/openai.py`
- Modify: `openagent/provider/ollama.py`
- Test: `tests/providers/test_provider_stream_contract.py`
- Test: `tests/test_openai_provider.py`
- Test: `tests/test_ollama_provider.py`

- [ ] **Step 1: Write failing tests for provider streaming normalization**

```python
@pytest.mark.asyncio
async def test_provider_stream_yields_provider_stream_events(provider):
    events = [event async for event in provider.stream(messages=[])]
    assert all(event.type.startswith("provider_") for event in events)


@pytest.mark.asyncio
async def test_provider_stream_has_single_terminal_event(provider):
    events = [event async for event in provider.stream(messages=[])]
    terminal = [event for event in events if event.type in {"provider_message_completed", "provider_error"}]
    assert len(terminal) == 1
```

- [ ] **Step 2: Run provider tests to verify they fail**

Run: `uv run pytest tests/providers/test_provider_stream_contract.py tests/test_openai_provider.py tests/test_ollama_provider.py -v`
Expected: FAIL because the new provider stream contract is not implemented yet.

- [ ] **Step 3: Implement the minimal provider stream contract and adapt OpenAI/Ollama first**

```python
class BaseProvider(ABC):
    async def chat(...):
        ...

    async def stream(...):
        yield ProviderMessageStarted(...)
```

- [ ] **Step 4: Keep legacy provider import paths working while new package paths stabilize**

Retain compatibility exports from `openagent.provider.*` to the new `openagent.providers.*` modules until the app layer is migrated.

- [ ] **Step 5: Run provider tests to verify they pass or reduce to known optional-dependency skips**

Run: `uv run pytest tests/providers/test_provider_stream_contract.py tests/test_openai_provider.py tests/test_ollama_provider.py -v`
Expected: PASS except for clearly marked optional-dependency skips if the repository standardizes that behavior.

## Task 10: Normalize Anthropic and Google Provider Contracts

**Files:**
- Modify: `openagent/provider/anthropic.py`
- Modify: `openagent/provider/google.py`
- Test: `tests/providers/test_anthropic_provider.py`
- Test: `tests/providers/test_google_provider.py`

- [ ] **Step 1: Write failing Anthropic and Google provider contract tests**

```python
@pytest.mark.asyncio
async def test_anthropic_provider_matches_provider_stream_contract():
    ...


@pytest.mark.asyncio
async def test_google_provider_matches_provider_stream_contract():
    ...
```

- [ ] **Step 2: Run the Anthropic and Google tests to verify they fail**

Run: `uv run pytest tests/providers/test_anthropic_provider.py tests/providers/test_google_provider.py -v`
Expected: FAIL until the provider adapters implement the normalized contract.

- [ ] **Step 3: Implement the minimal Anthropic and Google contract adapters**

Match the same provider stream semantics already established for OpenAI and Ollama.

- [ ] **Step 4: Run the Anthropic and Google tests to verify they pass**

Run: `uv run pytest tests/providers/test_anthropic_provider.py tests/providers/test_google_provider.py -v`
Expected: PASS or PASS with clearly marked optional-dependency skips only.

## Task 11: Move Workflow Tools into the SoloCoder App Layer

**Files:**
- Modify: `openagent/apps/solocoder/agent.py`
- Modify: `openagent/tools/__init__.py`
- Modify: `openagent/tools/builtin.py` (remove after workflow tools migrate)
- Test: `tests/test_builtin_tools.py`

- [ ] **Step 1: Write failing tests for app-owned workflow tool composition**

```python
def test_workflow_tools_live_in_solocoder_package():
    from openagent.apps.solocoder.agent import build_solocoder_tools
    assert callable(build_solocoder_tools)
```

- [ ] **Step 2: Run the workflow composition tests to verify they fail**

Run: `uv run pytest tests/test_builtin_tools.py -v`
Expected: FAIL until the workflow tools move out of reusable core composition.

- [ ] **Step 3: Implement the minimal app-owned workflow tool builder**

Move planning/task/skill-oriented tool composition into the SoloCoder app layer while retaining compatibility re-exports only where necessary.

- [ ] **Step 4: Run the workflow composition tests to verify they pass**

Run: `uv run pytest tests/test_builtin_tools.py -v`
Expected: PASS.

## Task 12: Final Export Cleanup and Integration Verification

**Files:**
- Modify: `openagent/__init__.py`
- Modify: `openagent/provider/__init__.py`
- Modify: `openagent/core/__init__.py`
- Modify: `README.md`
- Modify: `dev_docs/architecture.md`
- Test: `tests/test_agent.py`
- Test: `tests/test_tool.py`
- Test: `tests/test_session.py`

- [ ] **Step 1: Write failing tests for the intended public surface**

```python
def test_openagent_exports_runtime_first_public_api():
    import openagent
    assert hasattr(openagent, "Agent")
    assert hasattr(openagent, "Session")
    assert not hasattr(openagent, "display_tool_call_claude_style")
```

- [ ] **Step 2: Run the public API tests to verify they fail**

Run: `uv run pytest tests/test_agent.py tests/test_tool.py tests/test_session.py -v`
Expected: FAIL until the final export surface is cleaned up.

- [ ] **Step 3: Implement the final export cleanup and doc alignment**

```python
# openagent/__init__.py
from openagent.runtime.agent import Agent
from openagent.core.session import Session
```

- [ ] **Step 4: Run the focused integration test suite**

Run: `uv run pytest tests/test_agent.py tests/test_tool.py tests/test_session.py tests/test_builtin_tools.py tests/test_openai_provider.py tests/test_ollama_provider.py -v`
Expected: PASS, or PASS with clearly documented optional-dependency skips only.

- [ ] **Step 5: Run the full test suite for final verification**

Run: `uv run pytest`
Expected: PASS, or PASS with clearly documented optional-dependency skips only. Any remaining failures must be called out explicitly as pre-existing or intentionally deferred.
