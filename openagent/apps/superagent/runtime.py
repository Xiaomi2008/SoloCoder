"""Concurrent scheduler: slots, heartbeats, bounded repair, verification.

Concurrency is opt-in (default 1). Each assignment is an independent asyncio
task; shared shell/file state is the operator's responsibility. Automatic retry
is limited to host-detected worker/verification failures with bounded backoff.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable

from .domain import Board, Input, ReportKind
from .execution import Execution
from .router import SuperAgentRouter
from .worker import SuperAgentWorker


class WatchdogTimeout(TimeoutError):
    """Total deadline or heartbeat silence; both cancel the worker."""


class SuperAgentRuntime:
    def __init__(self, board: Board, router: SuperAgentRouter,
                 worker_factory: Callable[[str], SuperAgentWorker] | None = None,
                 *, assignment_timeout: float = 600, router_timeout: float = 120,
                 store=None, execution=None, verify_commands=(), worktrees=None,
                 max_concurrent: int = 1, heartbeat_timeout: float = 120,
                 auto_retries: int = 0, retry_backoff: float = 30.0) -> None:
        if min(assignment_timeout, router_timeout, heartbeat_timeout) <= 0:
            raise ValueError('Deadlines must be positive')
        if max_concurrent < 1 or auto_retries < 0 or retry_backoff < 0:
            raise ValueError('Invalid scheduler limits')
        self.board = board
        self.router = router
        self._worker_factory = worker_factory
        self._workers: dict[str, SuperAgentWorker] = {}
        self.assignment_timeout = assignment_timeout
        self.router_timeout = router_timeout
        self.heartbeat_timeout = heartbeat_timeout
        self.max_concurrent = max_concurrent
        self.auto_retries = auto_retries
        self.retry_backoff = retry_backoff
        self.store = store
        self._active: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self.execution = execution or Execution()
        self.verify_commands = tuple(verify_commands)
        self.worktrees = worktrees
        board.automatic_retry_limit = auto_retries
        board.retry_backoff = retry_backoff
        # Verification cannot safely run while a Worker mutates the directory.
        self._verify_lock = asyncio.Lock()

    def checkpoint(self):
        if self.store is not None:
            self.store.save(self.board)

    def worker_for(self, ticket_id: str) -> SuperAgentWorker:
        if ticket_id not in self._workers:
            self._workers[ticket_id] = (
                self._worker_factory(ticket_id) if self._worker_factory else
                SuperAgentWorker(self.router.provider, self.board, ticket_id,
                                 execution=self.execution, heartbeat_timeout=self.heartbeat_timeout))
        return self._workers[ticket_id]

    async def run_pending(self):
        async with self._lock:
            try:
                while True:
                    await self._assess_pending()
                    await self._run_launches()
                    await self._verify_pending()
                    # Verification can schedule a retry after the launch loop
                    # exited. Cycle again only when one is actually due.
                    if not any(t.id in self.board._launch and t.retry_not_before <= time.time()
                               for t in self.board.tickets.values()
                               if not t.stopped and not t.ended):
                        break
                await self._relay_notices()
                return self.board.take_replies()
            finally:
                self.checkpoint()

    async def submit(self, text: str, **kwargs):
        self.board.submit(text, **kwargs)
        self.checkpoint()
        return await self.run_pending()

    async def stop(self, ticket_id: str) -> str:
        result = self.board.stop(ticket_id)
        task = self._active.get(ticket_id)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.checkpoint()
        return result

    def retry(self, ticket_id: str) -> str:
        ticket = self.board.tickets.get(ticket_id)
        if ticket is None:
            return 'Error: unknown ticket.'
        if ticket.stopped or ticket.ended or ticket_id in self._active:
            return 'Error: only inactive, unfinished tickets can be retried.'
        if not ticket.mandate.still_valid(self.board.policy):
            return 'Error: mandate revoked.'
        if not ticket.assignments:
            return 'Error: ticket is already queued.'
        if not ticket.inputs:
            ticket.inputs = list(ticket.assignments[-1].inputs)
        if not ticket.inputs:
            ticket.inputs = [Input('operator-retry',
                                   'Inspect current files and safely resume the task.', self.board._tick())]
        ticket.attention = None
        ticket.verification = "not_requested"
        ticket.retry_not_before = 0.0
        if ticket_id not in self.board._launch:
            self.board._launch.append(ticket_id)
        self.checkpoint()
        return f'Queued {ticket_id}; prior side effects are not rolled back.'

    async def _assess_pending(self):
        while (item := self.board.next_batch()) is not None:
            thread, batch = item
            self.checkpoint()
            try:
                await asyncio.wait_for(self.router.decide(thread, batch), self.router_timeout)
            except Exception as error:
                self.board.log(f'orchestrator failed: {type(error).__name__}: {error}')
            finally:
                decided = {tid for d in self.board.decisions for tid in d.trigger_ids}
                for trigger in batch:
                    if trigger.id not in decided:
                        self.board.record_unavailable(trigger,
                            'Router ended without a decision or exceeded deadline')
                thread.evaluating = False
                self.board._inflight_batches.pop(thread.id, None)
                self.checkpoint()

    async def _run_launches(self):
        pending: set[asyncio.Task] = set()
        while True:
            # Fill free slots from due launches. If all active work is sleeping
            # in backoff, wait for the earliest retry instead of busy-spinning.
            while len(pending) < self.max_concurrent:
                ticket_id = self.board.launch_due(time.time())
                if ticket_id is None:
                    break
                task = asyncio.create_task(self._assignment(ticket_id))
                pending.add(task)
                task.add_done_callback(pending.discard)
            if not pending:
                # A just-failed assignment may have scheduled a retry after the
                # slot-filling pass. Wait for it; returning now would strand it
                # until an unrelated submit woke the scheduler.
                retries = [t.retry_not_before for t in self.board.tickets.values()
                           if t.id in self.board._launch and not t.stopped and not t.ended]
                if retries:
                    await asyncio.sleep(min(5.0, max(.01, min(retries) - time.time())))
                    continue
                return
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if (error := task.exception()) and not isinstance(error, asyncio.CancelledError):
                    self.board.log(f'assignment task error: {type(error).__name__}: {error}')
        # Loop exits when pending is empty and no due launch remains.

    async def _assignment(self, ticket_id: str):
        ticket = self.board.tickets[ticket_id]
        if not ticket.inputs:
            return
        if not ticket.mandate.still_valid(self.board.policy):
            self.board.report(ticket_id, 'blocked', 'Mandate revoked; execution refused.')
            return
        assignment = self.board.start_assignment(ticket_id)
        before = len(ticket.reports)
        self.checkpoint()
        try:
            if ticket.repository and self.worktrees is None:
                raise RuntimeError("Ticket requires its --repo worktree configuration to resume")
            if self.worktrees is not None:
                if not ticket.repository:
                    ticket.repository = self.worktrees.repo
                    ticket.branch = 'superagent/' + ticket.id
                    from .worktrees import git
                    ticket.base_commit = await git(self.worktrees.repo, 'rev-parse', 'HEAD')
                    self.checkpoint()
                await self.worktrees.prepare(ticket)
            task = asyncio.create_task(self.worker_for(ticket_id).run(assignment.inputs))
            self._active[ticket_id] = task
            await self._watch(task, ticket)
            if len(ticket.reports) == before and not ticket.stopped:
                self.board.report(ticket_id, 'blocked',
                                  'Worker ended without a report. Inspect and retry explicitly.')
        except WatchdogTimeout as error:
            self._fail(ticket_id, f'Watchdog: {error}; execution cancelled.', 'worker')
        except asyncio.CancelledError:
            if not ticket.stopped:
                self._fail(ticket_id, 'Assignment interrupted; inspect files before retrying.', None)
                raise
        except Exception as error:
            self._fail(ticket_id, f'Executor failed: {type(error).__name__}: {error}', 'worker')
        finally:
            self._active.pop(ticket_id, None)
            self.board.finish_assignment(assignment.id)
            if ticket.inputs and not ticket.stopped and ticket.mandate.still_valid(self.board.policy):
                ticket.verification = "not_requested"
                ticket.ended = False
                ticket.outcome = None
                if ticket_id not in self.board._launch:
                    self.board._launch.append(ticket_id)
            self.checkpoint()

    async def _watch(self, task: asyncio.Task, ticket) -> None:
        """Bound total duration, then bound silence since the last heartbeat."""
        deadline = time.time() + self.assignment_timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise WatchdogTimeout('assignment deadline')
            silence = max(0.0, ticket.assignments[-1].last_heartbeat + self.heartbeat_timeout
                          - time.time())
            await asyncio.wait({task}, timeout=min(remaining, silence) or 0.001,
                               return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                # Propagate the worker's exception; never leave it un-retrieved.
                task.result()
                return
            if time.time() >= (ticket.assignments[-1].last_heartbeat + self.heartbeat_timeout):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise WatchdogTimeout('heartbeat stall')

    def _fail(self, ticket_id, summary, failure_class=None):
        ticket = self.board.tickets[ticket_id]
        if ticket.stopped:
            return
        ticket.ended = False
        ticket.outcome = None
        ticket.verification = "failed"
        self.board.notices = [n for n in self.board.notices
                              if not (n.ticket_id == ticket_id and n.kind is ReportKind.READY)]
        self.board.report(ticket_id, 'failed', summary)
        if failure_class and self.board.schedule_retry(
                ticket_id, failure_class, self.auto_retries, self.retry_backoff) == "retry scheduled":
            self.board.log(f"auto-retry {ticket_id} ({failure_class})")

    async def _verify_pending(self):
        for ticket in self.board.tickets.values():
            if ticket.stopped or ticket.verification != "pending":
                continue
            if ticket.inputs:
                ticket.verification = "not_requested"
                continue
            if ticket.id in self._active:
                continue  # another assignment still mutating this directory
            async with self._verify_lock:
                if ticket.verification != "pending" or ticket.id in self._active:
                    continue
                if not ticket.mandate.still_valid(self.board.policy):
                    self.board.finish_verification(ticket.id, "failed", ["Mandate revoked."])
                    continue
                if not self.verify_commands:
                    self.board.finish_verification(ticket.id, "review", ["No host-configured checks."])
                    continue
                evidence = []
                try:
                    for command in self.verify_commands:
                        task = asyncio.create_task(self.execution.execute(
                            ticket.directory, ticket.directory, command, 120))
                        self._active[ticket.id] = task
                        output = await asyncio.wait_for(task, 130)
                        evidence.append(f"$ {command}\n{output}")
                        if ticket.stopped or ticket.inputs or ticket.verification != "pending":
                            break
                        if not output.startswith("Exit code: 0\n"):
                            self.board.finish_verification(ticket.id, "failed", evidence)
                            self.board.schedule_retry(ticket.id, "verification",
                                                      self.auto_retries, self.retry_backoff)
                            break
                    else:
                        self.board.finish_verification(ticket.id, "passed", evidence)
                except asyncio.CancelledError:
                    if not ticket.stopped:
                        raise
                except Exception as error:
                    self.board.finish_verification(ticket.id, "failed", [*evidence, str(error)])
                    self.board.schedule_retry(ticket.id, "verification",
                                              self.auto_retries, self.retry_backoff)
                finally:
                    self._active.pop(ticket.id, None)
                    self.checkpoint()

    def review_decision(self, ticket_id: str, approved: bool) -> str:
        ticket = self.board.tickets.get(ticket_id)
        if ticket is None or ticket.verification != "review":
            return "Error: ticket is not awaiting manual verification."
        return self.board.finish_verification(
            ticket_id, "passed" if approved else "failed",
            ["Explicit operator review: " + ("approved" if approved else "rejected")])

    async def _relay_notices(self):
        notices = list(self.board.notices)
        if not notices:
            return
        try:
            await asyncio.wait_for(self.router.relay(notices), self.router_timeout)
        except Exception as error:
            self.board.log(f'relay failed: {type(error).__name__}: {error}')
        else:
            del self.board.notices[:len(notices)]
        finally:
            self.checkpoint()
