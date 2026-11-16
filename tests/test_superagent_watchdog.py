"""The Watchdog: what it measures, what it may read, and that it cannot change a job."""
import json
import time

import pytest

from openagent.apps.superagent.domain import AssignmentState, Policy
from openagent.apps.superagent.storage import DurableBoard, StateStore, attach_store
from openagent.apps.superagent.watchdog import Watchdog, activity, load_board, measure, since_assignment
from openagent.core.types import Message, TextBlock, ToolUseBlock

CARD = {"role": "user", "content": "Ticket ticket-1 -- Game\nDirectory: /x\nScope: game"}


def turn(name="page_eval", **arguments):
    return [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": name, "arguments": arguments}]},
        {"role": "tool_result", "content": [{"type": "tool_result", "tool_use_id": "1", "tool_name": name,
                                             "content": "ok", "is_error": False}]},
    ]


@pytest.fixture
def job(tmp_path):
    board = DurableBoard(workspace=str(tmp_path), policy=Policy())
    store = StateStore(str(tmp_path))
    attach_store(board, store)
    trigger = board.submit("Make a game", actor="owner")
    board.next_batch()
    board.route(kind="accept", trigger_ids=trigger.id, title="Game", worktree_name="game", acknowledgement="Making it.")
    ticket = next(iter(board.tickets.values()))
    from pathlib import Path
    Path(ticket.directory).mkdir(parents=True, exist_ok=True)
    (Path(ticket.directory) / "index.html").write_text("<canvas>")
    assignment = board.start_assignment(ticket.id)
    board.heartbeat(ticket.id, "Working")
    store.save(board)
    (tmp_path / "sessions").mkdir(exist_ok=True)
    return tmp_path, board, ticket, assignment, store


def save_session(workspace, ticket, messages):
    (workspace / "sessions" / f"{ticket.id}.json").write_text(json.dumps({"messages": messages}))


class Model:
    """Plays the given tool calls, then would call the last one forever."""

    def __init__(self, *calls):
        self.calls = list(calls)
        self.turns = 0
        self.prompts = []

    async def chat(self, messages, tools=None, system_prompt=None, **kwargs):
        self.prompts.append(messages[-1].text)
        name, arguments = self.calls[min(self.turns, len(self.calls) - 1)]
        self.turns += 1
        return Message(role="assistant", content=[ToolUseBlock(name=name, arguments=arguments)])


def test_it_counts_repeats_turns_and_errors_for_the_latest_attempt_only(job):
    _, _, ticket, *_ = job
    old = [{"role": "user", "content": "Ticket t\nDirectory: /old"}, *turn("write", path="old")]
    now = [CARD, *turn(x=1), *turn(y=2), *turn(y=2), *turn(y=2)]
    facts = measure(ticket, old + now)
    assert facts["turns_this_attempt"] == 4
    assert facts["tools_this_attempt"] == {"page_eval": 4}
    assert facts["same_call_in_a_row_now"] == 3 and facts["most_repeated_call"] == 3
    assert facts["running"] and facts["heartbeat_s_ago"] is not None
    assert facts["files"][0]["path"] == "index.html"


def test_a_stale_heartbeat_can_be_seen(job):
    _, _, ticket, assignment, _ = job
    assert measure(ticket, [CARD], now=assignment.last_heartbeat + 900)["heartbeat_s_ago"] == 900


def test_activity_is_cut_short_and_shows_the_latest_turns():
    messages = [CARD, *turn(a="x" * 5000), *turn(b=1), *turn(c=2)]
    shown = activity(messages, last=2)
    assert "[turn 2]" in shown and "[turn 3]" in shown and "[turn 1]" not in shown
    assert "chars]" in activity(messages, last=3)
    assert len(since_assignment(messages)) == len(messages)


def test_reading_a_running_job_does_not_file_a_restart_report(job):
    workspace, *_ = job
    board = load_board(workspace)
    ticket = next(iter(board.tickets.values()))
    assert ticket.assignments[-1].state is AssignmentState.RUNNING
    assert not ticket.reports


async def test_a_check_records_a_verdict_and_changes_nothing_in_the_job(job):
    workspace, _, ticket, _, _ = job
    save_session(workspace, ticket, [CARD, *turn(x=1)])
    before = {name: (workspace / name).read_bytes() for name in ("state.json", f"sessions/{ticket.id}.json")}
    model = Model(("read_activity", {"ticket_id": ticket.id}),
                  ("verdict", {"ticket_id": ticket.id, "state": "looping", "reason": "Same test.", "evidence": "turn 1"}))
    rows = await Watchdog(model, workspace).check()
    assert [r["state"] for r in rows] == ["looping"]
    assert model.turns == 2
    assert before == {name: (workspace / name).read_bytes() for name in before}
    assert json.loads((workspace / "watchdog.jsonl").read_text())["state"] == "looping"


async def test_it_has_no_tool_that_can_change_anything(job):
    workspace, board, ticket, *_ = job
    tools = Watchdog(Model(), workspace)._tools(board, {ticket.id: {}}, {})
    assert {t._tool_name for t in tools} == {"read_activity", "read_ticket_file", "verdict"}


async def test_a_bad_verdict_is_refused_and_the_model_can_try_again(job):
    workspace, _, ticket, _, _ = job
    save_session(workspace, ticket, [CARD])
    good = {"ticket_id": ticket.id, "state": "stuck", "reason": "r", "evidence": "e"}
    model = Model(("verdict", {**good, "state": "fine"}), ("verdict", {**good, "evidence": " "}), ("verdict", good))
    assert [r["state"] for r in await Watchdog(model, workspace).check()] == ["stuck"]
    assert model.turns == 3


async def test_no_verdict_is_recorded_as_unclear_not_as_healthy(job):
    workspace, _, ticket, _, _ = job
    save_session(workspace, ticket, [CARD])
    model = Model(("read_activity", {"ticket_id": ticket.id}))
    rows = await Watchdog(model, workspace, max_turns_each=2).check()
    assert rows[0]["state"] == "unclear" and "without a verdict" in rows[0]["reason"]


async def test_files_outside_the_ticket_and_other_tickets_are_refused(job):
    workspace, board, ticket, *_ = job
    (workspace / "secret.txt").write_text("secret")
    tools = {t._tool_name: t for t in Watchdog(Model(), workspace)._tools(board, {ticket.id: {}}, {})}
    assert "Error" in tools["read_ticket_file"](ticket.id, "../../secret.txt")
    assert "Error" in tools["read_ticket_file"]("ticket-999", "index.html")
    assert "Error" in tools["read_activity"]("ticket-999")
    assert tools["read_ticket_file"](ticket.id, "index.html") == "<canvas>"


async def test_a_quiet_workspace_costs_no_model_call(job):
    workspace, board, ticket, assignment, store = job
    board.finish_assignment(assignment.id)
    store.save(board)
    model = Model()
    assert await Watchdog(model, workspace).check() == []
    assert model.turns == 0


async def test_earlier_verdicts_reach_the_next_check(job):
    workspace, _, ticket, _, _ = job
    save_session(workspace, ticket, [CARD])
    good = ("verdict", {"ticket_id": ticket.id, "state": "looping", "reason": "first", "evidence": "e"})
    await Watchdog(Model(good), workspace).check()
    model = Model(good)
    await Watchdog(model, workspace).check()
    assert '"reason": "first"' in model.prompts[0]


async def test_a_named_ticket_is_checked_even_when_nothing_runs_on_it(job):
    workspace, board, ticket, assignment, store = job
    board.finish_assignment(assignment.id)
    store.save(board)
    save_session(workspace, ticket, [CARD])
    model = Model(("verdict", {"ticket_id": ticket.id, "state": "unclear", "reason": "r", "evidence": "e"}))
    assert len(await Watchdog(model, workspace).check([ticket.id])) == 1
