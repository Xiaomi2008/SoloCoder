"""Tests for the super-agent app: the domain alone, then the loop with a script.

The domain is model-free on purpose. Everything the host decides -- what a
Trigger means, which Ticket it becomes, when a Ticket ends -- is a Board method,
so it is testable without a provider. The two end-to-end tests drive the real
router/worker Agents with a scripted provider, so the prompt/tool wiring is
exercised too, but no network and no model are involved.

Ids are never hardcoded here: the Board mints them from a single monotonic
counter, so every test reads the id it needs off the object the Board returned.
"""

from __future__ import annotations

import pytest

from openagent.apps.superagent import (
    Board,
    DecisionKind,
    Mandate,
    Policy,
    ReportKind,
    Strength,
    SuperAgentRouter,
    SuperAgentRuntime,
    compute_stage,
)
from openagent.core.types import Message, TextBlock, ToolUseBlock


class ScriptedProvider:
    """Answers from a fixed queue of Messages, one per model call.

    Duck-typed like conftest's MockProvider: Agent only calls ``chat``. A
    response that carries a ToolUseBlock is a tool turn; one with plain text
    ends the Agent loop.
    """

    def __init__(self, responses: list[Message]) -> None:
        self._responses = list(responses)
        self.model = "scripted"
        self.api_key = None
        self.calls = 0

    async def chat(self, messages, tools=None, system_prompt="", **kwargs) -> Message:
        if not self._responses:
            raise AssertionError("scripted provider ran out of responses")
        self.calls += 1
        return self._responses.pop(0)


def call(name: str, **arguments) -> Message:
    """One assistant turn whose only content is a tool call."""
    return Message(role="assistant", content=[ToolUseBlock(name=name, arguments=arguments)])


def said(text: str) -> Message:
    """One assistant turn with no tool call: this ends the loop."""
    return Message(role="assistant", content=[TextBlock(text)])


# -- the domain: no model --------------------------------------------------


def make_board(tmp_path, **policy_kwargs) -> Board:
    return Board(workspace=str(tmp_path), policy=Policy(**policy_kwargs))


def submit_and_trigger(board: Board, text: str, **kwargs):
    """Submit one message and return (thread, trigger) without running a model."""
    trigger = board.submit(text, **kwargs)
    return board.threads[trigger.thread_id], trigger


def test_explicit_accept_creates_a_ticket(tmp_path):
    board = make_board(tmp_path)
    thread, trigger = submit_and_trigger(board, "fix the parser bug", actor="owner")

    result = board.route(
        kind="accept",
        trigger_ids=trigger.id,
        title="Fix the parser bug",
        worktree_name="parser-bug",
        acknowledgement="I will fix the parser bug and verify it.",
    )

    assert "ticket-" in result
    (ticket,) = board.tickets.values()
    assert ticket.short_name == "parser-bug"
    assert ticket.mandate.strength is Strength.EXPLICIT
    assert ticket.mandate.thread_id == thread.id
    assert ticket.directory.endswith("parser-bug")
    assert not ticket.ended and not ticket.stopped
    # Accepted but not yet launched: no Assignment has been started.
    assert str(compute_stage(ticket)) == "working/Queued"
    # The trigger is now an Input for the Ticket, waiting for a Worker.
    assert [i.trigger_id for i in board.take_inputs(ticket.id)] == [trigger.id]
    assert board.next_launch() == ticket.id


def test_ready_without_pr_requires_host_verification(tmp_path):
    board = make_board(tmp_path)
    _, trigger = submit_and_trigger(board, "fix the parser bug", actor="owner")
    board.route(kind="accept", trigger_ids=trigger.id, title="Fix it", worktree_name="fix-it")
    (ticket,) = board.tickets.values()

    board.take_inputs(ticket.id)
    board.report(ticket.id, "ready", "Fixed and tested.")
    assert not ticket.ended
    assert ticket.verification == "pending"
    board.finish_verification(ticket.id, "passed", ["Host check passed"] )

    assert ticket.ended and ticket.outcome == "completed"
    assert str(compute_stage(ticket)) == "ended/Ended - completed"
    assert board.next_launch() is None


def test_ready_with_pr_awaits_review(tmp_path):
    board = make_board(tmp_path)
    _, trigger = submit_and_trigger(board, "fix the parser bug", actor="owner")
    board.route(kind="accept", trigger_ids=trigger.id, title="Fix it", worktree_name="fix-it")
    (ticket,) = board.tickets.values()

    board.take_inputs(ticket.id)
    board.report(ticket.id, "ready", "Opened a PR.", pr="https://example.test/pull/1")

    assert not ticket.ended
    assert ticket.pr == "https://example.test/pull/1"
    assert str(compute_stage(ticket)) == "working/Awaiting verification"
    board.finish_verification(ticket.id, "passed", ["Host check passed"])
    assert str(compute_stage(ticket)) == "person/Awaiting review"


def test_a_person_message_resumes_an_ended_ticket(tmp_path):
    board = make_board(tmp_path)
    thread, trigger = submit_and_trigger(board, "fix the parser bug", actor="owner")
    board.route(kind="accept", trigger_ids=trigger.id, title="Fix it", worktree_name="fix-it")
    (ticket,) = board.tickets.values()
    board.take_inputs(ticket.id)
    board.report(ticket.id, "ready", "Fixed and tested.")
    board.finish_verification(ticket.id, "passed", ["Host check passed"])
    assert ticket.ended

    resumed = board.submit("thanks, one more thing", actor="owner", thread_id=thread.id)

    # No new Eval and no new Decision: the Subscription delivered it and the
    # Mandate was revalidated against today's Policy.
    assert not ticket.ended
    assert [i.trigger_id for i in board.take_inputs(ticket.id)] == [resumed.id]
    assert board.next_launch() == ticket.id
    assert len(board.decisions) == 1


def test_a_stopped_ticket_receives_nothing(tmp_path):
    board = make_board(tmp_path)
    thread, trigger = submit_and_trigger(board, "fix the parser bug", actor="owner")
    board.route(kind="accept", trigger_ids=trigger.id, title="Fix it", worktree_name="fix-it")
    (ticket,) = board.tickets.values()
    board.take_inputs(ticket.id)

    assert "Stopped" in board.stop(ticket.id)
    assert ticket.stopped
    assert "already stopped" in board.stop(ticket.id)

    board.submit("one more thing", actor="owner", thread_id=thread.id)

    # The message reached the Board but not the stopped Ticket.
    assert board.take_inputs(ticket.id) == []
    # Both triggers are still pending: only next_batch() clears a Thread's
    # pending list, and these domain tests never start the loop.
    assert len(thread.pending) == 2


def test_observe_mode_records_would_accept(tmp_path):
    board = make_board(tmp_path)  # default accept mode is observe
    _, trigger = submit_and_trigger(board, "the docs look stale", actor="stranger")

    result = board.route(kind="accept", trigger_ids=trigger.id, title="Update docs")

    assert "would accept" in result
    assert board.tickets == {}
    (decision,) = board.decisions
    assert decision.would_accept
    assert decision.strength is Strength.INFERRED
    # The Decision still says "accept": observe mode changes the effect, not the
    # recorded intent. A person reviewing the log must see what would have happened.
    assert decision.kind is DecisionKind.ACCEPT


def test_auto_mode_accepts_an_inferred_candidate(tmp_path):
    board = make_board(tmp_path, accept_mode="auto")
    _, trigger = submit_and_trigger(board, "the docs look stale", actor="stranger")

    board.route(kind="accept", trigger_ids=trigger.id, title="Update docs", worktree_name="docs")

    (ticket,) = board.tickets.values()
    assert ticket.mandate.strength is Strength.INFERRED
    assert ticket.mandate.scope == "inferred candidate"


def test_a_mandate_does_not_outlive_the_policy(tmp_path):
    board = make_board(tmp_path)
    _, trigger = submit_and_trigger(board, "fix the parser bug", actor="owner")
    board.route(kind="accept", trigger_ids=trigger.id, title="Fix it", worktree_name="fix-it")
    (ticket,) = board.tickets.values()

    assert ticket.mandate.still_valid(board.policy)
    # A person removed from the allowed list cannot keep authorizing work.
    board.policy = Policy(allowed=frozenset({"someone-else"}))
    assert not ticket.mandate.still_valid(board.policy)


def test_an_inferred_mandate_needs_auto_mode():
    mandate = Mandate(1, Strength.INFERRED, "inferred candidate", "cli", "thread-1", "bot", ())
    assert mandate.still_valid(Policy(accept_mode="auto"))
    assert not mandate.still_valid(Policy(accept_mode="observe"))


def test_multi_ticket_followup_waits_for_selection(tmp_path):
    board = make_board(tmp_path)
    thread, trigger = submit_and_trigger(board, "fix the parser bug", actor="owner")
    second = board.submit("also update the docs", actor="owner", thread_id=thread.id)
    board.next_batch()
    board.route(kind="accept", trigger_ids=trigger.id, title="Fix it", worktree_name="fix-it")
    board.route(kind="accept", trigger_ids=second.id, title="Update docs", worktree_name="docs")
    assert len(board.tickets) == 2

    third = board.submit("both of these matter", actor="owner", thread_id=thread.id)

    assert not any(i.trigger_id == third.id for ticket in board.tickets.values() for i in ticket.inputs)
    assert [t.id for t in thread.pending] == [third.id]


def test_a_reply_is_bounded_to_a_hundred_words(tmp_path):
    board = make_board(tmp_path)
    assert board.reply(purpose="notice", text="one two three").startswith("Delivered")
    assert "truncated" in board.reply(purpose="notice", text="word " * 200)
    assert "Error" in board.reply(purpose="attention", text="look?")


# -- the loop: router and worker with a script ------------------------------------


@pytest.mark.asyncio
async def test_the_loop_accepts_works_and_reports(tmp_path):
    """One message in, one Ticket out, one report relayed back to the person."""
    board = Board(workspace=str(tmp_path), policy=Policy())
    trigger = board.submit("add a --verbose flag to the parser", actor="owner")
    provider = ScriptedProvider([
        # router: accept the candidate
        call(
            "route",
            kind="accept",
            trigger_ids=trigger.id,
            title="Add a --verbose flag",
            worktree_name="verbose-flag",
            acknowledgement="I will add a --verbose flag to the parser and verify it.",
        ),
        # worker: do the work and report it ready
        call("report", kind="ready", summary="Added --verbose; tests pass."),
        # router: relay the result to the person
        call("reply", purpose="result", text="The --verbose flag is in."),
    ])
    runtime = SuperAgentRuntime(board=board, router=SuperAgentRouter(provider=provider, board=board),
                                verify_commands=["true"])

    replies = await runtime.run_pending()

    (ticket,) = board.tickets.values()
    assert ticket.title == "Add a --verbose flag"
    assert ticket.ended and ticket.outcome == "completed"
    assert len(ticket.reports) == 1
    assert ticket.reports[0].kind is ReportKind.READY
    assert ticket.assignments[0].state.value == "ended"
    # The acceptance produced an ack carrying the real Ticket id; the router's
    # result reply is bound to the sole pending report when its id is omitted.
    assert [r.purpose for r in replies] == ["ack", "result"]
    assert replies[0].ticket_id == ticket.id
    # Router and worker both stop on the tool batch that settles their work.
    assert provider.calls == 3
    assert ticket.directory.startswith(str(tmp_path))
    assert (tmp_path / "work" / "verbose-flag").is_dir()


@pytest.mark.asyncio
async def test_a_batch_arriving_together_is_one_router_turn(tmp_path):
    """Three messages on one Thread are assessed once, not three times."""
    board = Board(workspace=str(tmp_path), policy=Policy())
    first = board.submit("message 1", actor="owner")
    thread = board.threads[first.thread_id]
    board.submit("message 2", actor="owner", thread_id=thread.id)
    board.submit("message 3", actor="owner", thread_id=thread.id)
    ids = [t.id for t in thread.pending]
    assert len(ids) == 3
    provider = ScriptedProvider([
        call("route", kind="ignore", trigger_ids=", ".join(ids), reason="noise"),
    ])
    runtime = SuperAgentRuntime(board=board, router=SuperAgentRouter(provider=provider, board=board))

    await runtime.run_pending()

    assert provider.calls == 1  # no unnecessary closing inference
    assert board.tickets == {}
    # One batched Decision covers all three candidates. If the loop assessed them
    # one at a time this would be three Decisions and three router turns.
    assert len(board.decisions) == 1
    (decision,) = board.decisions
    assert decision.trigger_ids == tuple(ids)
    assert decision.kind is DecisionKind.IGNORE


@pytest.mark.parametrize(
    ('arguments', 'expected_provider', 'expected_model', 'expected_url'),
    [([], 'lmstudio', 'qwen3.5-35b-a3b', 'http://localhost:1234/v1'),
     (['--provider', 'openai_oauth', '--model', 'gpt-6.1-terra'],
      'openai_oauth', 'gpt-6.1-terra', None),
     (['--provider', 'taopc', '--model', 'deepseek-v4.1-x'],
      'taopc', 'deepseek-v4.1-x', None)],
)
async def test_cli_build_uses_registry(tmp_path, monkeypatch, arguments,
                                       expected_provider, expected_model, expected_url):
    from openagent.apps.superagent import __main__ as cli

    calls = []
    provider = ScriptedProvider([])

    class Registry:
        def endpoint(self, name):
            return None

        def ref(self, name, model, *, base_url):
            calls.append((name, model, base_url))
            return 'ref'

        async def build_async(self, ref):
            assert ref == 'ref'
            return provider

    monkeypatch.setattr(cli, 'ProviderRegistry', Registry)
    args = cli._parser().parse_args([*arguments, '--workspace', str(tmp_path)])
    runtime = await cli._build(args)
    assert runtime.router.provider is provider
    assert calls == [(expected_provider, expected_model, expected_url)]


async def test_subscription_rejects_custom_url(tmp_path):
    from openagent.apps.superagent import __main__ as cli

    args = cli._parser().parse_args([
        '--provider', 'openai_oauth', '--base-url', 'https://example.com',
        '--workspace', str(tmp_path),
    ])
    with pytest.raises(ValueError, match='not subscription auth'):
        await cli._build(args)
