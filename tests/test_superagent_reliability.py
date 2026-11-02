"""Host reliability tests: no network or real model needed."""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from openagent.apps.superagent.domain import AssignmentState, Policy, ReportKind
from openagent.apps.superagent.runtime import SuperAgentRuntime
from openagent.apps.superagent.shell import run_shell, scoped_directory
from openagent.apps.superagent.storage import DurableBoard, StateStore, attach_store


def accepted(tmp_path):
    board = DurableBoard(workspace=str(tmp_path), policy=Policy())
    store = StateStore(str(tmp_path))
    attach_store(board, store)
    trigger = board.submit('Make a game', actor='owner')
    board.next_batch()
    board.route(kind='accept', trigger_ids=trigger.id, title='Game', worktree_name='game',
                acknowledgement='Making the game.')
    board._inflight_batches.clear()
    board.threads[trigger.thread_id].evaluating = False
    ticket = next(iter(board.tickets.values()))
    store.save(board)
    return board, store, ticket


class Router:
    async def decide(self, thread, batch):
        pass

    async def relay(self, notices):
        pass


def runtime(board, worker, **kwargs):
    return SuperAgentRuntime(board, Router(), worker_factory=lambda _: worker, **kwargs)


def test_snapshot_round_trip_and_ids(tmp_path):
    board, store, ticket = accepted(tmp_path)
    restored = store.load()
    assert restored.tickets[ticket.id].mandate.strength is ticket.mandate.strength
    assert restored.policy.allowed == frozenset({'owner'})
    assert restored._launch == [ticket.id]
    new = restored.submit('Next', actor='owner')
    assert int(new.id.split('-')[1]) > board._ids
    assert restored._triggers[new.id] is restored.threads[new.thread_id].triggers[0]
    assert 'bearer_token' not in store.path.read_text()


def test_interrupted_assignment_requires_explicit_retry(tmp_path):
    board, store, ticket = accepted(tmp_path)
    board.next_launch()
    assignment = board.start_assignment(ticket.id)
    assert assignment.inputs and not ticket.inputs
    recovered = store.load()
    t = recovered.tickets[ticket.id]
    assert t.assignments[0].state is AssignmentState.ENDED
    assert t.reports[-1].kind is ReportKind.BLOCKED
    assert ticket.id not in recovered._launch
    rt = runtime(recovered, None, store=store)
    assert rt.retry(ticket.id).startswith('Queued')
    assert t.inputs[0].text == 'Make a game'
    assert t.assignments[0] is recovered._assignments[assignment.id]


def test_inflight_router_batch_recovered(tmp_path):
    board = DurableBoard(workspace=str(tmp_path))
    trigger = board.submit('Do work', actor='owner')
    board.next_batch()
    store = StateStore(str(tmp_path))
    store.save(board)
    recovered = store.load()
    assert recovered.next_batch()[1][0].id == trigger.id


def test_invalid_version_does_not_overwrite(tmp_path):
    store = StateStore(str(tmp_path))
    store.path.write_text('{"version": 999}')
    with pytest.raises(ValueError, match='version'):
        store.load()
    assert json.loads(store.path.read_text())['version'] == 999


def test_ack_once_and_unique_workspaces(tmp_path):
    board, _, ticket = accepted(tmp_path)
    board.ack(ticket.id, 'Duplicate acknowledgement')
    board.reply(purpose='ack', ticket_id=ticket.id, text='Another acknowledgement')
    assert len(board.replies) == 1
    trigger = board.submit('Another game', actor='owner')
    board.route(kind='accept', trigger_ids=trigger.id, title='Game', worktree_name='game')
    assert len({t.directory for t in board.tickets.values()}) == 2
    board.route(kind='accept', trigger_ids=trigger.id)
    assert len(board.tickets) == 2


async def test_watchdog_cancels_hung_worker(tmp_path):
    board, store, ticket = accepted(tmp_path)
    cancelled = asyncio.Event()

    async def run(inputs):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    rt = runtime(board, SimpleNamespace(run=run), assignment_timeout=.02, store=store)
    await rt.run_pending()
    assert cancelled.is_set()
    assert ticket.reports[-1].kind is ReportKind.FAILED
    assert 'Watchdog' in ticket.reports[-1].summary
    assert ticket.assignments[-1].state is AssignmentState.ENDED
    assert store.load().tickets[ticket.id].reports[-1].kind is ReportKind.FAILED


async def test_missing_report_is_blocked(tmp_path):
    board, _, ticket = accepted(tmp_path)

    async def run(inputs):
        return 'I am done'  # plain text is not a host report

    await runtime(board, SimpleNamespace(run=run)).run_pending()
    assert ticket.reports[-1].kind is ReportKind.BLOCKED
    assert not ticket.ended


async def test_stop_cancels_assignment(tmp_path):
    board, _, ticket = accepted(tmp_path)
    started = asyncio.Event()

    async def run(inputs):
        started.set()
        await asyncio.sleep(60)

    rt = runtime(board, SimpleNamespace(run=run))
    runner = asyncio.create_task(rt.run_pending())
    await started.wait()
    await rt.stop(ticket.id)
    await runner
    assert ticket.stopped and ticket.ended
    assert not rt._active
    assert rt.retry(ticket.id).startswith('Error:')


async def test_followup_during_worker_is_not_lost(tmp_path):
    board, _, ticket = accepted(tmp_path)
    calls = []

    async def run(inputs):
        calls.append(inputs)
        if len(calls) == 1:
            board.submit('Add restart', actor='owner', thread_id=ticket.origin_thread_ids[0])
        board.report(ticket.id, 'ready', 'Done')

    await runtime(board, SimpleNamespace(run=run)).run_pending()
    assert len(calls) == 2
    assert calls[1][0].text == 'Add restart'


async def test_revoked_mandate_never_executes(tmp_path):
    board, _, ticket = accepted(tmp_path)
    board.policy = Policy(allowed=frozenset())

    async def run(inputs):
        pytest.fail('unauthorized execution')

    await runtime(board, SimpleNamespace(run=run)).run_pending()
    assert ticket.reports[-1].kind is ReportKind.BLOCKED
    assert not ticket.assignments


async def test_router_deadline_marks_unavailable(tmp_path):
    board = DurableBoard(workspace=str(tmp_path))
    trigger = board.submit('Build', actor='owner')

    async def decide(thread, batch):
        await asyncio.sleep(60)

    rt = SuperAgentRuntime(board, SimpleNamespace(decide=decide), router_timeout=.01)
    await rt.run_pending()
    assert board.decisions[0].trigger_ids == (trigger.id,)
    assert board.decisions[0].kind.value == 'unavailable'


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX process groups')
async def test_shell_timeout_kills_descendant(tmp_path):
    pidfile = tmp_path / 'pid'
    output = await run_shell(f'sleep 60 & echo $! > {pidfile}; wait', str(tmp_path), .1)
    assert 'timed out' in output
    pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(.02)
    else:
        pytest.fail(f'shell descendant {pid} survived timeout')


async def test_shell_cancellation_and_output_limit(tmp_path):
    task = asyncio.create_task(run_shell('sleep 60', str(tmp_path), 120))
    await asyncio.sleep(.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    output = await run_shell("printf hello; exit 7", str(tmp_path))
    assert 'Exit code: 7' in output and 'hello' in output
    output = await run_shell("yes x | head -c 100000", str(tmp_path))
    assert len(output) < 64100


def test_directory_scope(tmp_path):
    with pytest.raises(ValueError, match='inside'):
        scoped_directory(str(tmp_path), '..')
    link = tmp_path / 'escape'
    link.symlink_to(tmp_path.parent)
    with pytest.raises(ValueError, match='inside'):
        scoped_directory(str(tmp_path), str(link))


async def test_session_checkpoint_repairs_missing_tool_result(tmp_path):
    from openagent.apps.superagent import sessions
    from openagent.core.session import Session
    from openagent.core.types import Message, ToolUseBlock

    agent = SimpleNamespace(session=Session(system_prompt='Current policy'))
    agent.session.add_message(Message(role='assistant', content=[ToolUseBlock('bash', {'command': 'echo x'})]))
    path = tmp_path / 'session.json'
    sessions.save(agent, path)
    agent.session = Session(system_prompt='New policy')
    sessions.restore(agent, path)
    assert agent.session.system_prompt == 'New policy'
    assert agent.session.messages[-1].tool_results[0].is_error


async def test_watchdog_cancels_real_shell_assignment(tmp_path):
    board, _, ticket = accepted(tmp_path)
    pidfile = tmp_path / 'worker-shell.pid'

    async def run(inputs):
        await run_shell(f'echo $$ > {pidfile}; sleep 60', str(tmp_path), 120)

    rt = runtime(board, SimpleNamespace(run=run), assignment_timeout=.15)
    await rt.run_pending()
    assert ticket.reports[-1].kind is ReportKind.FAILED
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


async def test_worker_construction_failure_finishes_assignment(tmp_path):
    board, _, ticket = accepted(tmp_path)

    def broken_factory(ticket_id):
        raise OSError('Cannot create worker directory')

    rt = SuperAgentRuntime(board, Router(), worker_factory=broken_factory)
    await rt.run_pending()
    assert ticket.assignments[-1].state is AssignmentState.ENDED
    assert ticket.reports[-1].kind is ReportKind.FAILED


async def test_early_ready_does_not_hide_later_timeout(tmp_path):
    board, _, ticket = accepted(tmp_path)

    async def run(inputs):
        board.report(ticket.id, 'ready', 'Premature completion')
        await asyncio.sleep(60)

    await runtime(board, SimpleNamespace(run=run), assignment_timeout=.01).run_pending()
    assert not ticket.ended
    assert ticket.reports[-1].kind is ReportKind.FAILED
