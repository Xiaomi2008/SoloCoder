"""A Worker's headless page, in real Chromium: it runs a game and stays in its directory."""
import json
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from openagent.apps.superagent.domain import Policy
from openagent.apps.superagent.page_check import PageCheck
from openagent.apps.superagent.storage import DurableBoard, StateStore, attach_store
from openagent.apps.superagent.worker import SuperAgentWorker, turn_warning
from openagent.core.types import Message, TextBlock, ToolUseBlock

GAME = """<!doctype html><title>Game</title><canvas id=c width=200 height=100></canvas><script>
let x = 0, jumps = 0;
addEventListener('keydown', e => { if (e.key === 'ArrowRight') x += 10; if (e.key === ' ') jumps++; });
</script>"""


@pytest.fixture
async def check(tmp_path):
    work = tmp_path / "ticket"
    work.mkdir()
    (work / "index.html").write_text(GAME)
    page = PageCheck(work)
    try:
        await page._start()
    except Exception as exc:
        await page.close()
        pytest.skip(f"no Chromium: {exc}")
    tools = {t._tool_name: t for t in page.tools()}
    yield page, tools, work
    await page.close()


async def test_keys_reach_the_game_and_state_can_be_read(check):
    _, tools, _ = check
    assert "title 'Game'" in await tools["page_open"]("index.html")
    assert "No page errors" in await tools["page_run"]("press ArrowRight; press ArrowRight; wait 10; press Space")
    assert json.loads((await tools["page_eval"]("({x, jumps})")).splitlines()[0]) == {"x": 20, "jumps": 1}


async def test_a_screenshot_is_held_for_this_worker(check):
    page, tools, _ = check
    await tools["page_open"]("index.html")
    assert (await tools["page_screenshot"]()).startswith("Screenshot captured")
    assert page.capture


async def test_files_outside_the_directory_are_refused(check, tmp_path):
    _, tools, work = check
    (tmp_path / "secret.html").write_text("secret")
    assert "outside" in await tools["page_open"]("../secret.html")
    (work / "link.html").symlink_to(tmp_path / "secret.html")
    assert "outside" in await tools["page_open"]("link.html")


async def test_the_page_cannot_load_outside_its_directory_or_the_network(check, tmp_path):
    _, tools, work = check
    (tmp_path / "secret.txt").write_text("secret")
    (work / "probe.html").write_text(
        f"""<!doctype html><title>probe</title><script>
        window.result = {{}};
        for (const [k, u] of [['file', 'file://{tmp_path}/secret.txt'], ['web', 'http://example.com/x.png']]) {{
          const i = new Image(); i.onload = () => result[k] = 'loaded'; i.onerror = () => result[k] = 'blocked'; i.src = u;
        }}</script>"""
    )
    out = await tools["page_open"]("probe.html")
    assert "blocked request" in out
    await tools["page_run"]("wait 300")
    assert json.loads((await tools["page_eval"]("result")).splitlines()[0]) == {"file": "blocked", "web": "blocked"}


async def test_page_errors_are_reported(check):
    _, tools, work = check
    (work / "bad.html").write_text("<script>nope();</script>")
    assert "nope" in await tools["page_open"]("bad.html")


async def test_a_hung_page_is_an_error_not_a_hang(check, monkeypatch):
    _, tools, work = check
    monkeypatch.setattr("openagent.apps.superagent.page_check.STEP_TIMEOUT_S", 1)
    await tools["page_open"]("index.html")
    out = await check[0]._step("hang", lambda: __import__("asyncio").sleep(5), timeout=0.1)
    assert out.startswith("Error: no answer")


async def test_bad_actions_are_refused_before_anything_runs(check):
    _, tools, _ = check
    assert "unknown action" in await tools["page_run"]("explode now")
    assert "at most" in await tools["page_run"]("wait 999999")


async def test_a_held_key_is_reported_and_released_by_opening_a_page(check):
    _, tools, work = check
    (work / "keys.html").write_text(
        "<!doctype html><title>Keys</title><script>let ups = 0;"
        "addEventListener('keyup', () => ups++);</script>")
    await tools["page_open"]("keys.html")
    assert "Still held: ArrowRight" in await tools["page_run"]("down ArrowRight")
    assert check[0]._held == {"ArrowRight"}
    await tools["page_open"]("keys.html")
    assert check[0]._held == set()
    # Without the release this reads as a repeat of a key still down.
    await tools["page_eval"]("window.seen = []; addEventListener('keydown', e => seen.push(e.repeat))")
    await tools["page_run"]("press ArrowRight")
    assert json.loads((await tools["page_eval"]("seen")).splitlines()[0]) == [False]


async def test_acting_before_opening_a_page_says_so(tmp_path):
    tools = {t._tool_name: t for t in PageCheck(tmp_path).tools()}
    assert "open a page" in await tools["page_run"]("press Space")
    assert "open a page" in await tools["page_eval"]("1")
    assert "open a page" in await tools["page_screenshot"]()


def _worker(tmp_path, **options):
    board = DurableBoard(workspace=str(tmp_path), policy=Policy())
    attach_store(board, StateStore(str(tmp_path)))
    trigger = board.submit("Make a game", actor="owner")
    board.next_batch()
    board.route(kind="accept", trigger_ids=trigger.id, title="Game", worktree_name="game", acknowledgement="Making it.")
    ticket_id = next(iter(board.tickets))
    return SuperAgentWorker(object(), board, ticket_id, **options)


def _names(worker):
    return {definition.name for definition in worker._agent.tool_registry.definitions}


def test_a_worker_has_neither_tool_set_unless_the_operator_asks(tmp_path):
    names = _names(_worker(tmp_path))
    assert not {n for n in names if n.startswith(("page_", "desktop_"))}


def test_the_flags_add_the_tools(tmp_path):
    names = _names(_worker(tmp_path, page_check=True, desktop_apps=("Calculator",)))
    assert {"page_open", "page_run", "page_eval", "page_screenshot", "desktop_snapshot"} <= names
    assert "desktop_list_apps" not in names


class _Busy:
    """A model that keeps calling a tool until it reads a limit warning, or forever."""

    def __init__(self, obeys: bool):
        self.obeys = obeys
        self.warned_at: list[int] = []
        self.turns = 0

    async def chat(self, messages, tools=None, system_prompt=None, **kwargs):
        self.turns += 1
        last = messages[-1].text or ""
        if "turns left" in last:
            self.warned_at.append(self.turns)
            if self.obeys:
                return Message(role="assistant", content=[
                    ToolUseBlock(name="report", arguments={"kind": "blocked", "summary": "Half done; jump untested."})])
        if self.obeys and self.warned_at and self.warned_at[-1] < self.turns:
            return Message(role="assistant", content=[TextBlock(text="Reported.")])
        return Message(role="assistant", content=[ToolUseBlock(name="glob", arguments={"pattern": "*"})])


def _busy_worker(tmp_path, obeys):
    worker = _worker(tmp_path, max_turns=6)
    model = _Busy(obeys)
    worker._agent.provider = model
    return worker, model, worker.board.tickets[worker.ticket_id]


async def test_a_worker_warned_of_its_limit_reports_in_time(tmp_path):
    worker, model, ticket = _busy_worker(tmp_path, obeys=True)
    await worker.run(ticket.inputs)
    assert [r.summary for r in ticket.reports] == ["Half done; jump untested."]
    assert model.warned_at == [4]


async def test_a_worker_that_ignores_the_warnings_is_reported_for_what_happened(tmp_path):
    worker, model, ticket = _busy_worker(tmp_path, obeys=False)
    await worker.run(ticket.inputs)
    assert model.warned_at == [4, 6, 7]
    assert len(ticket.reports) == 1 and "Used all 6 turns" in ticket.reports[0].summary


def test_the_warning_names_the_turns_left():
    assert "3 turns left" in turn_warning(3) and "0 turns left" in turn_warning(-1)


def test_a_blocked_notice_states_what_is_in_the_ticket_directory(tmp_path):
    worker = _worker(tmp_path)
    board, ticket_id = worker.board, worker.ticket_id
    Path(board.tickets[ticket_id].directory, "index.html").write_text("abc")
    board.report(ticket_id, "blocked", "Stopped.")
    assert "index.html (3 bytes)" in board.notices[-1].text
    assert "index.html" not in board.tickets[ticket_id].reports[-1].summary


def test_a_blocked_notice_says_when_the_directory_is_empty(tmp_path):
    worker = _worker(tmp_path)
    worker.board.report(worker.ticket_id, "blocked", "Stopped.")
    assert "empty ticket directory" in worker.board.notices[-1].text


def test_old_page_screenshots_are_dropped_even_with_a_warning_attached(tmp_path):
    worker = _worker(tmp_path, max_turns=4)
    agent = worker._agent
    agent.page = PageCheck(tmp_path)
    result = type("R", (), {"tool_name": "page_screenshot", "is_error": False, "content": "Screenshot captured."})()
    for _ in range(5):
        agent.session.add("assistant", "turn")
        agent.completed_turns += 1
        agent.page.capture = "aW1n"
        agent._add_tool_result_followup_messages([result])
    images = [m for m in agent.session.messages if isinstance(m.content, list)
              and any(type(b).__name__ == "ImageBlock" for b in m.content)]
    assert len(images) == 3
    assert "turns left" in images[-1].text


class _Compacting(_Busy):
    """Keeps calling a tool, and answers a compaction's request with a summary."""

    def __init__(self):
        super().__init__(obeys=False)
        self.summaries = 0

    async def chat(self, messages, tools=None, system_prompt=None, **kwargs):
        if "Reply with the summary" in (messages[-1].text or ""):
            self.summaries += 1
            return Message(role="assistant", content=[TextBlock(text="Wrote the game; jump untested.")])
        return await super().chat(messages, tools=tools, system_prompt=system_prompt, **kwargs)


async def test_a_worker_compacts_and_keeps_its_card_and_its_turn_count(tmp_path):
    worker, _, ticket = _busy_worker(tmp_path, obeys=False)
    model = _Compacting()
    worker._agent.provider = model
    # Small enough that history is over the limit from the first tool result.
    worker._agent.loop_defaults = {"max_context_tokens": 60}
    await worker.run(ticket.inputs)
    messages = worker._agent.session.messages
    assert model.summaries > 0 and "Conversation summary" in messages[0].text
    assert sum(m.text.startswith(f"Ticket {ticket.id}") for m in messages) == 1
    # A summary is not a turn, and rewriting the history does not refund any.
    assert model.turns == 7 and worker._agent.turns_left == -1
    assert "Used all 6 turns" in ticket.reports[-1].summary
    assert model.warned_at == [4, 6, 7]


async def test_a_compacted_session_survives_a_restart(tmp_path):
    worker, _, ticket = _busy_worker(tmp_path, obeys=False)
    worker._agent.provider = _Compacting()
    worker._agent.loop_defaults = {"max_context_tokens": 60}
    await worker.run(ticket.inputs)
    fresh = SuperAgentWorker(object(), worker.board, worker.ticket_id, max_turns=6)
    assert fresh._agent.session.messages[0].text == worker._agent.session.messages[0].text
    assert len(fresh._agent.session.messages) == len(worker._agent.session.messages)


class _Reports:
    """Files the given reports in turn, then would keep filing the last forever."""

    def __init__(self, *reports):
        self.reports = list(reports)
        self.turns = 0

    async def chat(self, messages, tools=None, system_prompt=None, **kwargs):
        arguments = self.reports[min(self.turns, len(self.reports) - 1)]
        self.turns += 1
        return Message(role="assistant", content=[ToolUseBlock(name="report", arguments=arguments)])


@pytest.mark.parametrize("final", [
    {"kind": "ready", "summary": "Done."},
    {"kind": "blocked", "summary": "Stuck."},
    {"kind": "failed", "summary": "Broke."},
    {"kind": "waiting", "summary": "Nothing to do."},
    {"kind": "progress", "summary": "Half way.", "question": "Which level?"},
])
async def test_a_report_that_ends_the_turn_ends_the_assignment(tmp_path, final):
    worker = _worker(tmp_path, max_turns=6)
    worker._agent.provider = model = _Reports(final)
    ticket = worker.board.tickets[worker.ticket_id]
    await worker.run(ticket.inputs)
    assert model.turns == 1 and len(ticket.reports) == 1


async def test_a_progress_report_does_not_end_the_assignment(tmp_path):
    worker = _worker(tmp_path, max_turns=6)
    worker._agent.provider = model = _Reports({"kind": "progress", "summary": "Half way."},
                                              {"kind": "ready", "summary": "Done."})
    ticket = worker.board.tickets[worker.ticket_id]
    await worker.run(ticket.inputs)
    assert model.turns == 2 and [r.kind.value for r in ticket.reports] == ["progress", "ready"]


async def test_an_earlier_attempts_report_does_not_end_this_one(tmp_path):
    worker = _worker(tmp_path, max_turns=6)
    ticket = worker.board.tickets[worker.ticket_id]
    worker.board.report(worker.ticket_id, "blocked", "From the last attempt.")
    worker._agent.provider = model = _Reports({"kind": "progress", "summary": "Going."},
                                              {"kind": "ready", "summary": "Done."})
    await worker.run(ticket.inputs)
    assert model.turns == 2
