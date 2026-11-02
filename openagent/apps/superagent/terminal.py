"""Small, dependency-free terminal views; no full-screen takeover or cursor state."""
from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap

from .domain import Board, HumanReply, compute_stage

# Strip terminal controls from model/user text; only this renderer emits ANSI.
_CONTROL = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))|[\x00-\x08\x0b-\x1f\x7f]')


def clean(value) -> str:
    return _CONTROL.sub('', str(value))


class Terminal:
    COLORS = {'accent': '96', 'good': '92', 'warning': '93', 'error': '91', 'muted': '2', 'title': '1'}

    def __init__(self, stream=None, *, width: int | None = None, color: bool | None = None):
        self.stream = stream if stream is not None else sys.stdout
        self._width = width
        self.color = (self.stream.isatty() and 'NO_COLOR' not in os.environ
                      and os.environ.get('TERM') != 'dumb') if color is None else color

    @property
    def width(self) -> int:
        return max(24, min(110, self._width or shutil.get_terminal_size((90, 24)).columns))

    def style(self, text: str, kind: str) -> str:
        return f'\033[{self.COLORS[kind]}m{text}\033[0m' if self.color else text

    def line(self, text=''):
        print(text, file=self.stream, flush=True)

    def paragraph(self, text, *, indent='  ', kind=None):
        for part in clean(text).expandtabs(4).splitlines() or ['']:
            for line in textwrap.wrap(part, width=self.width - len(indent),
                                      replace_whitespace=False) or ['']:
                self.line(indent + (self.style(line, kind) if kind else line))

    def heading(self, label):
        self.line()
        self.line(self.style(clean(label).upper(), 'accent'))
        self.line(self.style('─' * self.width, 'muted'))

    def message(self, text):
        value = clean(text)
        self.paragraph(value, kind='error' if value.startswith('Error:') else 'muted')

    def banner(self, runtime, args):
        self.heading('SUPER AGENT  /  coding workspace')
        self.paragraph(f'{args.provider} / {runtime.router.provider.model}', kind='title')
        self.paragraph(runtime.board.workspace, kind='muted')
        self.paragraph(f'Inferred accepts: {runtime.board.policy.accept_mode}  ·  '
                       f'Worker deadline: {runtime.assignment_timeout:g}s')
        execution = getattr(runtime, 'execution', None)
        if execution is not None:
            self.paragraph(f'Shell: {execution.mode} · Backend: {execution.sandbox}', kind='warning')
        self.paragraph('State saved locally · Host shell is NOT sandboxed', kind='warning')
        self.paragraph(f"Slots: {getattr(runtime, 'max_concurrent', 1)}  ·  "
                       f"Heartbeat: {getattr(runtime, 'heartbeat_timeout', 0):g}s  ·  "
                       f"Auto-retries: {getattr(runtime, 'auto_retries', 0)}", kind='muted')
        self.dashboard(runtime.board)
        self.paragraph(':help commands  ·  :tickets jobs  ·  :threads conversations', kind='muted')
        self.line()

    def dashboard(self, board: Board):
        counts = {'working': 0, 'person': 0, 'idle': 0, 'ended': 0}
        for ticket in board.tickets.values():
            counts[compute_stage(ticket).zone] += 1
        self.paragraph(f'{len(board.tickets)} tickets  /  {len(board.threads)} threads', kind='title')
        self.paragraph(f"Working {counts['working']}  ·  Needs you {counts['person']}  ·  "
                       f"Idle {counts['idle']}  ·  Ended {counts['ended']}")

    def tickets(self, board: Board):
        self.heading('Tickets / jobs')
        if not board.tickets:
            self.paragraph('No tickets yet. Type a request to start one.', kind='muted')
        for ticket in board.tickets.values():
            stage = compute_stage(ticket)
            kind = 'warning' if stage.zone == 'person' else 'good' if ticket.outcome == 'completed' else 'accent'
            self.paragraph(f'{ticket.id}  [{stage.label}]', kind=kind)
            self.paragraph(ticket.title, indent='    ')
        self.line()

    def threads(self, board: Board, selected: str | None):
        self.heading('Threads / conversations')
        if not board.threads:
            self.paragraph('No conversations yet.', kind='muted')
        for thread in board.threads.values():
            self.paragraph(f'{">" if selected == thread.id else " "} {thread.id}  {thread.title}', kind='title')
            targets = ', '.join(sorted(thread.subscriptions)) or 'no tickets'
            self.paragraph(f'{len(thread.triggers)} messages → {targets}', indent='    ', kind='muted')
        self.line()

    def ticket(self, board: Board, ticket_id: str):
        ticket = board.tickets.get(ticket_id)
        if ticket is None:
            self.message('Error: unknown ticket. Use :tickets to see IDs.')
            return
        self.heading(f'{ticket.id} / {ticket.title}')
        self.paragraph(f'Status: {compute_stage(ticket)}', kind='title')
        self.paragraph(f'Directory: {ticket.directory}')
        self.paragraph(f'Origin threads: {", ".join(ticket.origin_thread_ids)}')
        self.paragraph(f'Attempts: {len(ticket.assignments)}  ·  Queued inputs: {len(ticket.inputs)}')
        if ticket.progress:
            self.paragraph(f'Last progress: {ticket.progress}')
        if ticket.retry_budget:
            self.paragraph(f'Automatic retries used: {ticket.retry_budget}')
        self.paragraph(f'Verification: {ticket.verification}')
        for evidence in ticket.verification_evidence:
            self.paragraph(evidence, kind='muted')
        if ticket.branch:
            self.paragraph(f'Branch: {ticket.branch}')
        if ticket.attention:
            self.paragraph(f'Question: {ticket.attention}', kind='warning')
        if ticket.reports:
            report = ticket.reports[-1]
            self.paragraph(f'Last report / {report.kind.value}: {report.summary}')
        self.line()

    def reply(self, reply: HumanReply):
        labels = {'ack': ('ACCEPTED', 'accent'), 'result': ('RESULT', 'good'),
                  'attention': ('NEEDS YOU', 'warning'), 'notice': ('NOTICE', 'warning')}
        label, kind = labels.get(reply.purpose, (reply.purpose.upper(), 'muted'))
        self.line()
        self.line(self.style(f'  {label}  /  {clean(reply.ticket_id or "workspace")}', kind))
        self.paragraph(reply.text)
        if reply.question:
            self.paragraph(f'? {reply.question}', kind='warning')
        self.line()

    def help(self):
        self.heading('Commands')
        for command, meaning in [
            (':status', 'Workspace summary'),
            (':tickets', 'List jobs and stages'),
            (':ticket <id>', 'Inspect a job, directory, and latest report'),
            (':threads', 'List conversations and their connected jobs'),
            (':thread <id>', 'Send subsequent messages to that conversation'),
            (':thread', 'Use a new conversation for each subsequent request'),
            (':stop <id>', 'Permanently stop a job and cancel its execution'),
            (':retry <id>', 'Retry unfinished work after inspecting its files'),
            (':allow <exec-id> / :deny <exec-id>', 'Approve or deny one exact shell command'),
            (':approve <id> / :reject <id>', 'Manual verification decision when checks are absent'),
            (':diff <id>', 'Review ticket worktree changes; never merges'),
            (':log', 'Activity history'),
            (':drain', 'Wait for queued work to finish'),
            (':quit', 'Save and exit; interrupt active work'),
        ]:
            self.paragraph(command, kind='accent')
            self.paragraph(meaning, indent='    ', kind='muted')
        self.line()

    @staticmethod
    def prompt(thread_id: str | None) -> str:
        # Keep readline's prompt free of escape sequences.
        return f'you [{thread_id or "new thread"}] > '
