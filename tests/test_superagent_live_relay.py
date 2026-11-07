"""Replies go out as each worker reports, and input is routed while others work."""
import asyncio
from types import SimpleNamespace

from openagent.apps.superagent import SuperAgentRuntime
from tests.test_superagent_scheduler import tickets


class RelayingRouter:
    """Relays each notice as a reply, and records what it was asked to route."""

    def __init__(self, board, on_decide=None):
        self.board = board
        self.decided = []
        self.on_decide = on_decide

    async def decide(self, thread, batch):
        self.decided.extend(t.text for t in batch)
        if self.on_decide:
            self.on_decide(batch)

    async def relay(self, notices):
        for notice in notices:
            self.board.reply(purpose='notice', text=notice.text, ticket_id=notice.ticket_id)


async def until(condition, timeout=2.0):
    for _ in range(int(timeout / .01)):
        if condition():
            return True
        await asyncio.sleep(.01)
    return condition()


async def test_a_report_reaches_the_person_while_another_worker_runs(tmp_path):
    board, _, (quick, slow) = tickets(tmp_path, 2)
    gate = asyncio.Event()

    def run_factory(ticket_id):
        async def run(inputs):
            if ticket_id == slow.id:
                await gate.wait()
            board.report(ticket_id, 'blocked', f'{ticket_id} needs a decision')
        return SimpleNamespace(run=run)

    seen = []
    rt = SuperAgentRuntime(board, RelayingRouter(board), worker_factory=run_factory,
                           max_concurrent=2, on_reply=seen.append)
    runner = asyncio.create_task(rt.run_pending())

    assert await until(lambda: any(r.ticket_id == quick.id for r in seen))
    assert not gate.is_set() and slow.id in rt._active  # the other worker is still running
    gate.set()
    assert await runner == []  # everything went out through on_reply
    assert {r.ticket_id for r in seen} == {quick.id, slow.id}


async def test_without_on_reply_replies_are_returned_at_the_end(tmp_path):
    board, _, (ticket,) = tickets(tmp_path)

    async def run(inputs):
        board.report(ticket.id, 'blocked', 'needs a decision')

    rt = SuperAgentRuntime(board, RelayingRouter(board),
                           worker_factory=lambda _: SimpleNamespace(run=run))
    replies = await rt.run_pending()
    assert [r.ticket_id for r in replies] == [ticket.id]


async def test_input_is_routed_while_a_worker_runs(tmp_path):
    board, _, (ticket,) = tickets(tmp_path)
    gate = asyncio.Event()

    async def run(inputs):
        await gate.wait()
        board.report(ticket.id, 'ready', 'Done')

    router = RelayingRouter(board)
    rt = SuperAgentRuntime(board, router, worker_factory=lambda _: SimpleNamespace(run=run),
                           on_reply=lambda reply: None)
    runner = asyncio.create_task(rt.run_pending())
    assert await until(lambda: ticket.id in rt._active)

    board.submit('Also, should enemies respawn?', actor='owner')
    rt.intake()
    assert await until(lambda: 'Also, should enemies respawn?' in router.decided)
    assert not gate.is_set()  # routed before the running worker finished
    gate.set()
    await runner


async def test_input_for_a_running_ticket_does_not_start_a_second_worker(tmp_path):
    board, _, (ticket,) = tickets(tmp_path)
    gate = asyncio.Event()
    runs = []

    async def run(inputs):
        runs.append([i.text for i in inputs])
        if len(runs) == 1:
            await gate.wait()
        board.report(ticket.id, 'blocked', f'run {len(runs)} done')

    def deliver(batch):
        for trigger in batch:
            board._deliver(ticket, trigger)

    rt = SuperAgentRuntime(board, RelayingRouter(board, on_decide=deliver),
                           worker_factory=lambda _: SimpleNamespace(run=run),
                           max_concurrent=2, on_reply=lambda reply: None)
    runner = asyncio.create_task(rt.run_pending())
    assert await until(lambda: len(runs) == 1)

    board.submit('One more thing', actor='owner')
    rt.intake()
    await asyncio.sleep(.1)
    assert len(runs) == 1  # a free slot, but the ticket is already running
    gate.set()
    await runner
    assert len(runs) == 2 and runs[1] == ['One more thing']  # picked up once the first ended
