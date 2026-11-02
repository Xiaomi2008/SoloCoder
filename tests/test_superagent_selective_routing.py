"""Multi-ticket follow-ups are selected by the Router, never broadcast."""
from types import SimpleNamespace

import pytest

from openagent.apps.superagent import Board, DecisionKind, Policy, SuperAgentRouter, SuperAgentRuntime
from openagent.apps.superagent.router import format_batch
from openagent.apps.superagent.storage import DurableBoard, StateStore, attach_store
from tests.test_superagent import ScriptedProvider, call


def setup_tickets(tmp_path, board_cls=Board):
    board = board_cls(workspace=str(tmp_path))
    first = board.submit('Build a game', actor='owner')
    second = board.submit('Write game instructions', actor='owner', thread_id=first.thread_id)
    thread, _ = board.next_batch()
    board.route(kind='accept', trigger_ids=first.id, title='Game')
    board.route(kind='accept', trigger_ids=second.id, title='Instructions')
    game, docs = board.tickets.values()
    thread.evaluating = False
    board._inflight_batches.clear()
    for ticket in (game, docs):
        board.take_inputs(ticket.id)
    board._launch.clear()
    return board, thread, game, docs


def followup(board, thread, text='Change the game controls to WASD', **kwargs):
    trigger = board.submit(text, actor=kwargs.pop('actor', 'owner'), thread_id=thread.id, **kwargs)
    return trigger


def test_attach_delivers_only_selected_ticket(tmp_path):
    board, thread, game, docs = setup_tickets(tmp_path)
    trigger = followup(board, thread)
    assert not game.inputs and not docs.inputs
    board.next_batch()
    result = board.route(kind='attach', trigger_ids=trigger.id, ticket_id=game.id)
    assert result.startswith('attach ->')
    assert [i.trigger_id for i in game.inputs] == [trigger.id]
    assert not docs.inputs
    assert board.next_launch() == game.id
    assert board.next_launch() is None
    board.route(kind='attach', trigger_ids=trigger.id, ticket_id=docs.id)
    assert not docs.inputs  # duplicate routing is refused


def test_invalid_target_can_be_corrected(tmp_path):
    board, thread, game, docs = setup_tickets(tmp_path)
    trigger = followup(board, thread)
    before = len(board.decisions)
    assert board.route(kind='attach', trigger_ids=trigger.id, ticket_id='ticket-missing').startswith('Error:')
    assert len(board.decisions) == before
    board.route(kind='attach', trigger_ids=trigger.id, ticket_id=game.id)
    assert game.inputs and not docs.inputs


@pytest.mark.parametrize('state', ['stopped', 'revoked'])
def test_invalid_mandates_and_stopped_targets_refused(tmp_path, state):
    board, thread, game, docs = setup_tickets(tmp_path)
    if state == 'stopped':
        board.stop(game.id)
    else:
        board.policy = Policy(allowed=frozenset({'someone-else'}))
    # Still routes rather than choosing the other available ticket.
    trigger = followup(board, thread)
    assert trigger in thread.pending
    assert board.route(kind='attach', trigger_ids=trigger.id, ticket_id=game.id).startswith('Error:')
    assert not game.inputs and not docs.inputs


def test_active_revoked_target_rechecked(tmp_path):
    board, thread, game, _ = setup_tickets(tmp_path)
    # A different allowed actor still cannot reuse the revoked owner's mandate.
    board.policy = Policy(allowed=frozenset({'other-owner'}))
    trigger = followup(board, thread, actor='other-owner')
    result = board.route(kind='attach', trigger_ids=trigger.id, ticket_id=game.id)
    assert 'mandate revoked' in result
    assert not game.inputs


@pytest.mark.parametrize('kind', ['attach', 'stop'])
def test_untrusted_actor_cannot_control_existing_ticket(tmp_path, kind):
    board, thread, game, _ = setup_tickets(tmp_path)
    trigger = followup(board, thread, actor='stranger')
    assert board.route(kind=kind, trigger_ids=trigger.id, ticket_id=game.id).startswith('Error:')
    assert not game.inputs and not game.stopped


def test_attach_reopens_only_selected_completed_ticket(tmp_path):
    board, thread, game, docs = setup_tickets(tmp_path)
    board.report(game.id, 'ready', 'Game done')
    board.report(docs.id, 'ready', 'Docs done')
    board.finish_verification(game.id, 'passed', ['Host check passed'])
    board.finish_verification(docs.id, 'passed', ['Host check passed'])
    trigger = followup(board, thread)
    assert game.ended and docs.ended
    board.route(kind='attach', trigger_ids=trigger.id, ticket_id=game.id)
    assert not game.ended and docs.ended
    assert game.inputs[0].trigger_id == trigger.id
    assert board.next_launch() == game.id


def test_clarification_delivers_question_without_launch(tmp_path):
    board, thread, game, docs = setup_tickets(tmp_path)
    trigger = followup(board, thread, 'Make it better')
    assert board.route(kind='clarify', trigger_ids=trigger.id).startswith('Error:')
    question = f'Which task: {game.id} Game or {docs.id} Instructions?'
    board.route(kind='clarify', trigger_ids=trigger.id, reason=question)
    assert board.replies[-1].text == question
    assert board.decisions[-1].kind is DecisionKind.CLARIFY
    assert not game.inputs and not docs.inputs
    assert board.next_launch() is None
    # Clarification answer is a new candidate. Router can include the original
    # requested change in reason, so the worker sees more than just "the game".
    answer = followup(board, thread, 'The game')
    board.route(kind='attach', trigger_ids=answer.id, ticket_id=game.id,
                reason='Original request: improve the game controls')
    assert 'improve the game controls' in game.inputs[0].text
    assert 'The game' in game.inputs[0].text
    assert not docs.inputs


def test_context_subscription_does_not_auto_launch(tmp_path):
    board, _, game, docs = setup_tickets(tmp_path)
    other = board.submit('Reference discussion', actor='owner')
    board.subscribe(game.id, other.thread_id)
    trigger = board.submit('Some reference information', actor='owner', thread_id=other.thread_id)
    assert not game.inputs
    assert trigger in board.threads[other.thread_id].pending


def test_selective_routing_survives_restart(tmp_path):
    board, thread, game, docs = setup_tickets(tmp_path, DurableBoard)
    store = StateStore(str(tmp_path))
    attach_store(board, store)
    trigger = followup(board, thread)
    board = store.load()
    assert board.next_batch()[1][0].id == trigger.id
    attach_store(board, store)
    board.route(kind='attach', trigger_ids=trigger.id, ticket_id=game.id)
    recovered = store.load()
    assert recovered.tickets[game.id].inputs[0].trigger_id == trigger.id
    assert not recovered.tickets[docs.id].inputs


async def test_real_router_tool_selects_worker_without_broadcast(tmp_path):
    board, thread, game, docs = setup_tickets(tmp_path)
    trigger = followup(board, thread)
    provider = ScriptedProvider([call('route', kind='attach', trigger_ids=trigger.id, ticket_id=game.id)])
    workers_called = []

    def factory(ticket_id):
        async def run(inputs):
            workers_called.append((ticket_id, [i.text for i in inputs]))
            board.report(ticket_id, 'waiting', 'Updated controls')
        return SimpleNamespace(run=run)

    runtime = SuperAgentRuntime(board, SuperAgentRouter(provider, board), worker_factory=factory)
    await runtime.run_pending()
    assert workers_called == [(game.id, ['Change the game controls to WASD'])]
    assert provider.calls == 1  # no final prose inference, no other worker


async def test_router_can_create_new_task_on_multi_ticket_thread(tmp_path):
    board, thread, game, docs = setup_tickets(tmp_path)
    trigger = followup(board, thread, 'Build a calculator, unrelated to the game')
    provider = ScriptedProvider([call('route', kind='accept', trigger_ids=trigger.id, title='Calculator')])
    thread, batch = board.next_batch()
    await SuperAgentRouter(provider, board).decide(thread, batch)
    assert len(board.tickets) == 3
    assert not game.inputs and not docs.inputs
    assert list(board.tickets.values())[-1].inputs[0].trigger_id == trigger.id


def test_prompt_contains_target_details_and_clarification_context(tmp_path):
    board, thread, game, docs = setup_tickets(tmp_path)
    ambiguous = followup(board, thread, 'Change the appearance')
    board.route(kind='clarify', trigger_ids=ambiguous.id, reason='Game or instructions?')
    game.attention = 'Which color?'
    answer = followup(board, thread, 'The game')
    prompt = format_batch(board, thread, [answer])
    assert game.id in prompt and docs.id in prompt
    assert 'Which color?' in prompt
    assert 'Change the appearance' in prompt
    assert 'Last routing question: Game or instructions?' in prompt


@pytest.mark.parametrize('actor,addresses_agent', [('stranger', True), ('owner', False)])
def test_single_ticket_fast_path_requires_authorized_direct_request(tmp_path, actor, addresses_agent):
    board = Board(workspace=str(tmp_path))
    first = board.submit('Build a game', actor='owner')
    board.next_batch()
    board.route(kind='accept', trigger_ids=first.id, title='Game')
    game = next(iter(board.tickets.values()))
    board.take_inputs(game.id)
    board._launch.clear()
    followup(board, board.threads[first.thread_id], actor=actor, addresses_agent=addresses_agent)
    assert not game.inputs
    assert board.next_launch() is None
