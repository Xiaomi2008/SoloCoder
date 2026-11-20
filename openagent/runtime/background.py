"""Tool calls that outlive the turn that made them.

A shell command can take an hour, and while the turn waits on it the person
cannot type. So a call still going after ``BACKGROUND_AFTER`` seconds becomes a
job: the model gets a placeholder result, the run goes on and ends, and the job
keeps running. Its real result reaches the model as a message when the session
next has a turn, and the client starts one for it.

Jobs belong to the service, one registry per session, because runs come and go
while they run. They do not survive the client that started them.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from openagent.core.tool import ToolProgress
from openagent.core.types import ToolResultBlock, ToolUseBlock
from openagent.runtime.loop import BACKGROUND_NOTICE_TEXT, next_id

BACKGROUND_AFTER = 25.0

#: Only these move. A browser or desktop action left running would fight the
#: model's next action for the one page or screen there is, so those still block.
BACKGROUND_TOOLS = frozenset({"bash"})

#: The arguments ``bash`` takes, kept when a call is redirected to it.
_BASH_ARGUMENTS = ("command", "timeout", "working_dir")


def background_now(call: ToolUseBlock) -> ToolUseBlock | None:
    """The ``bash`` call to adopt at once, for a call that asked for the background.

    ``bash_background`` and ``bash(background=true)`` skip the wait and become
    jobs like any other, so they call the agent back. ``bash_background`` used to
    start a shell only ``bash_output`` could read: a model that said it would
    come back when its command finished was never woken to do so.
    """
    asked = call.name == "bash_background" or (
        call.name == "bash" and str(call.arguments.get("background")).lower() == "true"
    )
    if not asked:
        return None
    arguments = {key: value for key, value in call.arguments.items() if key in _BASH_ARGUMENTS}
    return replace(call, name="bash", arguments=arguments)

#: What the model and the client are sent of a finished job's output: the end,
#: where a build or a test run says how it went.
MAX_RESULT_CHARS = 20_000
#: Finished jobs kept for ``bash_output`` to report on.
KEEP_FINISHED = 20


def _duration(seconds: float) -> str:
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m {rest}s" if minutes else f"{rest}s"


def _tail(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"[…{len(text) - limit:,} earlier characters cut]\n{text[-limit:]}"


@dataclass
class BackgroundJob:
    id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    progress: ToolProgress
    task: asyncio.Task
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float | None = None
    #: "running", then "completed", "failed" or "cancelled".
    state: str = "running"
    output: str = ""

    @property
    def label(self) -> str:
        """The command, on one line: how the person and the model know it."""
        text = str(self.arguments.get("command") or self.tool_name).strip()
        first = text.splitlines()[0] if text else self.tool_name
        return first if len(first) <= 80 else f"{first[:79]}…"

    @property
    def elapsed(self) -> float:
        return (self.ended_at or time.monotonic()) - self.started_at

    @property
    def wakes(self) -> bool:
        """Does finishing call the agent back? Not when it was told to stop."""
        return self.state in ("completed", "failed")

    def info(self) -> dict[str, Any]:
        return {
            "task_id": self.id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "state": self.state,
            "elapsed": self.elapsed,
        }

    def status(self, tail_lines: int | None = None) -> str:
        """The answer to "how is it going?", for ``bash_output``."""
        verb = "running for" if self.state == "running" else f"{self.state} after"
        text = self.progress.text() if self.state == "running" else self.output
        if tail_lines:
            text = "\n".join(text.splitlines()[-tail_lines:])
        head = f"{self.id} ({self.label}): {verb} {_duration(self.elapsed)}"
        return f"{head}\n{_tail(text)}" if text else f"{head}\n(no output yet)"

    def notice(self) -> str:
        """The message that hands the model this job's result."""
        how = {
            "completed": "finished",
            "failed": "failed",
            "cancelled": "was stopped before it finished",
        }[self.state]
        lines = [f"{BACKGROUND_NOTICE_TEXT} {self.id} ({self.label}) {how} after {_duration(self.elapsed)}."]
        if self.output:
            lines += ["Output:", _tail(self.output)]
        if self.wakes:
            lines.append(
                "Continue the work that was waiting on it. "
                "If nothing is left to do, say so in a sentence."
            )
        return "\n".join(lines)


class BackgroundJobs:
    """One session's jobs, and the results its model has not been told."""

    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id
        self.owner: Any = None
        self.on_finished: Callable[[BackgroundJob], None] | None = None
        self.jobs: dict[str, BackgroundJob] = {}
        self._undelivered: list[BackgroundJob] = []

    def adopt(
        self,
        call: ToolUseBlock,
        task: asyncio.Task,
        progress: ToolProgress,
        run_id: str,
        started_at: float,
    ) -> BackgroundJob:
        job = BackgroundJob(
            id=next_id("bg"),
            run_id=run_id,
            tool_call_id=call.id,
            tool_name=call.name,
            arguments=call.arguments,
            progress=progress,
            task=task,
            started_at=started_at,
        )
        self.jobs[job.id] = job
        task.add_done_callback(lambda done: self._settle(job, done))
        return job

    def _settle(self, job: BackgroundJob, task: asyncio.Task) -> None:
        job.ended_at = time.monotonic()
        result: ToolResultBlock | None = None
        if task.cancelled():
            job.state = "cancelled"
            job.output = job.progress.text()
        elif (error := task.exception()) is not None:
            job.state = "failed"
            job.output = f"{type(error).__name__}: {error}"
        else:
            result = task.result()
            job.state = "failed" if result.is_error else "completed"
            job.output = result.content
        self._undelivered.append(job)
        self._prune()
        if self.on_finished is not None:
            self.on_finished(job)

    def _prune(self) -> None:
        done = [job for job in self.jobs.values() if job.state != "running"]
        for job in done[: max(0, len(done) - KEEP_FINISHED)]:
            self.jobs.pop(job.id, None)

    @property
    def running(self) -> list[BackgroundJob]:
        return [job for job in self.jobs.values() if job.state == "running"]

    @property
    def wake_due(self) -> bool:
        """Is a result waiting that the agent should be called back for?"""
        return any(job.wakes for job in self._undelivered)

    def drain(self) -> list[BackgroundJob]:
        """The finished jobs the model has not heard about, once each."""
        jobs, self._undelivered = self._undelivered, []
        return jobs

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.state != "running":
            return False
        job.progress.kill()
        job.task.cancel()
        return True

    def cancel_all(self) -> None:
        for job in self.running:
            self.cancel(job.id)

    def status(self, job_id: str, tail_lines: int | None = None) -> str:
        if job_id == "all":
            if not self.jobs:
                return "No background tasks in this session."
            return "\n\n".join(job.status(tail_lines or 5) for job in self.jobs.values())
        job = self.jobs.get(job_id)
        return job.status(tail_lines) if job else f"Error: no background task {job_id!r}"


__all__ = [
    "BACKGROUND_AFTER",
    "BACKGROUND_TOOLS",
    "BackgroundJob",
    "BackgroundJobs",
    "background_now",
]
