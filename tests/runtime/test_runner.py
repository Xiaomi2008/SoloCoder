"""The streaming, tool-capable run loop.

These tests are the executable version of the event contract in
``dev_docs/agent-service-protocol.md`` §4. If an ordering guarantee is stated
there, a test here asserts it, because the SwiftUI shell is written against
that document and nothing else enforces it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from openagent.core.session import Session
from openagent.core.tool import ToolContext, ToolRegistry, tool
from openagent.core.types import Message
from openagent.runtime.events import (
    ContextCompactionStarted,
    MessageCompleted,
    MessageDelta,
    MessageFailed,
    MessageStarted,
    PermissionRequested,
    PermissionResolved,
    ReasoningDelta,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
)
from openagent.runtime.permissions import (
    AllowAllGate,
    PermissionDecision,
    PermissionRequest,
    ReadOnlyGate,
    RiskGate,
    gate_for_mode,
    risk_reason,
    tool_risk,
)
from openagent.runtime.runner import MAX_TURNS_MESSAGE, AgentRunner
from tests.support.streaming_provider import (
    HangingProvider,
    ScriptedStreamProvider,
    empty_turn,
    error_turn,
    raising_turn,
    reasoning_turn,
    text_turn,
    tool_turn,
)


@tool
def echo(text: str) -> str:
    """Echo the text back."""
    return f"echo:{text}"


@tool
def boom(text: str) -> str:
    """Always fails."""
    raise RuntimeError(f"exploded on {text}")


def scoped_registry() -> tuple[ToolRegistry, list[str]]:
    """A tool that writes through the injected session scope."""
    seen: list[str] = []

    @tool
    def touch(name: str, tool_context: ToolContext) -> str:
        """Write a file inside the session directory."""
        path = tool_context.resolve(name)
        seen.append(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("hi")
        return f"wrote {path}"

    registry = ToolRegistry()
    registry.register(touch)
    return registry, seen


async def collect(runner: AgentRunner, prompt: str = "go", **kwargs: Any) -> list[RuntimeEvent]:
    return [event async for event in runner.stream(prompt, **kwargs)]


# --------------------------------------------------------------- happy paths


async def test_text_run_emits_the_documented_sequence():
    provider = ScriptedStreamProvider([text_turn("Hel", "lo")])
    runner = AgentRunner(provider, tools=[echo])

    events = await collect(runner)

    assert [e.type for e in events] == [
        "run_started",
        "context_usage",
        "message_started",
        "message_delta",
        "message_delta",
        "message_completed",
        "context_usage",
        "run_completed",
    ]
    result = events[-1].result
    assert result.output_text == "Hello"
    assert result.stop_reason == "end_turn"
    assert result.run_id == events[0].run_id
    assert result.final_message_id == events[2].message_id


async def test_tool_call_runs_and_reports_its_result():
    provider = ScriptedStreamProvider(
        [tool_turn("echo", {"text": "hi"}, id="call_9"), text_turn("done")]
    )
    runner = AgentRunner(provider, tools=[echo])

    events = await collect(runner)
    types = [e.type for e in events]

    assert "tool_call_started" in types and "tool_call_completed" in types
    started = next(e for e in events if isinstance(e, ToolCallStarted))
    completed = next(e for e in events if isinstance(e, ToolCallCompleted))
    assert started.tool_name == "echo"
    assert started.tool_call_id == "call_9"
    assert completed.result == "echo:hi"
    # The assistant message closes before tool events begin (contract §4.3).
    assert types.index("message_completed") < types.index("tool_call_started")
    assert types[-1] == "run_completed"


async def test_the_model_is_shown_the_tools_and_the_history():
    provider = ScriptedStreamProvider([text_turn("ok")])
    runner = AgentRunner(provider, tools=[echo, boom], system_prompt="BE HELPFUL")

    await collect(runner, prompt="do the thing")

    first = provider.calls[0]
    assert {t.name for t in first["tools"]} == {"echo", "boom"}
    assert first["system_prompt"] == "BE HELPFUL"
    assert first["messages"][0].text == "do the thing"


async def test_tool_results_go_back_into_the_session():
    provider = ScriptedStreamProvider(
        [tool_turn("echo", {"text": "x"}), text_turn("finished")]
    )
    runner = AgentRunner(provider, tools=[echo])

    await collect(runner)

    roles = [m.role for m in runner.session.messages]
    assert roles == ["user", "assistant", "tool_result", "assistant"]
    assert runner.session.messages[2].tool_results[0].content == "echo:x"


async def test_runner_preserves_the_callers_session():
    """A new ``Session`` is empty, and empty is falsy — so ``session or Session()``
    silently throws away the caller's session.

    The service owns the session it hands the runner: it saves it when the run
    ends, and a reconnect loads it. Swap in a fresh one and every conversation
    is saved empty, which is exactly the bug this test pins.
    """
    session = Session(system_prompt="sp")
    provider = ScriptedStreamProvider([text_turn("a"), text_turn("b")])
    runner = AgentRunner(provider, session=session, tools=[])

    await collect(runner, "first")

    assert runner.session is session
    assert [m.role for m in session.messages] == ["user", "assistant"]


async def test_session_scope_is_injected_and_anchors_relative_paths(tmp_path):
    registry, written = scoped_registry()
    provider = ScriptedStreamProvider(
        [tool_turn("touch", {"name": "sub/note.txt"}), text_turn("done")]
    )
    runner = AgentRunner(
        provider, tools=registry, tool_context=ToolContext(working_dir=str(tmp_path))
    )

    events = await collect(runner)

    assert (tmp_path / "sub" / "note.txt").read_text() == "hi"
    assert written == [str(tmp_path / "sub" / "note.txt")]
    assert not (Path.cwd() / "sub" / "note.txt").exists()
    # The scope is a harness fact: it must not appear in the model's schema.
    tool_def = next(t for t in provider.calls[0]["tools"] if t.name == "touch")
    assert "tool_context" not in tool_def.parameters["properties"]


# ------------------------------------------------------------------- failures


async def test_tool_failure_emits_tool_call_failed_and_keeps_running():
    provider = ScriptedStreamProvider(
        [tool_turn("boom", {"text": "x"}), text_turn("recovered")]
    )
    runner = AgentRunner(provider, tools=[boom])

    events = await collect(runner)
    failed = next(e for e in events if isinstance(e, ToolCallFailed))

    assert "exploded on x" in failed.error
    assert not any(isinstance(e, ToolCallCompleted) for e in events)
    assert events[-1].result.output_text == "recovered"


async def test_provider_error_event_fails_the_run():
    provider = ScriptedStreamProvider([error_turn("upstream 503")])
    runner = AgentRunner(provider)

    events = await collect(runner)

    assert [e.type for e in events] == [
        "run_started",
        "context_usage",
        "message_started",
        "message_failed",
        "run_failed",
    ]
    assert events[-1].error == "upstream 503"
    assert isinstance(events[-2], MessageFailed)


async def test_stream_exception_fails_the_run():
    provider = ScriptedStreamProvider([raising_turn("connection reset")])
    runner = AgentRunner(provider)

    events = await collect(runner)

    assert events[-1].type == "run_failed"
    assert "connection reset" in events[-1].error
    assert events[-2].type == "message_failed"


async def test_empty_responses_are_retried_then_reported():
    provider = ScriptedStreamProvider([empty_turn(), empty_turn(), empty_turn(), empty_turn()])
    runner = AgentRunner(provider)

    events = await collect(runner, max_empty_response_retries=2)

    assert [e.type for e in events].count("message_failed") == 3
    assert events[-1].type == "run_failed"
    assert "empty responses" in events[-1].error
    # A retry nudge went into the session. Exactly one: the guard refuses to
    # stack identical system messages when a model keeps failing.
    nudges = [m for m in runner.session.messages if m.role == "system"]
    assert len(nudges) == 1


async def test_max_turns_is_a_stop_not_a_crash():
    provider = ScriptedStreamProvider(
        [tool_turn("echo", {"text": str(i)}) for i in range(10)]
    )
    runner = AgentRunner(provider, tools=[echo], max_turns=2)

    events = await collect(runner)

    assert events[-1].type == "run_completed"
    assert events[-1].result.stop_reason == "max_turns"
    assert events[-1].result.output_text == MAX_TURNS_MESSAGE
    # max_turns plus exactly one grace turn for the final text. The old loop
    # re-armed its own exit condition on every tool batch and never terminated.
    assert len(provider.calls) == 3


# --------------------------------------------------------------- permissions


async def test_permission_prompt_pair_precedes_the_tool_call():
    async def approve(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision(granted=True, scope="once")

    provider = ScriptedStreamProvider(
        [tool_turn("echo", {"text": "hi"}), text_turn("done")]
    )
    runner = AgentRunner(
        provider,
        tools=[echo],
        # `echo` is not a built-in tool, so it classifies as unknown -> asked.
        permission_gate=RiskGate(approve, ask_risks=frozenset({"unknown"})),
    )

    events = await collect(runner)
    types = [e.type for e in events]

    assert types.index("permission_requested") < types.index("tool_call_started")
    requested = next(e for e in events if isinstance(e, PermissionRequested))
    resolved = next(e for e in events if isinstance(e, PermissionResolved))
    assert requested.request_id == resolved.request_id
    assert requested.tool_name == "echo"
    assert requested.reason == risk_reason("echo")
    assert resolved.granted and resolved.scope == "once"


async def test_denied_tool_is_an_errored_result_with_no_started_event():
    async def refuse(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision(granted=False, scope="never", reason="not today")

    provider = ScriptedStreamProvider(
        [tool_turn("echo", {"text": "hi"}), text_turn("understood")]
    )
    runner = AgentRunner(
        provider,
        tools=[echo],
        permission_gate=RiskGate(refuse, ask_risks=frozenset({"unknown"})),
    )

    events = await collect(runner)
    types = [e.type for e in events]

    assert "tool_call_started" not in types
    assert "tool_call_completed" not in types
    denial = runner.session.messages[2].tool_results[0]
    assert denial.is_error and "not today" in denial.content
    assert events[-1].type == "run_completed"


async def test_a_gate_that_raises_denies_instead_of_running_the_tool():
    async def broken(request: PermissionRequest) -> PermissionDecision:
        raise RuntimeError("socket closed")

    ran = []

    @tool
    def dangerous() -> str:
        """Must not run."""
        ran.append(True)
        return "ran"

    provider = ScriptedStreamProvider([tool_turn("dangerous", {}), text_turn("ok")])
    runner = AgentRunner(
        provider, tools=[dangerous], permission_gate=RiskGate(broken, ask_risks=frozenset({"unknown"}))
    )

    events = await collect(runner)

    assert ran == []
    assert next(e for e in events if isinstance(e, PermissionResolved)).granted is False


async def test_readonly_gate_refuses_writes_without_a_prompt():
    """A refusal nobody can answer must not be rendered as a prompt."""
    wrote = []

    @tool
    def write_file(path: str) -> str:
        """Write a file."""
        wrote.append(path)
        return "written"

    provider = ScriptedStreamProvider(
        [tool_turn("write_file", {"path": "x.txt"}), text_turn("ok")]
    )
    runner = AgentRunner(provider, tools=[write_file], permission_gate=ReadOnlyGate())

    events = await collect(runner)

    assert wrote == []
    assert not any(isinstance(e, PermissionRequested) for e in events)
    assert "Permission denied" in runner.session.messages[2].tool_results[0].content


async def test_allow_all_gate_prompts_nobody():
    provider = ScriptedStreamProvider(
        [tool_turn("echo", {"text": "hi"}), text_turn("done")]
    )
    runner = AgentRunner(provider, tools=[echo], permission_gate=AllowAllGate())

    events = await collect(runner)

    assert not any(isinstance(e, PermissionRequested) for e in events)
    assert any(isinstance(e, ToolCallCompleted) for e in events)


async def test_session_scope_answers_the_tool_once_and_silently_after():
    asked: list[str] = []

    async def approve(request: PermissionRequest) -> PermissionDecision:
        asked.append(request.tool_name)
        return PermissionDecision(granted=True, scope="session")

    gate = RiskGate(approve, ask_risks=frozenset({"unknown"}))
    provider = ScriptedStreamProvider(
        [
            tool_turn("echo", {"text": "a"}, id="c1"),
            text_turn("mid"),
            tool_turn("echo", {"text": "b"}, id="c2"),
            text_turn("done"),
        ]
    )
    runner = AgentRunner(provider, tools=[echo], permission_gate=gate)

    events = await collect(runner)

    assert asked == ["echo"]
    assert [e.type for e in events].count("permission_requested") == 1


def test_gate_for_mode_ask_without_a_callback_is_readonly():
    """Nobody to ask is not the same as permission to act."""
    assert isinstance(gate_for_mode("ask"), ReadOnlyGate)
    assert isinstance(gate_for_mode("allow_all"), AllowAllGate)


def test_unknown_tools_classify_as_exec_grade():
    assert tool_risk("no_such_tool") == "unknown"
    assert tool_risk("bash") == "exec"
    assert "unknown" in risk_reason("no_such_tool")


# ------------------------------------------------------------- cancellation


async def test_cooperative_cancel_stops_at_the_tool_boundary():
    runner_holder: dict[str, AgentRunner] = {}

    @tool
    def stop_after() -> str:
        """Ask the run to stop."""
        runner_holder["runner"].cancel("user pressed stop")
        return "ok"

    provider = ScriptedStreamProvider([tool_turn("stop_after", {}), text_turn("never")])
    runner = AgentRunner(provider, tools=[stop_after])
    runner_holder["runner"] = runner

    events = await collect(runner)

    assert events[-1].type == "run_cancelled"
    assert events[-1].reason == "user pressed stop"
    assert not any(isinstance(e, ContextCompactionStarted) for e in events)


async def test_task_cancellation_still_ends_on_run_cancelled():
    provider = HangingProvider()
    runner = AgentRunner(provider)
    events: list[RuntimeEvent] = []

    async def consume() -> None:
        async for event in runner.stream("go"):
            events.append(event)

    task = asyncio.create_task(consume())
    while not provider.entered:
        await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert events[-1].type == "run_cancelled"
    assert events[-1].run_id == events[0].run_id


# ------------------------------------------------------------------ invariants


async def test_a_run_prints_nothing(capsys):
    """The service owns the terminal; the engine must not write to it."""
    provider = ScriptedStreamProvider(
        [tool_turn("echo", {"text": "hi"}), text_turn("done")]
    )
    runner = AgentRunner(provider, tools=[echo])

    await collect(runner)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


async def test_exactly_one_terminal_event_and_nothing_after():
    provider = ScriptedStreamProvider(
        [tool_turn("echo", {"text": "hi"}), text_turn("done")]
    )
    runner = AgentRunner(provider, tools=[echo])

    events = await collect(runner)

    terminals = {"run_completed", "run_failed", "run_cancelled"}
    types = [e.type for e in events]
    assert [t for t in types if t in terminals] == [types[-1]]


async def test_every_delta_belongs_to_an_opened_and_closed_message():
    provider = ScriptedStreamProvider([text_turn("a", "b", "c")])
    runner = AgentRunner(provider)

    events = await collect(runner)

    started = [e for e in events if isinstance(e, MessageStarted)]
    deltas = [e for e in events if isinstance(e, MessageDelta)]
    completed = [e for e in events if isinstance(e, MessageCompleted)]
    assert len(started) == len(completed) == 1
    assert {d.message_id for d in deltas} == {started[0].message_id}
    assert completed[0].output_text == "".join(d.delta for d in deltas)


async def test_reasoning_arrives_before_the_answer_and_never_becomes_history():
    """A reasoning model spends ~10 seconds on private tokens first.

    Measured on `swift-1.5-iq3_s`: ~1,900 reasoning tokens, then ~330 answer
    tokens. A client that never sees the reasoning shows a spinner that has not
    moved for ten seconds, which reads as a hang; a model that is shown its own
    reasoning pays for it again on every turn.
    """
    provider = ScriptedStreamProvider(
        [reasoning_turn("think-1 ", "think-2 ", text="the answer")]
    )
    runner = AgentRunner(provider)

    events = await collect(runner)

    assert [e.type for e in events] == [
        "run_started",
        "context_usage",
        "message_started",
        "reasoning_delta",
        "reasoning_delta",
        "message_delta",
        "message_completed",
        "context_usage",
        "run_completed",
    ]
    assert [e.delta for e in events if isinstance(e, ReasoningDelta)] == ["think-1 ", "think-2 "]
    completed = [e for e in events if isinstance(e, MessageCompleted)][0]
    assert completed.output_text == "the answer"

    carried = " ".join(str(m.content) for m in provider.calls[0]["messages"])
    assert "think-" not in carried, "the model's private reasoning leaked into history"


async def test_reasoning_effort_reaches_every_provider_turn():
    """The knob is per-run and applies to every turn of it, not just the first.

    A tool loop is several provider calls; thinking turned off has to stay off
    across them, or the second turn is slow again for no visible reason.
    """
    registry, _ = scoped_registry()
    provider = ScriptedStreamProvider(
        [tool_turn("touch", {"name": "note.txt"}), text_turn("done")]
    )
    runner = AgentRunner(
        provider, tools=registry, reasoning_effort="none"
    )

    await collect(runner)

    assert len(provider.calls) == 2, "the run should have made a tool turn and an answer turn"
    assert all(c["kwargs"].get("reasoning_effort") == "none" for c in provider.calls)


async def test_run_helper_returns_the_result_and_raises_on_failure():
    ok = AgentRunner(ScriptedStreamProvider([text_turn("fine")]))
    assert (await ok.run("go")).output_text == "fine"

    bad = AgentRunner(ScriptedStreamProvider([error_turn("nope")]))
    with pytest.raises(RuntimeError, match="nope"):
        await bad.run("go")


async def test_the_runner_compacts_against_its_context_window():
    long_history = "word " * 3000
    for window, compacts in ((1000, True), (None, False)):
        session = Session()
        for _ in range(4):
            session.add_message(Message(role="user", content=long_history))
            session.add_message(Message(role="assistant", content="ok"))
        provider = ScriptedStreamProvider([text_turn("done")])
        runner = AgentRunner(provider, session=session, context_window=window)

        events = await collect(runner)

        started = any(isinstance(e, ContextCompactionStarted) for e in events)
        assert started is compacts, window



class _SummaryProvider(ScriptedStreamProvider):
    """Streams the run's turns; answers the summary request from ``summaries``."""

    def __init__(self, turns, summaries):
        super().__init__(turns)
        self.summaries = list(summaries)
        self.summary_kwargs: list[dict[str, Any]] = []

    async def chat(self, messages, tools=None, system_prompt="", **kwargs):
        self.summary_kwargs.append(kwargs)
        return Message(role="assistant", content=self.summaries.pop(0))


def _long_session() -> Session:
    session = Session()
    for _ in range(4):
        session.add_message(Message(role="user", content="word " * 3000))
        session.add_message(Message(role="assistant", content="ok"))
    return session


async def test_an_empty_summary_is_retried_without_thinking():
    provider = _SummaryProvider([text_turn("done")], ["", "the summary"])
    session = _long_session()
    runner = AgentRunner(provider, session=session, context_window=1000)

    events = await collect(runner)

    assert provider.summary_kwargs[1].get("reasoning_effort") == "none"
    assert any(e.type == "context_compaction_completed" for e in events)
    assert "the summary" in session.messages[0].text


async def test_a_summary_that_stays_empty_fails_and_keeps_the_history():
    """An empty summary replaced the history with nothing, reported no end,
    and left the app on "compacting context" for good."""
    provider = _SummaryProvider([text_turn("done")], ["", ""])
    session = _long_session()
    runner = AgentRunner(provider, session=session, context_window=1000)

    events = await collect(runner)

    types = [e.type for e in events]
    assert "context_compaction_started" in types
    assert "context_compaction_failed" in types
    assert not any("Conversation summary:\n\n" == m.text for m in session.messages)
    assert events[-1].type == "run_completed"
