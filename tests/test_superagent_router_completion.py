"""Router completion follows successful domain obligations, not closing prose."""
import pytest

from openagent.apps.superagent import Board, SuperAgentRouter
from tests.test_superagent import ScriptedProvider, call, said


def setup_batch(tmp_path):
    board = Board(workspace=str(tmp_path))
    first = board.submit('First task', actor='owner')
    second = board.submit('Second task', actor='owner', thread_id=first.thread_id)
    thread, batch = board.next_batch()
    return board, thread, batch, first, second


async def test_partial_batch_and_failed_route_do_not_finish(tmp_path):
    board, thread, batch, first, second = setup_batch(tmp_path)
    provider = ScriptedProvider([
        call('route', kind='ignore', trigger_ids='not-a-trigger'),
        call('route', kind='ignore', trigger_ids=first.id),
        call('route', kind='ignore', trigger_ids=second.id),
    ])
    router = SuperAgentRouter(provider, board)
    await router.decide(thread, batch)
    assert provider.calls == 3
    assert len(board.decisions) == 2
    assert router._agent.session.messages[-1].tool_results
    assert router._agent.completion_check is None
    assert not any('empty response' in m.text.lower() for m in router._agent.session.messages)


def reports(tmp_path):
    board = Board(workspace=str(tmp_path))
    ids = []
    for title in ('One', 'Two'):
        trigger = board.submit(title, actor='owner')
        board.route(kind='accept', trigger_ids=trigger.id, title=title)
        ids.append(list(board.tickets)[-1])
    for ticket_id in ids:
        board.take_inputs(ticket_id)
        board.report(ticket_id, 'ready', 'Done')
        board.finish_verification(ticket_id, 'passed', ['Operator test check'])
    return board, ids


async def test_relay_requires_all_reports_and_rejects_ambiguous_id(tmp_path):
    board, ids = reports(tmp_path)
    provider = ScriptedProvider([
        call('reply', purpose='result', text='Ambiguous'),
        call('reply', purpose='ack', ticket_id=ids[0], text='Wrong purpose'),
        call('reply', purpose='result', ticket_id=ids[0], text='One done'),
        call('reply', purpose='result', ticket_id=ids[1], text='Two done'),
    ])
    router = SuperAgentRouter(provider, board)
    await router.relay(list(board.notices))
    assert provider.calls == 4
    assert [(r.ticket_id, r.text) for r in board.replies] == [
        (ids[0], 'One done'), (ids[1], 'Two done')]
    assert router._relay_pending is None


async def test_failed_reply_does_not_finish(tmp_path):
    board, ids = reports(tmp_path)
    provider = ScriptedProvider([
        call('reply', purpose='result', ticket_id=ids[0], text=''),
        call('reply', purpose='result', ticket_id=ids[0], text='Done'),
    ])
    await SuperAgentRouter(provider, board).relay([board.notices[0]])
    assert provider.calls == 2
    assert len(board.replies) == 1


async def test_plain_text_does_not_claim_reports_relayed(tmp_path):
    board, _ = reports(tmp_path)
    router = SuperAgentRouter(ScriptedProvider([said('All delivered')]), board)
    with pytest.raises(RuntimeError, match='before relaying'):
        await router.relay(list(board.notices))
    assert not board.replies
    assert router._agent.completion_check is None


async def test_attention_requires_exact_question(tmp_path):
    board, ids = reports(tmp_path)
    board.notices.clear()
    ticket = board.tickets[ids[0]]
    ticket.ended = False
    board.report(ticket.id, 'blocked', 'Need decision', question='Which color?')
    provider = ScriptedProvider([
        call('reply', purpose='attention', ticket_id=ticket.id, text='Choose', question='Wrong'),
        call('reply', purpose='attention', ticket_id=ticket.id, text='Choose', question='Which color?'),
    ])
    await SuperAgentRouter(provider, board).relay(list(board.notices))
    assert provider.calls == 2
    assert board.replies[0].question == 'Which color?'


async def test_a_long_reply_is_sent_back_to_be_rewritten(tmp_path):
    board, ids = reports(tmp_path)
    provider = ScriptedProvider([
        call('reply', purpose='result', ticket_id=ids[0], text='word ' * 136),
        call('reply', purpose='result', ticket_id=ids[0], text='One done; flag not tested'),
        call('reply', purpose='result', ticket_id=ids[1], text='Two done'),
    ])
    await SuperAgentRouter(provider, board).relay(list(board.notices))
    assert [r.text for r in board.replies] == ['One done; flag not tested', 'Two done']


async def test_a_reply_that_stays_long_is_still_delivered_cut(tmp_path):
    board, ids = reports(tmp_path)
    long = call('reply', purpose='result', ticket_id=ids[0], text='word ' * 136)
    provider = ScriptedProvider([long, long, long,
                                 call('reply', purpose='result', ticket_id=ids[1], text='Two done')])
    await SuperAgentRouter(provider, board).relay(list(board.notices))
    assert provider.calls == 4
    assert len(board.replies[0].text.split()) == 101
    assert board.replies[1].text == 'Two done'
