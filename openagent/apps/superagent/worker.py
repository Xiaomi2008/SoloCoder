"""The worker: one isolated Executor session per Ticket, in its own directory.

A worker owns the technical work. It never talks to a person directly; it
acknowledges once, reports, and the orchestrator relays. It never sees or names
its own ticket id: the host bound it when it built these tools.
"""

from __future__ import annotations

from pathlib import Path
import re

from openagent.core.agent import Agent
from openagent.core.tool import ToolContext, tool
from openagent.runtime.loop import COMPACTION_KEEP_FRACTION, PAGE_SCREENSHOT_FOLLOWUP_TEXT, drop_old_screenshots
from openagent.tools import edit, glob, grep, read, write

from .page_check import PROMPT as PAGE_PROMPT
from .page_check import PageCheck

from .execution import Execution
from . import sessions

from .domain import Board, Input, Ticket, compute_stage

WORKER_PROMPT = """\
You are a worker. Own the technical work in your ticket directory: read the
context, assess the scope, choose an approach and verify the result. You never
talk to a person directly; the orchestrator speaks for you.

Your authority is the ticket's mandate, shown in the turn. Do not expand it. If
the request is vague, the direction is unresolved, or inspection shows the work
needs a design decision, report dropped instead of guessing.

Before substantial work, call ack once with a brief acknowledgement naming the
object and the intended result. Then work quietly.

You do not merge pull requests and you do not enable auto-merge. Use bash for
the repository's own commands.

Finish every turn with report: progress while you are still working, waiting
when you have no more to do and nothing is pending, blocked only when you truly
cannot proceed, failed when the attempt broke, dropped when there is nothing
for a person to do, and ready only when the implementation is reviewed and
verified -- a draft is blocked, not ready. A ready report requests host verification;
it does not mean completion. Never claim host verification passed yourself. Distinguish completed work from an
incomplete draft. Ask one concrete question at a time: pass it as question and
the ticket waits for the answer.

Shell commands have bounded timeouts and are cancelled with your assignment.
Keep them short. Your working directory is not an OS sandbox; do not access
files outside your ticket directory."""


def format_turn(ticket: Ticket, inputs: list[Input]) -> str:
    """What the worker wakes up to: its mandate, its directory, its new messages."""
    mandate = ticket.mandate
    messages = "\n\n".join(f"[{i.trigger_id}] {i.text}" for i in inputs)
    return (
        f"Ticket {ticket.id} -- {ticket.title}\n"
        f"Directory: {ticket.directory}\n"
        f"Scope: {mandate.scope}\n"
        f"Rules: {', '.join(mandate.rules) if mandate.rules else 'none'}\n"
        f"Stage: {compute_stage(ticket)}\n"
        f"Pull request: {ticket.pr or 'none'}\n"
        f"Reports so far: {len(ticket.reports)}\n"
        "\n"
        f"New messages:\n{messages}\n"
        "\nWork on this and report when you stop."
    )


# Models sometimes emit fullwidth backslashes (U+FF5C '＼', U+FF3F '＿') and
# fullwidth angle brackets instead of ASCII. Normalize before matching.
_LOOKALIKES = str.maketrans({
    '\uFF0F': '/',
    '\uFF3C': '\\',
    '\uFF5C': '\\',
    '\uFF3F': '_',
    '\uFF1C': '<',
    '\uFF1E': '>',
})


def normalize_tool_text(text: str) -> str:
    return text.translate(_LOOKALIKES)


# Some providers leak control tokens into assistant text, e.g.
# "<\\DSML\\ invoke name=\"write\">" or "<tool_use>...</tool_use>". Rather than
# enumerating every leak shape, treat any tag whose payload names a real tool as
# a printed-instead-of-invoked call. Prose rarely contains such a tag.
_TOOL_NAME = r'(?:invoke|tool_use|tool_call|function_call|write|edit|read|bash|glob|grep|ack|report|subscribe|route|reply|tickets|page_\w+|desktop_\w+)'
PSEUDO_TOOL_CALL_PATTERN = re.compile(
    r'<[^<>]{0,80}\b' + _TOOL_NAME + r'\b[^<>]*>'
    r'|<\|\|?[^|>\n]{0,40}(?:call|tool)[^|>\n]{0,40}\|>'
    r'|DSML',
    re.I | re.S)

PSEUDO_TOOL_RETRY = (
    "Your previous message contained a tool call written as plain text instead "
    "of a real tool call. Do not print XML or JSON for tool calls. Emit actual "
    "tool calls through the tool interface, one step at a time, then continue."
)


def repair_textual_tool_calls(agent) -> bool:
    """Detect a model that printed its tool call instead of invoking one.

    Some models emit ``<tool_use>``/``<path>`` XML as ordinary text. The Agent
    loop treats that as a final answer, so the Worker would end with no report
    and no file written. Appending a corrective system message makes the next
    call a real tool call. Returns True when a repair was appended.
    """
    if not agent.session.messages:
        return False
    last = agent.session.messages[-1]
    if last.role != 'assistant' or last.has_tool_calls:
        return False
    text = last.text or ''
    if not PSEUDO_TOOL_CALL_PATTERN.search(normalize_tool_text(text)):
        return False
    if agent.session.messages[-1].text == PSEUDO_TOOL_RETRY:
        return False
    agent.session.add('system', PSEUDO_TOOL_RETRY)
    return True


def is_assignment(message) -> bool:
    """The card ``format_turn`` wrote, not a follow-up the host added later."""
    return message.role == 'user' and message.text.startswith('Ticket ') and '\nDirectory: ' in message.text


def turn_warning(turns_left: int) -> str:
    """What a Worker is told near its limit, so that it stops in time to report."""
    return (
        f"You have {max(turns_left, 0)} turns left in this assignment. Stop testing and call report unless "
        "you already have: ready only if the work is verified, otherwise blocked, saying what works, what "
        "does not and what you did not test. A person sees nothing of this work without a report."
    )


class TicketAgent(Agent):
    page: PageCheck | None = None

    @property
    def turns_left(self) -> int:
        return self.max_turns - self.completed_turns

    def _compaction_options(self, settings, tool_defs) -> dict:
        # The assignment card stays word for word: it holds the mandate, and the
        # newest user message may only be a screenshot note or a limit warning.
        return {
            'keep_tokens': int(settings.max_context_tokens * COMPACTION_KEEP_FRACTION),
            'tools': tool_defs,
            'is_prompt': is_assignment,
        }

    def _add_tool_result_followup_messages(self, results) -> bool:
        added = super()._add_tool_result_followup_messages(results)
        # Twice, because a model deep in a test loop can read past the first. At
        # zero the loop still grants one turn, which is enough for the report.
        left = self.turns_left
        warning = turn_warning(left) if left == 3 or left <= 1 else None
        # Held by the Worker's own page, not in a module global, so concurrent
        # tickets cannot attach each other's screenshot.
        capture = self.page.capture if self.page is not None else None
        if capture and any(getattr(result, 'tool_name', None) == 'page_screenshot' for result in results):
            self.page.capture = None
            text = PAGE_SCREENSHOT_FOLLOWUP_TEXT + (f"\n\n{warning}" if warning else "")
            self.session.add_user_multimodal(text=text, image_data=capture, image_mime_type='image/jpeg')
            drop_old_screenshots(self.session)
            return True
        if warning:
            self.session.add('user', warning)
        return added

    def _finish_after_tools(self) -> bool:
        # The Worker never "finishes" on its own: a ticket is done only when the
        # host verification gate accepts a report(ready). Ending early would
        # silently drop work, so keep the loop going until the turn limit.
        return False

    def _repair_textual_tool_call(self) -> bool:
        return repair_textual_tool_calls(self)

    @property
    def tool_context(self) -> ToolContext:
        return ToolContext(working_dir=self.working_dir, enforce_scope=True)


class SuperAgentWorker:
    """One ticket's Executor: a directory, a session, a bounded tool set."""

    def __init__(
        self,
        provider: object,
        board: Board,
        ticket_id: str,
        *,
        max_turns: int = 12,
        execution=None,
        heartbeat_timeout: float = 120,
        page_check: bool = False,
        desktop_apps: tuple[str, ...] = (),
    ) -> None:
        self.execution = execution or Execution()
        self.desktop_apps = tuple(desktop_apps)
        self.heartbeat_timeout = heartbeat_timeout
        self.provider = provider
        self.board = board
        self.ticket_id = ticket_id
        directory = Path(board.tickets[ticket_id].directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.page = PageCheck(directory, heartbeat=lambda note: board.heartbeat(ticket_id, note)) if page_check else None
        prompt = WORKER_PROMPT
        if self.page is not None:
            prompt += "\n\n" + PAGE_PROMPT
        if self.desktop_apps:
            prompt += f"\n\nThe desktop tools control the real desktop, and only these apps: {', '.join(self.desktop_apps)}."
        self._agent = TicketAgent(
            provider=provider,
            system_prompt=prompt,
            tools=self._tools(),
            working_dir=str(directory),
            max_turns=max_turns,
        )
        self._agent.page = self.page
        self.session_path = Path(board.workspace) / "sessions" / f"{ticket_id}.json"
        sessions.restore(self._agent, self.session_path)
        self._agent.provider = sessions.CheckpointProvider(
            provider, self._agent, self.session_path,
            heartbeat=lambda note: board.heartbeat(ticket_id, note))

    def _tools(self) -> list:
        board = self.board
        ticket_id = self.ticket_id

        @tool
        async def bash(command: str, timeout: float = 30, working_dir: str = "") -> str:
            """Run a shell command inside the ticket directory.

            Args:
                command: Shell command to execute. No background services.
                timeout: Maximum seconds, greater than zero and at most 120.
                working_dir: Optional subdirectory of this ticket.
            """
            ticket = board.tickets[ticket_id]
            if ticket.stopped or not ticket.mandate.still_valid(board.policy):
                return "Error: ticket stopped or mandate revoked."
            board.heartbeat(ticket_id, f"Running shell: {command[:120]}")
            try:
                result = await self.execution.run(ticket_id, ticket.directory, command, timeout, working_dir,
                    check=lambda: not ticket.stopped and ticket.mandate.still_valid(board.policy))
            finally:
                board.heartbeat(ticket_id, "Shell returned")
            return result

        @tool
        def ack(text: str) -> str:
            """Acknowledge the request once, before substantial work.

            Args:
                text: At most two sentences naming the object and the intended result.

            Returns:
                Whether the host delivered it.
            """
            return board.ack(ticket_id, text)

        @tool
        def report(
            kind: str,
            summary: str = "",
            pr: str = "",
            question: str = "",
        ) -> str:
            """Report where this ticket stands. Every turn ends with one.

            Args:
                kind: progress, waiting, ready, blocked, failed or dropped.
                summary: What was done or found, in a few sentences.
                pr: The pull request URL, when one exists.
                question: For a decision you need from a person, one concrete question.

            Returns:
                Whether the host recorded it.
            """
            return board.report(
                ticket_id, kind, summary, pr=pr, question=question
            )

        @tool
        def subscribe(thread_id: str, reason: str = "") -> str:
            """Follow a related thread for context. It lends no authority.

            Args:
                thread_id: The thread to follow.
                reason: One sentence on why it is relevant.

            Returns:
                Whether the subscription was recorded.
            """
            return board.subscribe(ticket_id, thread_id, reason)

        tools = [read, write, edit, glob, grep, bash, ack, report, subscribe]
        if self.page is not None:
            tools.extend(self.page.tools())
        if self.desktop_apps:
            from openagent.tools.desktop import build_desktop_tools

            tools.extend(build_desktop_tools(self.desktop_apps))
        return tools

    async def run(self, inputs: list[Input]) -> str:
        """One turn: the new messages, then whatever the worker concludes."""
        ticket = self.board.tickets[self.ticket_id]
        before = len(ticket.reports)
        limit = self._agent.max_turns
        try:
            result = await self._agent.run(
                f"{format_turn(ticket, inputs)}\nYou have {limit} turns for this assignment; save the last for report.")
            if self._agent.turns_left <= 0 and len(ticket.reports) == before and not ticket.stopped:
                self.board.report(self.ticket_id, "blocked",
                                  f"Used all {limit} turns without reporting. Inspect the ticket directory and retry.")
            return result
        finally:
            if self.page is not None:
                await self.page.close()
            sessions.save(self._agent, self.session_path)
