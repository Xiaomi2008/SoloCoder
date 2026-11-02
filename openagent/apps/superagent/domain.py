"""The super-agent domain: what the host decides on, not the chat it happens in.

A cut-down transcription of DeepSeek's dsh-superagent-prototype
(docs/001-domain.md): Source -> Trigger -> Thread -> Eval -> Ticket ->
Assignment -> Report. A session is an implementation detail of an Executor; the
unit of work is a Ticket. Concepts the prototype rejects stay rejected here
too: no Principal, no cross-Source identity, no waiting Eval, no Ticket that
follows a session's state.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


def _slug(text: str, limit: int = 32) -> str:
    """A directory-and-branch-friendly name from a human title."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return "-".join(words)[:limit].strip("-") or "work"


def _as_enum(value: Any, enum_cls: type[Enum]) -> Any | None:
    """Accept a member or its string form; None when it is neither."""
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value).strip().lower())
    except ValueError:
        return None


class Strength(str, Enum):
    """How a Trigger was admitted: a person asked, or the host inferred it."""

    EXPLICIT = "explicit"
    INFERRED = "inferred"


class DecisionKind(str, Enum):
    ACCEPT = "accept"
    ATTACH = "attach"
    CLARIFY = "clarify"
    STOP = "stop"
    IGNORE = "ignore"
    UNAVAILABLE = "unavailable"


class ReportKind(str, Enum):
    PROGRESS = "progress"
    WAITING = "waiting"
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"
    DROPPED = "dropped"


class AssignmentState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    ENDED = "ended"


@dataclass(frozen=True)
class Stage:
    """What a person is being asked for, in the panel's own words."""

    zone: str
    label: str

    def __str__(self) -> str:
        return f"{self.zone}/{self.label}"


@dataclass(frozen=True)
class Policy:
    """The versioned assessment rules and who may start work.

    Only what this prototype needs: an accept mode, an allowed list and the
    enabled rules (id -> description). Source parts of the real Policy
    (admission tables, bot lists) are reduced to the single ``admission`` knob
    on the Board.
    """

    version: int = 1
    accept_mode: str = "observe"
    allowed: frozenset[str] = frozenset({"owner"})
    rules: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Mandate:
    """What one accept granted. Immutable; only accept, stop and a failed
    re-check change it, exactly as docs/002-authorization.md says."""

    policy_version: int
    strength: Strength
    scope: str
    source: str
    thread_id: str
    actor: str
    rules: tuple[str, ...] = ()

    def still_valid(self, policy: Policy) -> bool:
        """Whether this Mandate survives a re-check before resuming.

        An explicit Mandate needs its requester still on the list; an inferred
        one needs ``auto`` mode and its matched rules still enabled.
        """
        if self.strength is Strength.EXPLICIT:
            return self.actor in policy.allowed
        return policy.accept_mode == "auto" and all(r in policy.rules for r in self.rules)


@dataclass
class Trigger:
    """One immutable event with its Source metadata."""

    id: str
    thread_id: str
    source: str
    actor: str
    text: str
    addresses_agent: bool
    at: float


@dataclass
class Thread:
    """Triggers in time order; holds its Subscriptions and pending Triggers."""

    id: str
    source: str
    title: str
    triggers: list[Trigger] = field(default_factory=list)
    pending: list[Trigger] = field(default_factory=list)
    subscriptions: set[str] = field(default_factory=set)
    evaluating: bool = False


@dataclass
class Report:
    id: str
    kind: ReportKind
    summary: str
    pr: str | None
    question: str | None
    at: float


@dataclass
class Input:
    """A message delivered to a Ticket's Executor Session."""

    trigger_id: str
    text: str
    at: float


@dataclass
class Assignment:
    """One binding of a Ticket to one Executor turn."""

    id: str
    ticket_id: str
    state: AssignmentState
    attempt: int
    at: float
    ended_at: float | None = None
    inputs: list[Input] = field(default_factory=list)
    last_heartbeat: float = 0.0
    progress: str = ""
    failure_class: str | None = None


@dataclass
class Ticket:
    """The unit of work. It defines its own state, never the session's."""

    id: str
    short_name: str
    title: str
    mandate: Mandate
    directory: str
    origin_thread_ids: tuple[str, ...]
    created_at: float
    assignments: list[Assignment] = field(default_factory=list)
    reports: list[Report] = field(default_factory=list)
    inputs: list[Input] = field(default_factory=list)
    attention: str | None = None
    pr: str | None = None
    ended: bool = False
    stopped: bool = False
    outcome: str | None = None
    acknowledged: bool = False
    verification: str = "not_requested"
    verification_evidence: list[str] = field(default_factory=list)
    repository: str | None = None
    branch: str | None = None
    base_commit: str | None = None
    retry_budget: int = 0
    retry_not_before: float = 0.0
    failure_class: str | None = None
    progress: str = ""


@dataclass
class Decision:
    """The Eval's judgement on a batch of Triggers."""

    id: str
    trigger_ids: tuple[str, ...]
    kind: DecisionKind
    ticket_id: str | None
    title: str
    worktree_name: str
    acknowledgement: str
    reason: str
    strength: Strength | None
    would_accept: bool
    policy_version: int
    at: float
    rules: tuple[str, ...] = ()


@dataclass
class HumanReply:
    """Something a person will see, and nothing else will."""

    ticket_id: str | None
    purpose: str
    text: str
    question: str | None


@dataclass
class Notice:
    """A worker report the orchestrator still has to relay."""

    ticket_id: str
    kind: ReportKind
    text: str


def compute_stage(ticket: Ticket) -> Stage:
    """The Ticket's stage, first match wins (docs/003-execution.md).

    The delivery row is omitted: this prototype delivers Inputs in memory and
    never retries, so a failed delivery cannot be observed.
    """
    if ticket.ended:
        return Stage("ended", f"Ended - {ticket.outcome or 'no outcome'}")
    if ticket.verification == "pending":
        return Stage("working", "Awaiting verification")
    if ticket.verification == "review":
        return Stage("person", "Awaiting verification approval")
    if ticket.attention:
        return Stage("person", "Awaiting answer")
    if ticket.retry_not_before > time.time():
        return Stage("working", f"Retry {ticket.retry_budget} scheduled")
    last = ticket.reports[-1] if ticket.reports else None
    if last is not None and last.kind is ReportKind.READY:
        return Stage("person", "Awaiting review")
    if last is not None and last.kind in (ReportKind.BLOCKED, ReportKind.FAILED):
        unanswered = [i for i in ticket.inputs if i.at > last.at]
        if not unanswered:
            return Stage("person", "Blocked" if last.kind is ReportKind.BLOCKED else "Failed")
    if not ticket.assignments or ticket.assignments[-1].state is AssignmentState.PENDING:
        return Stage("working", "Queued")
    last_assignment = ticket.assignments[-1]
    if (last is not None and last.kind is ReportKind.WAITING) or (
        last_assignment.state is AssignmentState.ENDED
    ):
        return Stage("idle", "Waiting for a Trigger")
    return Stage("working", "Working")


class Board:
    """The host's state: threads, tickets, decisions and what a person saw.

    One object per deployment. The model never holds a reference to it except
    through the closures its tools are built from, so it cannot name a ticket
    it was not given, and a forged id resolves to nothing.
    """

    def __init__(
        self,
        *,
        workspace: str = ".",
        policy: Policy | None = None,
        admission: str = "inferred",
    ) -> None:
        self.workspace = str(Path(workspace).expanduser())
        self.policy = policy or Policy()
        self.admission = admission
        self.threads: dict[str, Thread] = {}
        self.tickets: dict[str, Ticket] = {}
        self.decisions: list[Decision] = []
        self.replies: list[HumanReply] = []
        self.notices: list[Notice] = []
        self.log_lines: list[str] = []
        self._triggers: dict[str, Trigger] = {}
        self._assignments: dict[str, Assignment] = {}
        self._launch: list[str] = []
        self._ids = 0
        self._seq = 0.0
        self._inflight_batches: dict[str, list[Trigger]] = {}
        self.verification_required = True
        self.automatic_retry_limit = 0
        self.retry_backoff = 30.0

    # -- bookkeeping ------------------------------------------------------

    def _new_id(self, prefix: str) -> str:
        self._ids += 1
        return f"{prefix}-{self._ids}"

    def _tick(self) -> float:
        self._seq += 1.0
        return self._seq

    def heartbeat(self, ticket_id: str, progress: str = "") -> str:
        ticket = self.tickets.get(ticket_id)
        if ticket is None or ticket.stopped or not ticket.assignments:
            return "Error: no live ticket."
        ticket.assignments[-1].last_heartbeat = time.time()
        ticket.progress = progress.strip()[:500]
        self.log(f"heartbeat {ticket_id}: {ticket.progress or 'alive'}")
        return "Heartbeat recorded."

    def stall(self, ticket_id: str, reason: str) -> str:
        return self.report(ticket_id, "blocked", reason)

    def schedule_retry(self, ticket_id: str, failure_class: str, limit: int, backoff: float) -> str:
        ticket = self.tickets[ticket_id]
        ticket.failure_class = failure_class
        if failure_class not in ("worker", "verification") or ticket.retry_budget >= limit:
            return "retry refused"
        ticket.retry_budget += 1
        ticket.retry_not_before = time.time() + backoff * 2 ** (ticket.retry_budget - 1)
        ticket.attention = None
        ticket.verification = "not_requested"
        if not ticket.inputs and ticket.assignments:
            ticket.inputs = list(ticket.assignments[-1].inputs)
        if not ticket.inputs:
            ticket.inputs = [Input('auto-retry',
                                   'The previous attempt failed. Inspect current files, '
                                   'avoid repeating external side effects, and resume safely.',
                                   self._tick())]
        if ticket_id not in self._launch:
            self._launch.append(ticket_id)
        self.log(f"scheduled retry {ticket.retry_budget}/{limit} for {ticket_id} in "
                 f"{backoff * 2 ** (ticket.retry_budget - 1):g}s")
        return "retry scheduled"

    def launch_due(self, now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        while self._launch:
            ticket_id = self._launch[0]
            ticket = self.tickets.get(ticket_id)
            if ticket is None or ticket.ended or ticket.stopped:
                self._launch.pop(0)
                continue
            if ticket.retry_not_before > now:
                return None
            self._launch.pop(0)
            return ticket_id
        return None

    def log(self, text: str) -> None:
        self.log_lines.append(text)

    def overview(self) -> str:
        if not self.tickets:
            return "No tickets."
        return "\n".join(
            f"{t.id}  {compute_stage(t)}  {t.short_name}  {t.title}"
            for t in self.tickets.values()
        )

    def take_replies(self) -> list[HumanReply]:
        replies, self.replies = list(self.replies), []
        return replies

    def take_notices(self) -> list[Notice]:
        notices, self.notices = list(self.notices), []
        return notices

    # -- intake -----------------------------------------------------------

    def submit(
        self,
        text: str,
        *,
        actor: str,
        source: str = "cli",
        thread_id: str | None = None,
        addresses_agent: bool = True,
        title: str | None = None,
    ) -> Trigger:
        """Deliver unambiguous follow-ups; route multi-ticket messages selectively."""
        thread = self.threads.get(thread_id) if thread_id else None
        if thread is None:
            first_line = (text.strip().splitlines() or ["untitled"])[0][:80]
            thread = Thread(self._new_id("thread"), source, title or first_line)
            self.threads[thread.id] = thread
        trigger = Trigger(
            self._new_id("trigger"),
            thread.id,
            source,
            actor,
            text.strip(),
            addresses_agent,
            self._tick(),
        )
        thread.triggers.append(trigger)
        self._triggers[trigger.id] = trigger
        self.log(f"trigger {trigger.id} on {thread.id} from {actor}")

        subscribed = [self.tickets[t] for t in sorted(thread.subscriptions) if t in self.tickets]
        # Count all subscriptions, even stopped/revoked tickets. Silently choosing
        # the only runnable ticket could send a message to the wrong task.
        if (len(subscribed) == 1 and thread.id in subscribed[0].origin_thread_ids
                and actor in self.policy.allowed and addresses_agent):
            if self._deliver(subscribed[0], trigger):
                return trigger
        strength = self.gate(trigger)
        if strength is None:
            self.log(f"not admitted: {trigger.id}")
            return trigger
        thread.pending.append(trigger)
        return trigger

    def gate(self, trigger: Trigger) -> Strength | None:
        """Who may start work: explicit request, inferred candidate, or nobody."""
        on_list = trigger.actor in self.policy.allowed
        if trigger.addresses_agent:
            if on_list:
                return Strength.EXPLICIT
            return Strength.INFERRED if self.admission == "inferred" else None
        return Strength.INFERRED if on_list else None

    def next_batch(self) -> tuple[Thread, list[Trigger]] | None:
        """The next Thread with Triggers nobody has assessed yet."""
        for thread in self.threads.values():
            if thread.pending and not thread.evaluating:
                batch = list(thread.pending)
                thread.pending.clear()
                thread.evaluating = True
                self._inflight_batches[thread.id] = batch
                return thread, batch
        return None

    # -- decisions --------------------------------------------------------

    def route(
        self,
        *,
        kind: str,
        trigger_ids: str,
        ticket_id: str = "",
        title: str = "",
        worktree_name: str = "",
        acknowledgement: str = "",
        reason: str = "",
        rules: str = "",
    ) -> str:
        """The orchestrator's decision, applied immediately. One call, one decision."""
        ids = [p for p in re.split(r"[,\s]+", trigger_ids or "") if p]
        if not ids:
            return "Error: trigger_ids is empty; name the candidate trigger id(s) you are deciding on."
        triggers = []
        for tid in ids:
            trigger = self._triggers.get(tid)
            if trigger is None:
                return f"Error: unknown trigger id '{tid}'."
            triggers.append(trigger)
        decision_kind = _as_enum(kind, DecisionKind)
        if decision_kind is None:
            return "Error: kind must be accept, attach, clarify, stop, ignore or unavailable."
        if decision_kind in (DecisionKind.ATTACH, DecisionKind.STOP) and not ticket_id.strip():
            return f"Error: {decision_kind.value} needs ticket_id."
        if decision_kind is DecisionKind.CLARIFY and not reason.strip():
            return "Error: clarify needs a concrete routing question in reason."
        if decision_kind in (DecisionKind.ATTACH, DecisionKind.STOP):
            target = self.tickets.get(ticket_id.strip())
            if target is None:
                return f"Error: no ticket {ticket_id.strip()}."
            if any(t.actor not in self.policy.allowed or not t.addresses_agent for t in triggers):
                return "Error: only an allowed person addressing the agent may attach or stop work."
            if decision_kind is DecisionKind.ATTACH:
                if target.stopped:
                    return "Error: a stopped ticket cannot receive follow-ups."
                if not target.mandate.still_valid(self.policy):
                    return "Error: target ticket mandate revoked; a new accept is required."
        decided = {tid for d in self.decisions for tid in d.trigger_ids}
        if any(t.id in decided for t in triggers):
            return "Error: a trigger in this batch already has a decision."
        strength = self.gate(triggers[0]) if decision_kind is DecisionKind.ACCEPT else None
        effective_kind = decision_kind
        if decision_kind is DecisionKind.ACCEPT and strength is None:
            effective_kind = DecisionKind.IGNORE
        rule_ids = tuple(p for p in re.split(r"[,\s]+", rules or "") if p)
        decision = Decision(
            self._new_id("decision"),
            tuple(t.id for t in triggers),
            effective_kind,
            ticket_id.strip() or None,
            title.strip(),
            worktree_name.strip(),
            acknowledgement.strip(),
            reason.strip(),
            strength,
            False,
            self.policy.version,
            self._tick(),
            rule_ids,
        )
        self.decisions.append(decision)
        ticket = self.apply(decision)
        if decision_kind is DecisionKind.ACCEPT and effective_kind is DecisionKind.IGNORE:
            return (
                f"Refused: {triggers[0].id} was not admitted for accept; recorded as ignore."
            )
        if decision.would_accept:
            return (
                f"Recorded 'would accept' for {triggers[0].id}: observe mode starts nothing."
            )
        if ticket is None:
            return f"Recorded {effective_kind.value} for {', '.join(ids)}."
        return f"{effective_kind.value} -> ticket {ticket.id} '{ticket.short_name}'; now {compute_stage(ticket)}."

    def record_unavailable(self, trigger: Trigger, reason: str) -> None:
        self.decisions.append(
            Decision(
                self._new_id("decision"),
                (trigger.id,),
                DecisionKind.UNAVAILABLE,
                None,
                "",
                "",
                "",
                reason,
                None,
                False,
                self.policy.version,
                self._tick(),
            )
        )
        self.log(f"unavailable: {trigger.id} ({reason})")

    def apply(self, decision: Decision) -> Ticket | None:
        if decision.kind is DecisionKind.CLARIFY:
            self.reply(purpose="notice", text=decision.reason)
            self.log(f"decision {decision.id}: routing clarification requested")
            return None
        if decision.kind is DecisionKind.IGNORE:
            self.log(f"decision {decision.id}: ignore {decision.reason!r}")
            return None
        if decision.kind is DecisionKind.UNAVAILABLE:
            self.log(f"decision {decision.id}: unavailable {decision.reason!r}")
            return None
        if decision.kind is DecisionKind.STOP:
            if decision.ticket_id is None:
                return None
            self.stop(decision.ticket_id)
            return self.tickets.get(decision.ticket_id)
        if decision.kind is DecisionKind.ATTACH:
            ticket = self.tickets.get(decision.ticket_id or "")
            if ticket is None:
                self.log(f"decision {decision.id}: no ticket {decision.ticket_id}")
                return None
            for tid in decision.trigger_ids:
                trigger = self._triggers[tid]
                self._subscribe(ticket, trigger.thread_id)
                self._deliver(ticket, trigger)
                if decision.reason:
                    for item in ticket.inputs:
                        if item.trigger_id == trigger.id:
                            item.text += f"\nRouting context: {decision.reason}"
                            break
            return ticket
        if decision.strength is Strength.INFERRED:
            if self.policy.accept_mode == "observe":
                decision.would_accept = True
                self.log(f"decision {decision.id}: would accept (observe mode)")
                return None
            if self.policy.accept_mode != "auto":
                self.log(f"decision {decision.id}: inferred accept refused (mode {self.policy.accept_mode})")
                return None
        return self._accept(decision)

    def _accept(self, decision: Decision) -> Ticket:
        first = self._triggers[decision.trigger_ids[0]]
        short = _slug(decision.worktree_name or decision.title or first.text)
        mandate = Mandate(
            self.policy.version,
            decision.strength or Strength.EXPLICIT,
            "explicit request" if decision.strength is Strength.EXPLICIT else "inferred candidate",
            first.source,
            first.thread_id,
            first.actor,
            decision.rules,
        )
        origin = tuple(sorted({self._triggers[t].thread_id for t in decision.trigger_ids}))
        ticket_id = self._new_id("ticket")
        # Keep the first readable name, but never share an existing directory.
        used = {t.directory for t in self.tickets.values()}
        directory = str(Path(self.workspace) / "work" / short)
        if directory in used or Path(directory).exists():
            directory += "-" + ticket_id
        ticket = Ticket(
            ticket_id,
            short,
            decision.title or first.text[:80],
            mandate,
            directory,
            origin,
            self._tick(),
        )
        self.tickets[ticket.id] = ticket
        for thread_id in origin:
            self._subscribe(ticket, thread_id)
        for tid in decision.trigger_ids:
            trigger = self._triggers[tid]
            ticket.inputs.append(Input(trigger.id, trigger.text, trigger.at))
        if decision.acknowledgement:
            self.ack(ticket.id, decision.acknowledgement)
        self._launch.append(ticket.id)
        self.log(f"ticket {ticket.id} accepted: {ticket.title!r} in {ticket.directory}")
        return ticket

    def stop(self, ticket_id: str) -> str:
        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            return f"Error: no ticket {ticket_id}."
        if ticket.stopped:
            return f"Ticket {ticket_id} was already stopped."
        ticket.stopped = True
        self._end(ticket, "stopped by a person")
        return f"Stopped {ticket_id}. Its worker receives nothing more; new work needs a new accept."

    # -- subscriptions and delivery ---------------------------------------

    def _subscribe(self, ticket: Ticket, thread_id: str) -> None:
        thread = self.threads.get(thread_id)
        if thread is None:
            return
        thread.subscriptions.add(ticket.id)
        self.log(f"ticket {ticket.id} subscribed to {thread_id}")

    def subscribe(self, ticket_id: str, thread_id: str, reason: str = "") -> str:
        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            return f"Error: no ticket {ticket_id}."
        if thread_id not in self.threads:
            return f"Error: no thread {thread_id}."
        if thread_id in ticket.origin_thread_ids:
            return "Already subscribed: that is the thread this ticket started on."
        self._subscribe(ticket, thread_id)
        self.log(f"ticket {ticket.id} follows {thread_id} as context ({reason})")
        return f"Subscribed {ticket_id} to {thread_id} as context."

    def _deliver(self, ticket: Ticket, trigger: Trigger) -> bool:
        if ticket.stopped or not ticket.mandate.still_valid(self.policy):
            return False
        if any(i.trigger_id == trigger.id for i in ticket.inputs):
            return True
        if ticket.ended and not self._reopen(ticket):
            return False
        ticket.attention = None
        ticket.verification = "not_requested"
        ticket.inputs.append(Input(trigger.id, trigger.text, trigger.at))
        self._launch.append(ticket.id)
        self.log(f"input {trigger.id} delivered to {ticket.id}")
        return True

    def _reopen(self, ticket: Ticket) -> bool:
        if ticket.stopped:
            return False
        if not ticket.mandate.still_valid(self.policy):
            self.log(f"mandate of {ticket.id} revoked; the message needs a new accept")
            return False
        ticket.ended = False
        ticket.outcome = None
        self.log(f"ticket {ticket.id} resumed under its mandate")
        return True

    def _end(self, ticket: Ticket, outcome: str) -> None:
        ticket.ended = True
        ticket.outcome = outcome
        self._launch = [tid for tid in self._launch if tid != ticket.id]
        self.log(f"ticket {ticket.id} ended: {outcome}")

    # -- assignments and reports ------------------------------------------

    def next_launch(self) -> str | None:
        while self._launch:
            ticket_id = self._launch.pop(0)
            ticket = self.tickets.get(ticket_id)
            if ticket is None or ticket.ended or ticket.stopped:
                continue
            return ticket_id
        return None

    def take_inputs(self, ticket_id: str) -> list[Input]:
        ticket = self.tickets[ticket_id]
        inputs, ticket.inputs = list(ticket.inputs), []
        return inputs

    def start_assignment(self, ticket_id: str) -> Assignment:
        ticket = self.tickets[ticket_id]
        assignment = Assignment(
            self._new_id("assignment"),
            ticket_id,
            AssignmentState.RUNNING,
            len(ticket.assignments) + 1,
            self._tick(),
        )
        assignment.inputs = self.take_inputs(ticket_id)
        assignment.last_heartbeat = time.time()
        ticket.assignments.append(assignment)
        self._assignments[assignment.id] = assignment
        self.log(f"assignment {assignment.id} running for {ticket_id}")
        return assignment

    def finish_assignment(self, assignment_id: str) -> None:
        assignment = self._assignments.get(assignment_id)
        if assignment is None:
            return
        assignment.state = AssignmentState.ENDED
        assignment.ended_at = self._tick()
        self.log(f"assignment {assignment.id} ended")

    def ack(self, ticket_id: str, text: str) -> str:
        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            return f"Error: no ticket {ticket_id}."
        if ticket.acknowledged:
            return "Already acknowledged; no duplicate delivered."
        if not text.strip():
            return "Error: an acknowledgement needs text."
        ticket.acknowledged = True
        self.replies.append(HumanReply(ticket.id, "ack", text.strip(), None))
        self.log(f"ack for {ticket.id}: {text.strip()}")
        return "Delivered."

    def report(
        self,
        ticket_id: str,
        kind: str | ReportKind,
        summary: str = "",
        *,
        pr: str | None = None,
        question: str | None = None,
    ) -> str:
        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            return f"Error: no ticket {ticket_id}."
        if ticket.stopped:
            return "Error: a person stopped this ticket; report nothing."
        report_kind = _as_enum(kind, ReportKind)
        if report_kind is None:
            return "Error: kind must be progress, waiting, ready, blocked, failed or dropped."
        if report_kind is not ReportKind.READY and ticket.verification in ("pending", "review"):
            ticket.verification = "failed" if report_kind in (ReportKind.FAILED, ReportKind.BLOCKED) else "not_requested"
        report = Report(
            self._new_id("report"),
            report_kind,
            summary.strip(),
            (pr or "").strip() or None,
            (question or "").strip() or None,
            self._tick(),
        )
        ticket.reports.append(report)
        ticket.attention = report.question
        self.log(f"report {report.id}: {ticket.id} {report_kind.value}")
        if report_kind is ReportKind.READY:
            if self.verification_required:
                ticket.verification = "pending"
                if report.pr:
                    ticket.pr = report.pr
                return f"Ready request recorded for {ticket_id}; host verification required."
            if report.pr:
                ticket.pr = report.pr
            else:
                self._end(ticket, "completed")
        elif report_kind is ReportKind.DROPPED:
            self._end(ticket, "dropped")
        if report_kind in (ReportKind.READY, ReportKind.BLOCKED, ReportKind.FAILED, ReportKind.DROPPED) or report.question:
            text = f"{ticket.id} '{ticket.title}' reported {report_kind.value}: {report.summary}"
            if report.question:
                text += f" | question: {report.question}"
            if report.pr:
                text += f" | PR {report.pr}"
            self.notices.append(Notice(ticket.id, report_kind, text))
        return f"Reported {report_kind.value} for {ticket_id}."

    def finish_verification(self, ticket_id: str, status: str, evidence: list[str]) -> str:
        ticket = self.tickets[ticket_id]
        if ticket.stopped or ticket.verification not in ("pending", "review"):
            return "Error: no pending verification for this ticket."
        if ticket.inputs and status == "passed":
            return "Error: new inputs invalidate verification; finish the follow-up first."
        if status == "passed" and not ticket.mandate.still_valid(self.policy):
            return "Error: mandate revoked."
        if status not in ("passed", "failed", "review"):
            return "Error: invalid verification status."
        ticket.verification = status
        ticket.verification_evidence = list(evidence)
        if status == "passed":
            if not ticket.pr:
                self._end(ticket, "completed")
            ready = next((r for r in reversed(ticket.reports) if r.kind is ReportKind.READY), None)
            summary = (f"{ticket_id} '{ticket.title}' passed host verification. "
                       + (ready.summary if ready else "") + "\n" + "\n".join(evidence))
            self.notices.append(Notice(ticket_id, ReportKind.READY, summary))
        elif status == "failed":
            self.report(ticket_id, "blocked", "Host verification failed: " + "\n".join(evidence))
        else:
            self.notices.append(Notice(ticket_id, ReportKind.BLOCKED,
                                       f"{ticket_id} '{ticket.title}': no automated checks configured. "
                                       f"Human review required; use :approve {ticket_id} or :reject {ticket_id}."))
        self.log(f"verification {ticket_id}: {status}")
        return f"Verification {status} for {ticket_id}."

    # -- what a person sees ----------------------------------------------

    def reply(
        self,
        *,
        purpose: str,
        text: str,
        ticket_id: str = "",
        question: str = "",
    ) -> str:
        purpose = (purpose or "").strip().lower()
        if purpose not in ("ack", "result", "attention", "notice"):
            return "Error: purpose must be ack, result, attention or notice."
        text = text.strip()
        if not text:
            return "Error: reply needs text."
        words = text.split()
        truncated = len(words) > 100
        if truncated:
            text = " ".join(words[:100]) + " ..."
        if purpose == "attention" and not question.strip():
            return "Error: attention needs the exact question the ticket asked."
        if purpose == "ack" and ticket_id:
            return self.ack(ticket_id, text)
        if any(r.purpose == purpose and r.ticket_id == (ticket_id.strip() or None) and r.text == text
               for r in self.replies):
            return "Already delivered."
        self.replies.append(HumanReply(ticket_id.strip() or None, purpose, text, question.strip() or None))
        self.log(f"reply {purpose} for {ticket_id or '(no ticket)'}: {text}")
        return "Delivered (truncated to 100 words)." if truncated else "Delivered."
