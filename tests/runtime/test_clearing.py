"""Clearing old tool outputs: the step before a summary, with recall."""

from __future__ import annotations

from openagent.core.session import Session
from openagent.core.tool import ToolContext
from openagent.core.types import Message, ToolResultBlock, ToolUseBlock
from openagent.runtime.clearing import CLEARED_PREFIX, clear_old_tool_outputs
from openagent.runtime.runner import AgentRunner
from openagent.tools.recall import recall_output
from tests.support.streaming_provider import ScriptedStreamProvider, text_turn


def tool_run(steps: int = 20, output: str = "line of output\n" * 150, name: str = "bash") -> list[Message]:
    session = Session()
    session.add_message(Message(role="user", content="fix the failing test"))
    for i in range(steps):
        session.add_message(
            Message(role="assistant", content=[ToolUseBlock(id=f"call_{i}", name=name, arguments={"cmd": f"step {i}"})])
        )
        session.add_tool_results([ToolResultBlock(tool_use_id=f"call_{i}", tool_name=name, content=f"result {i}\n{output}")])
    return session.messages


def results(messages: list[Message]) -> list[ToolResultBlock]:
    return [b for m in messages if isinstance(m.content, list) for b in m.content if isinstance(b, ToolResultBlock)]


def test_old_outputs_are_cleared_oldest_first_until_the_target():
    messages = tool_run()
    clearing = clear_old_tool_outputs(messages, target_tokens=3000, keep_tokens=500)

    blocks = results(clearing.messages)
    cleared = [b.content.startswith(CLEARED_PREFIX) for b in blocks]
    assert clearing.tokens_after <= 3000 < clearing.tokens_before
    # Oldest first: everything cleared comes before everything kept.
    assert cleared == sorted(cleared, reverse=True)
    assert cleared[0] and not cleared[-1]


def test_the_original_history_is_not_changed():
    messages = tool_run()
    clear_old_tool_outputs(messages, target_tokens=1000, keep_tokens=500)
    assert not any(b.content.startswith(CLEARED_PREFIX) for b in results(messages))


def test_the_last_three_steps_are_never_cleared():
    messages = tool_run()
    clearing = clear_old_tool_outputs(messages, target_tokens=1, keep_tokens=0)
    assert [b.content.startswith(CLEARED_PREFIX) for b in results(clearing.messages)][-3:] == [False] * 3


def test_small_results_and_state_tools_are_kept():
    small = clear_old_tool_outputs(tool_run(output="ok"), target_tokens=1, keep_tokens=0)
    todos = clear_old_tool_outputs(tool_run(name="todo_list"), target_tokens=1, keep_tokens=0)
    assert small.cleared == 0
    assert todos.cleared == 0


def test_the_placeholder_says_what_ran_and_how_to_get_it_back():
    clearing = clear_old_tool_outputs(tool_run(), target_tokens=3000, keep_tokens=500, outputs_dir="/tmp/x")
    placeholder = results(clearing.messages)[0].content
    assert 'bash {"cmd": "step 0"}' in placeholder
    assert 'first line: "result 0"' in placeholder
    assert 'recall_output("call_0")' in placeholder


def test_large_old_tool_inputs_are_shortened():
    session = Session()
    session.add_message(Message(role="user", content="write it"))
    body = "def f():\n    return 1\n" * 400
    session.add_message(Message(role="assistant", content=[ToolUseBlock(id="w1", name="write", arguments={"path": "a.py", "content": body})]))
    session.add_tool_results([ToolResultBlock(tool_use_id="w1", tool_name="write", content="saved")])
    for message in tool_run(steps=4, output="x\n"):
        session.add_message(message)

    clearing = clear_old_tool_outputs(session.messages, target_tokens=1, keep_tokens=0, outputs_dir="/tmp/x")

    call = clearing.messages[1].content[0]
    assert call.arguments["path"] == "a.py"
    assert 'recall_output("w1.content")' in call.arguments["content"]
    assert ("w1.content", body) in clearing.saves


def test_a_cleared_output_can_be_recalled(tmp_path):
    clearing = clear_old_tool_outputs(tool_run(), target_tokens=3000, keep_tokens=500, outputs_dir=tmp_path)
    clearing.write(tmp_path)
    context = ToolContext(outputs_dir=str(tmp_path))

    whole = recall_output("call_0", max_lines=1000, tool_context=context)
    assert whole.startswith("result 0\nline of output")
    part = recall_output("call_0", start_line=2, max_lines=2, tool_context=context)
    assert part.startswith("line of output\nline of output\n[lines 2-3 of")


def test_recall_refuses_what_is_not_there(tmp_path):
    context = ToolContext(outputs_dir=str(tmp_path))
    assert recall_output("nope", tool_context=context).startswith("Error")
    assert recall_output("../../etc/passwd", tool_context=context).startswith("Error")
    assert recall_output("call_0", tool_context=ToolContext()).startswith("Error")


async def test_the_runner_clears_instead_of_summarising_when_that_is_enough(tmp_path):
    session = Session()
    for message in tool_run(steps=30):
        session.add_message(message)

    class NoSummary(ScriptedStreamProvider):
        async def chat(self, *args, **kwargs):
            raise AssertionError("clearing was enough; no summary call")

    runner = AgentRunner(
        NoSummary([text_turn("done")]),
        session=session,
        context_window=12000,
        tool_context=ToolContext(outputs_dir=str(tmp_path)),
    )
    events = [e async for e in runner.stream("go on")]

    done = [e for e in events if e.type == "context_compaction_completed"]
    assert done and done[0].reason.startswith("cleared ")
    assert any(tmp_path.iterdir())
    assert events[-1].type == "run_completed"


async def test_when_clearing_is_not_enough_the_summary_sees_the_original_outputs():
    session = Session()
    for message in tool_run(steps=30):
        session.add_message(message)
    # The person's own long messages: clearing tool output cannot fit these.
    for _ in range(3):
        session.add_message(Message(role="user", content="background " * 3000))
        session.add_message(Message(role="assistant", content="noted"))

    seen: list[list[Message]] = []

    class Summariser(ScriptedStreamProvider):
        async def chat(self, messages, *args, **kwargs):
            seen.append(list(messages))
            return Message(role="assistant", content="the summary")

    runner = AgentRunner(Summariser([text_turn("done")]), session=session, context_window=12000)
    events = [e async for e in runner.stream("go on")]

    assert seen, "the summary ran"
    assert not any(b.content.startswith(CLEARED_PREFIX) for b in results(seen[0]))
    assert [e.type for e in events].count("context_compaction_started") == 1
