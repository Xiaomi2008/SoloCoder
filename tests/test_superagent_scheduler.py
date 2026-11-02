"""Heartbeat stalls, bounded repair, and concurrent assignment slots."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from openagent.apps.superagent import Board, Policy, ReportKind, SuperAgentRuntime
from openagent.apps.superagent.domain import AssignmentState
from openagent.apps.superagent.storage import StateStore, attach_store, DurableBoard
from tests.test_superagent_reliability import Router, accepted


def tickets(tmp_path, count=1, durable=False):
    board = DurableBoard(workspace=str(tmp_path)) if durable else Board(workspace=str(tmp_path), policy=Policy())
    store = StateStore(str(tmp_path)) if durable else None
    if store:
        attach_store(board, store)
    out = []
    for index in range(count):
        trigger = board.submit(f'Task {index}', actor='owner')
        board.route(kind='accept', trigger_ids=trigger.id, title=f'Task {index}',
                    worktree_name=f'task-{index}')
        out.append(list(board.tickets.values())[-1])
    board.threads[next(iter(board.threads))].evaluating = False
    board._inflight_batches.clear()
    return board, store, out


async def test_heartbeat_extends_deadline(tmp_path):
    board, _, (ticket,) = tickets(tmp_path)
    async def run(inputs):
        for _ in range(8):
            board.heartbeat(ticket.id, 'working')
            await asyncio.sleep(.02)
        board.report(ticket.id, 'ready', 'Done')
    rt = SuperAgentRuntime(board, Router(), worker_factory=lambda _: SimpleNamespace(run=run),
                           heartbeat_timeout=.1, assignment_timeout=5)
    await rt.run_pending()
    assert ticket.verification == 'review'  # no checks configured
    assert ticket.progress == 'working'
    assert ticket.reports[-1].kind is ReportKind.READY


async def test_silent_worker_stall_is_killed(tmp_path):
    board, _, (ticket,) = tickets(tmp_path)
    cancelled = asyncio.Event()
    async def run(inputs):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()
    rt = SuperAgentRuntime(board, Router(), worker_factory=lambda _: SimpleNamespace(run=run),
                           heartbeat_timeout=.03, assignment_timeout=5)
    await rt.run_pending()
    assert cancelled.is_set()
    assert 'heartbeat stall' in ticket.reports[-1].summary
    assert ticket.assignments[-1].state is AssignmentState.ENDED


async def test_total_deadline_still_applies_with_heartbeats(tmp_path):
    board, _, (ticket,) = tickets(tmp_path)
    async def run(inputs):
        while True:
            board.heartbeat(ticket.id, 'busy')
            await asyncio.sleep(.01)
    rt = SuperAgentRuntime(board, Router(), worker_factory=lambda _: SimpleNamespace(run=run),
                           heartbeat_timeout=.05, assignment_timeout=.08)
    await rt.run_pending()
    assert 'assignment deadline' in ticket.reports[-1].summary


async def test_retry_only_for_host_detected_failures(tmp_path):
    board, _, (ticket,) = tickets(tmp_path)
    calls = 0
    async def run(inputs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('transient failure')
        board.report(ticket.id, 'ready', 'Recovered')
    rt = SuperAgentRuntime(board, Router(), worker_factory=lambda _: SimpleNamespace(run=run),
                           auto_retries=2, retry_backoff=0)
    await rt.run_pending()
    assert calls == 2
    assert ticket.retry_budget == 1
    assert ticket.verification == 'review'
    assert ticket.failure_class == 'worker'


async def test_retries_are_bounded(tmp_path):
    board, _, (ticket,) = tickets(tmp_path)
    calls = 0
    async def run(inputs):
        nonlocal calls
        calls += 1
        raise RuntimeError('always')
    rt = SuperAgentRuntime(board, Router(), worker_factory=lambda _: SimpleNamespace(run=run),
                           auto_retries=2, retry_backoff=0)
    await rt.run_pending()
    assert calls == 3  # one original + two retries
    assert ticket.retry_budget == 2
    assert 'auto_retries' not in ticket.reports[-1].summary


async def test_worker_declared_failure_is_not_auto_retried(tmp_path):
    board, _, (ticket,) = tickets(tmp_path)
    calls = 0
    async def run(inputs):
        nonlocal calls
        calls += 1
        board.report(ticket.id, 'failed', 'I broke it')
    rt = SuperAgentRuntime(board, Router(), worker_factory=lambda _: SimpleNamespace(run=run),
                           auto_retries=3, retry_backoff=0)
    await rt.run_pending()
    assert calls == 1 and ticket.retry_budget == 0


async def test_verification_failure_retries_then_blocks(tmp_path):
    board, _, (ticket,) = tickets(tmp_path)
    worker_calls = 0
    from pathlib import Path
    async def run(inputs):
        nonlocal worker_calls
        worker_calls += 1
        Path(ticket.directory).mkdir(parents=True, exist_ok=True)
        board.report(ticket.id, 'ready', 'Done')
    rt = SuperAgentRuntime(board, Router(), worker_factory=lambda _: SimpleNamespace(run=run),
                           verify_commands=['exit 1'], auto_retries=1, retry_backoff=0)
    await rt.run_pending()
    assert worker_calls == 2
    assert ticket.retry_budget == 1
    assert ticket.verification == 'failed'
    assert ticket.reports[-1].kind is ReportKind.BLOCKED


async def test_retry_backoff_waits(tmp_path):
    board, _, (ticket,) = tickets(tmp_path)
    async def run(inputs):
        raise RuntimeError('fail')
    rt = SuperAgentRuntime(board, Router(), worker_factory=lambda _: SimpleNamespace(run=run),
                           auto_retries=1, retry_backoff=.15)
    start = time.monotonic()
    await rt.run_pending()
    assert time.monotonic() - start >= .14
    assert ticket.retry_budget == 1


async def test_concurrency_slots_overlap(tmp_path):
    board, _, tickets_ = tickets(tmp_path, 2)
    started = []
    gate = asyncio.Event()
    def run_factory(ticket_id):
        async def run(inputs):
            started.append(ticket_id)
            await gate.wait()
            board.report(ticket_id, 'ready', 'Done')
        return SimpleNamespace(run=run)
    rt = SuperAgentRuntime(board, Router(), worker_factory=run_factory, max_concurrent=2)
    runner = asyncio.create_task(rt.run_pending())
    for _ in range(100):
        if len(started) == 2:
            break
        await asyncio.sleep(.01)
    assert len(started) == 2
    gate.set()
    await runner
    assert all(t.verification == 'review' for t in tickets_)


async def test_max_concurrent_one_serializes(tmp_path):
    board, _, tickets_ = tickets(tmp_path, 2)
    active, peak = 0, 0
    def run_factory(ticket_id):
        async def run(inputs):
            nonlocal active, peak
            active += 1; peak = max(peak, active)
            await asyncio.sleep(.02)
            board.report(ticket_id, 'ready', 'Done')
            active -= 1
        return SimpleNamespace(run=run)
    await SuperAgentRuntime(board, Router(), worker_factory=run_factory, max_concurrent=1).run_pending()
    assert peak == 1


async def test_verification_waits_for_concurrent_mutation(tmp_path):
    board, _, tickets_ = tickets(tmp_path, 2)
    release = asyncio.Event()
    def run_factory(ticket_id):
        async def run(inputs):
            from pathlib import Path
            Path(board.tickets[ticket_id].directory).mkdir(parents=True, exist_ok=True)
            if ticket_id == tickets_[0].id:
                board.report(ticket_id, 'ready', 'Done')
                await release.wait()
            else:
                await release.wait()
                board.report(ticket_id, 'ready', 'Done')
        return SimpleNamespace(run=run)
    rt = SuperAgentRuntime(board, Router(), worker_factory=run_factory, max_concurrent=2,
                           verify_commands=['true'])
    runner = asyncio.create_task(rt.run_pending())
    await asyncio.sleep(.1)
    assert tickets_[0].verification == 'pending'  # other Worker still mutating
    release.set()
    await runner
    assert all(t.verification == 'passed' for t in tickets_)


def test_stall_stage_and_persistence(tmp_path):
    board, store, (ticket,) = tickets(tmp_path, durable=True)
    board.next_launch()
    board.start_assignment(ticket.id)
    board.heartbeat(ticket.id, 'halfway')
    board.schedule_retry(ticket.id, 'worker', 1, 0)
    assert ticket.retry_budget == 1
    assert ticket.progress == 'halfway'
    assert board.schedule_retry(ticket.id, 'worker', 1, 0) == 'retry refused'
    assert board.launch_due(time.time() + 1) == ticket.id
    recovered = store.load()
    assert recovered.tickets[ticket.id].progress == 'halfway'
    assert recovered.tickets[ticket.id].retry_budget == 1
    assert recovered.tickets[ticket.id].assignments[0].last_heartbeat
