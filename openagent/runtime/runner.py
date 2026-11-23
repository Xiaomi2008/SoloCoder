"""AgentRunner: the tool-capable run loop, expressed as an event stream.

The repo had two loops. ``RuntimeAgent`` streams events but has no tools;
``core.agent.Agent`` has tools but prints ANSI to stdout and returns a string.
Neither is usable by an app: the Mac shell needs tools *and* events, and it
must not inherit a terminal.

This is the third loop, and the one the service uses. Rules:

- **No printing.** Every observable effect is an event. A renderer (the TUI,
  the SwiftUI view) decides how to show it.
- **The event sequence is the contract.** See
  ``dev_docs/agent-service-protocol.md`` §4 for the ordering guarantees.
- **The session scope is injected, never asked for.** ``tool_context`` goes to
  the tools; the model only writes paths.
- **Permissions fail closed.** A gate that times out, raises, or is missing
  when the mode says "ask" refuses the tool.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from openagent.core.session import Session
from openagent.core.tool import ToolContext, ToolProgress, ToolRegistry, tool, tool_progress
from openagent.core.types import (
    ContentBlock,
    ImageBlock,
    Message,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
)
from openagent.provider.base import BaseProvider
from openagent.providers import (
    ProviderReasoningDelta,
    ProviderError,
    ProviderTextDelta,
    ProviderToolCall,
    ProviderUsage,
)
from openagent.runtime.background import (
    BACKGROUND_AFTER,
    BACKGROUND_TOOLS,
    BackgroundJobs,
    background_now,
)
from openagent.provider.stall import is_stall
from openagent.runtime.events import (
    ReasoningDelta,
    AgentResult,
    BackgroundTasksDelivered,
    SteeringDelivered,
    BackgroundTaskStarted,
    ContextCompactionCompleted,
    ContextCompactionFailed,
    ContextCompactionStarted,
    ContextUsage,
    MessageCompleted,
    MessageDelta,
    MessageFailed,
    MessageStarted,
    MessageStats,
    PermissionRequested,
    PermissionResolved,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
)
from openagent.runtime.clearing import clear_old_tool_outputs
from openagent.runtime.loop import (
    CLEAR_TARGET_FRACTION,
    COMPACTION_KEEP_FRACTION,
    EMPTY_RESPONSE_EXHAUSTED_MESSAGE,
    EmptyResponseGuard,
    LoopSettings,
    add_screenshot_followup,
    add_video_followup,
    compaction_needed,
    drop_images,
    drop_old_videos,
    history_budget,
    is_empty_final_response,
    is_user_prompt,
    next_id,
    refuses_images,
    uses_shared_device,
)
from openagent.runtime.permissions import (
    PermissionDecision,
    PermissionGate,
    PermissionRequest,
    RiskGate,
    call_risk,
    risk_reason,
)

MAX_TURNS_MESSAGE = (
    "The model kept requesting more tool work after the turn limit was reached. "
    "Try again, increase max_turns, or switch to a more reliable model."
)

COMPACTION_REASON = "context approaching limit"
FULL_CONTEXT_REASON = "context window full"
COMMAND_REASON = "requested with /compact"

#: Typed as the whole message, this compacts the conversation and asks the
#: model nothing; the command itself never enters the history.
COMPACT_COMMAND = "/compact"

#: A reply that stalls mid-stream is asked for again this many times in a row.
MAX_STALL_RETRIES = 2


def context_full(usage: ProviderUsage | None, context_window: int | None) -> bool:
    """Did the server spend the whole window? Then an empty reply is no fault
    of the model's: it had a few dozen tokens to think in, and a retry nudge
    only takes more of them."""
    if usage is None or not usage.input_tokens or not context_window:
        return False
    used = usage.input_tokens + (usage.output_tokens or 0)
    return used >= context_window - max(512, context_window // 64)


def _registry_from_tools(
    tools: ToolRegistry | Sequence[Callable[..., Any]] | None,
) -> ToolRegistry:
    """Accept a registry, a list of callables, or nothing."""
    if isinstance(tools, ToolRegistry):
        return tools
    registry = ToolRegistry()
    for fn in tools or ():
        registry.register(fn if hasattr(fn, "_tool_name") else tool(fn))
    return registry


def _requires_prompt(gate: PermissionGate, request: PermissionRequest) -> bool:
    """Does this call need a user round-trip, as opposed to a silent policy answer?

    Only a :class:`RiskGate` asks. A gate that decides by itself
    (``AllowAllGate``, ``ReadOnlyGate``) must not emit a prompt nobody can
    answer, and a cached "allow for the session" must not re-ask.
    """
    if isinstance(gate, RiskGate):
        return gate.requires_prompt(request)
    prompter = getattr(gate, "requires_prompt", None)
    if callable(prompter):
        return bool(prompter(request))
    return False


class AgentRunner:
    """One session, streamed turn by turn, tools included.

    ``stream`` is the whole engine: the service, the TUI, and any test that
    wants to know what an agent does call nothing else.
    """

    SCREENSHOT_TOOL_IMAGE_MIME_TYPE = "image/jpeg"
    SCREENSHOT_FOLLOWUP_MAX_TOKENS = 2048

    def __init__(
        self,
        provider: BaseProvider,
        *,
        session: Session | None = None,
        tools: ToolRegistry | Sequence[Callable[..., Any]] | None = None,
        tool_context: ToolContext | None = None,
        system_prompt: str = "",
        max_turns: int = 100,
        permission_gate: PermissionGate | None = None,
        reasoning_effort: str | None = None,
        context_window: int | None = None,
        max_output_tokens: int | None = None,
        background: BackgroundJobs | None = None,
        background_after: float = BACKGROUND_AFTER,
        device_lock: asyncio.Lock | None = None,
        vision: bool = True,
        video: bool = False,
    ) -> None:
        self.provider = provider
        # `session or Session(...)` is a trap here: Session defines __len__, so
        # a brand-new empty session is falsy and the caller's session would be
        # silently replaced — the service would then save an empty conversation
        # and a reconnect would find nothing. Test: test_runner_preserves_the_callers_session.
        self.session = session if session is not None else Session(system_prompt=system_prompt)
        self.tool_registry = _registry_from_tools(tools)
        self.tool_context = tool_context or ToolContext()
        self.max_turns = max_turns
        self.permission_gate = permission_gate
        # How much the model is allowed to think before answering. Measured on
        # `swift-1.5-iq3_s`: the default produced 686,439 characters of private
        # reasoning and never emitted an answer; "none" answers in 1.1s.
        self.reasoning_effort = reasoning_effort
        # The model's window, which sets when the history is compacted. Without
        # it every model got the 128k default: a 32k local model overflowed
        # before compaction ever ran, and a 500k one was summarised at a fifth
        # of its window.
        self.context_window = context_window
        self.max_output_tokens = max_output_tokens
        #: Whether the model reads images. Cleared by the run itself when the
        #: server refuses one, so the caller can remember it for the next run.
        self.vision = vision
        #: Whether the model reads video: a session's videos go only to one that does.
        self.video = video
        #: Where long tool calls go. None: every call is waited for.
        self.background = background
        self.background_after = background_after
        #: Held from a run's first browser or desktop action until it ends, so
        #: runs side by side do not take turns clicking on the same page.
        self.device_lock = device_lock
        self._holds_device = False
        self._cancel_reason: str | None = None
        #: Messages the person typed while the run was going, for the model's
        #: next turn: (steer id, text, images).
        self._steering: list[tuple[str, str, list[str]]] = []
        #: Closed once the run has decided to end, so a message that comes later
        #: is refused and the client starts a run with it instead.
        self._steerable = True

    # ----------------------------------------------------------- entry points

    def cancel(self, reason: str = "Run cancelled") -> None:
        """Ask the run to stop.

        Cooperative: the loop checks at turn and tool boundaries. A run blocked
        in a provider call stops when that call returns; a caller that wants it
        stopped *now* cancels the asyncio task, which lands in a
        ``CancelledError`` branch below and still ends on ``run_cancelled``.
        """
        self._cancel_reason = reason

    def steer(self, text: str, images: Sequence[str] | None = None) -> str | None:
        """Queue a message for the model's next turn; its id, or None if too late.

        It goes in at the next turn boundary: after the tool call in progress,
        before the model is asked again. A run that would have ended takes
        another turn for it instead.
        """
        if not self._steerable or self._cancel_reason:
            return None
        steer_id = next_id("steer")
        self._steering.append((steer_id, text, list(images or ())))
        return steer_id

    def release_device(self) -> None:
        if self._holds_device and self.device_lock is not None:
            self.device_lock.release()
        self._holds_device = False

    @property
    def cancelled(self) -> bool:
        return self._cancel_reason is not None

    async def run(self, prompt: str | None = None, **kwargs: Any) -> AgentResult:
        """Drain :meth:`stream` into a result. Raises on failure or cancellation."""
        async for event in self.stream(prompt, **kwargs):
            if isinstance(event, RunCompleted):
                return event.result
            if isinstance(event, RunFailed):
                raise RuntimeError(event.error)
            if isinstance(event, RunCancelled):
                raise RuntimeError(event.reason)
        raise RuntimeError("Run ended without a terminal event")

    async def stream(
        self,
        prompt: str | None = None,
        *,
        images: Sequence[str] | None = None,
        run_id: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[RuntimeEvent]:
        """Run one user turn to completion, yielding events as it goes.

        ``images`` are base64 strings, all attached to the user message.

        ``run_id`` may be supplied by a caller that has to answer with the id
        *before* the first event arrives — the service returns it from
        ``run.start``, and the client uses it for ``run.cancel``.
        """
        run_id = run_id or next_id("run")
        tool_defs = self.tool_registry.definitions or None
        # Every request carries the system prompt and the tool schemas besides
        # the history; ~40 tool schemas are several thousand tokens. They are
        # counted once here, so the compaction point and the usage the client
        # shows both describe the whole request.
        overhead = _fixed_overhead(self.session.system_prompt, tool_defs)
        budget = history_budget(self.context_window, self.max_output_tokens)
        # Estimates are in GPT-4o tokens and the window is in the server's;
        # the budget is converted with the scale the server's counts taught,
        # so every comparison below stays in estimate units.
        calibrated = bool(budget) and "max_context_tokens" not in kwargs
        if calibrated:
            kwargs["max_context_tokens"] = self._history_limit(budget, overhead)
        settings, provider_kwargs = LoopSettings.from_kwargs(kwargs)
        guard = EmptyResponseGuard(settings.max_empty_response_retries)

        def compact_at() -> int | None:
            if not budget or settings.disable_compaction:
                return None
            point = overhead + int(settings.max_context_tokens * settings.compact_threshold)
            return int(point * self.session.token_scale)

        def usage(estimate: int | None = None) -> ContextUsage:
            if estimate is None:
                estimate = self.session.token_count + overhead
            return ContextUsage(
                run_id=run_id,
                used_tokens=int(estimate * self.session.token_scale),
                context_window=self.context_window,
                compact_at=compact_at(),
            )

        if prompt is not None and prompt.strip() == COMPACT_COMMAND and not images:
            async for event in self._compact_command(run_id, settings, tool_defs, usage):
                yield event
            return

        if prompt or images:
            self.session.add_user_multimodal(text=prompt, images=images)
        if not self.vision:
            drop_images(self.session)
        if not self.video:
            drop_old_videos(self.session, keep=0)

        yield RunStarted(run_id=run_id)

        #: No prompt: this run exists to hand the model a background result.
        resumed = not (prompt or images)
        turn = 0
        awaiting_final_response = False
        #: The loop grants exactly one turn past ``max_turns``, for the final
        #: text after the last tool batch. Granting it on every iteration — as
        #: the old loop did — made a model that keeps calling tools an infinite
        #: loop, because every iteration ends by setting the flag again.
        grace_used = False
        last_message_id = ""
        screenshot_followup_added = False
        #: Set when the server reported a full window: the next turn compacts
        #: whatever the estimate says. Once a run, so a history the summary
        #: cannot shrink still ends.
        force_compaction = False
        full_context_retried = False
        stall_retries = 0

        while turn < self.max_turns or (awaiting_final_response and not grace_used):
            if self._cancel_reason:
                yield RunCancelled(run_id=run_id, reason=self._cancel_reason)
                return

            delivered = self._deliver_background(run_id, resumed)
            resumed = False
            if delivered is not None:
                yield delivered
            for steered in self._deliver_steering(run_id):
                yield steered

            outcome: list[str] = []
            async for event in self._compact(
                run_id, settings, tool_defs, outcome,
                reason=FULL_CONTEXT_REASON if force_compaction else COMPACTION_REASON,
                force=force_compaction,
            ):
                yield event
            force_compaction = False
            if outcome == ["cancelled"]:
                return

            sent_estimate = self.session.token_count + overhead
            yield usage(sent_estimate)
            message_id = next_id("msg")
            last_message_id = message_id
            yield MessageStarted(run_id=run_id, message_id=message_id)

            turn_kwargs = self._provider_kwargs_for_turn(
                provider_kwargs, screenshot_followup_added
            )
            text_parts: list[str] = []
            tool_calls: list[ToolUseBlock] = []
            provider_error: str | None = None
            stalled = False
            stats: ProviderUsage | None = None

            try:
                async for chunk in self.provider.stream(
                    messages=self.session.messages,
                    tools=tool_defs,
                    system_prompt=self.session.system_prompt,
                    **turn_kwargs,
                ):
                    if isinstance(chunk, ProviderReasoningDelta):
                        # Not part of the answer, never part of history: the
                        # user sees the model working instead of a spinner that
                        # has been still for ten seconds.
                        yield ReasoningDelta(
                            run_id=run_id, message_id=message_id, delta=chunk.delta
                        )
                    elif isinstance(chunk, ProviderTextDelta):
                        text_parts.append(chunk.delta)
                        yield MessageDelta(
                            run_id=run_id, message_id=message_id, delta=chunk.delta
                        )
                    elif isinstance(chunk, ProviderToolCall):
                        tool_calls.append(
                            ToolUseBlock(
                                name=chunk.name,
                                arguments=chunk.arguments,
                                id=chunk.id,
                                invalid=chunk.invalid,
                            )
                        )
                    elif isinstance(chunk, ProviderUsage):
                        stats = chunk
                    elif isinstance(chunk, ProviderError):
                        provider_error = chunk.error
                        stalled = chunk.stalled
            except asyncio.CancelledError:
                yield RunCancelled(
                    run_id=run_id, reason=self._cancel_reason or "Run cancelled"
                )
                return
            except Exception as exc:
                if is_stall(exc):
                    provider_error, stalled = str(exc), True
                else:
                    yield MessageFailed(run_id=run_id, message_id=message_id, error=str(exc))
                    yield RunFailed(run_id=run_id, error=str(exc))
                    return

            if provider_error is not None and stalled and stall_retries < MAX_STALL_RETRIES:
                # The server went quiet mid-reply. Nothing of it went into the
                # history, so the same request is simply asked again.
                stall_retries += 1
                yield MessageFailed(
                    run_id=run_id,
                    message_id=message_id,
                    error=f"{provider_error}; asking again ({stall_retries} of {MAX_STALL_RETRIES})",
                )
                continue
            if provider_error is None:
                stall_retries = 0

            if provider_error is not None and self.vision and refuses_images(provider_error):
                # A text-only model behind a server that refuses images (llama.cpp
                # without an mmproj): nothing of the reply went into the history,
                # so the same turn is asked again without them.
                self.vision = False
                if drop_images(self.session):
                    yield MessageFailed(
                        run_id=run_id,
                        message_id=message_id,
                        error=f"{provider_error}; this model cannot read images, asking again without them",
                    )
                    screenshot_followup_added = False
                    continue

            if provider_error is not None:
                yield MessageFailed(
                    run_id=run_id, message_id=message_id, error=provider_error
                )
                yield RunFailed(run_id=run_id, error=provider_error)
                return

            blocks: list[ContentBlock] = []
            text = "".join(text_parts)
            if text:
                blocks.append(TextBlock(text=text))
            blocks.extend(tool_calls)
            message = Message(role="assistant", content=blocks)

            if stats is not None and stats.input_tokens:
                self.session.calibrate_tokens(stats.input_tokens, sent_estimate)
                if calibrated:
                    settings.max_context_tokens = self._history_limit(budget, overhead)

            if (
                is_empty_final_response(message)
                and not full_context_retried
                and not settings.disable_compaction
                and context_full(stats, self.context_window)
            ):
                yield MessageFailed(
                    run_id=run_id,
                    message_id=message_id,
                    error="context window full: compacting, then trying again",
                )
                full_context_retried = force_compaction = True
                continue

            if is_empty_final_response(message):
                yield MessageFailed(
                    run_id=run_id,
                    message_id=message_id,
                    error="empty response: no text and no tool calls",
                )
                guard.record_empty()
                if guard.exhausted:
                    yield RunFailed(run_id=run_id, error=EMPTY_RESPONSE_EXHAUSTED_MESSAGE)
                    return
                guard.ensure_retry_prompt(self.session)
                continue

            guard.record_success()
            self.session.add_message(message)
            if awaiting_final_response and turn >= self.max_turns:
                grace_used = True
            turn += 1
            awaiting_final_response = False

            if stats is not None:
                yield MessageStats(
                    run_id=run_id,
                    message_id=message_id,
                    input_tokens=stats.input_tokens,
                    output_tokens=stats.output_tokens,
                    cached_tokens=stats.cached_tokens,
                    prefill_tps=stats.prefill_tps,
                    decode_tps=stats.decode_tps,
                    time_to_first_token=stats.time_to_first_token,
                )
            # The assistant message closes before tool events begin, so a client
            # renders one message widget with tool rows under it.
            yield MessageCompleted(run_id=run_id, message_id=message_id, output_text=text)

            if not tool_calls and self._steering:
                # The person said something while this answer was written: the
                # run goes on for it rather than ending under it.
                awaiting_final_response = False
                continue
            if not tool_calls:
                self._steerable = False
                yield usage()
                yield RunCompleted(
                    run_id=run_id,
                    final_message_id=message_id,
                    result=AgentResult(
                        run_id=run_id,
                        final_message_id=message_id,
                        output_text=text,
                        stop_reason="end_turn",
                    ),
                )
                return

            results: list[ToolResultBlock] = []
            moved_to_background = False
            for call in tool_calls:
                if self._cancel_reason:
                    self._close_tool_calls(tool_calls, results)
                    yield RunCancelled(run_id=run_id, reason=self._cancel_reason)
                    return

                if moved_to_background:
                    # The model wrote this batch to run in order, and the call
                    # before this one has not finished.
                    results.append(
                        ToolResultBlock(
                            tool_use_id=call.id,
                            tool_name=call.name,
                            content=(
                                "Not run: an earlier call in this batch moved to the "
                                "background. Call this again if it does not depend on "
                                "that result, or once the result arrives."
                            ),
                            is_error=True,
                        )
                    )
                    continue

                decisions: list[PermissionDecision | None] = []
                # A call that cannot run has nothing for the user to approve.
                if call.invalid is None:
                    async for event in self._gate_tool_call(run_id, call, decisions):
                        yield event
                decision = decisions[0] if decisions else None

                if decision is not None and not decision.granted:
                    # A denial is a tool result, not a crash: the model learns
                    # it was refused and can change course.
                    results.append(
                        ToolResultBlock(
                            tool_use_id=call.id,
                            tool_name=call.name,
                            content=(
                                "Permission denied: "
                                f"{decision.reason or 'the user refused this tool call'}"
                            ),
                            is_error=True,
                        )
                    )
                    continue

                yield ToolCallStarted(
                    run_id=run_id,
                    message_id=message_id,
                    tool_call_id=call.id,
                    tool_name=call.name,
                    arguments=call.arguments,
                )
                if (
                    self.device_lock is not None
                    and not self._holds_device
                    and uses_shared_device(call.name)
                ):
                    # After `tool_call_started`, so the wait shows as this call.
                    try:
                        await self.device_lock.acquire()
                    except asyncio.CancelledError:
                        self._close_tool_calls(tool_calls, results)
                        yield RunCancelled(
                            run_id=run_id, reason=self._cancel_reason or "Run cancelled"
                        )
                        return
                    self._holds_device = True

                # Asked for the background outright: adopted without the wait.
                now = background_now(call) if self.background is not None else None
                if now is not None and self.tool_registry.resolve(now.name) is None:
                    now = None  # nothing to run it as; the tool answers for itself
                if self.background is not None and (now is not None or call.name in BACKGROUND_TOOLS):
                    progress = ToolProgress()
                    started_at = time.monotonic()
                    # The task's own context, so only this call sees the progress.
                    context = contextvars.copy_context()
                    context.run(tool_progress.set, progress)
                    task = asyncio.create_task(
                        self.tool_registry.execute(now or call, context=self.tool_context),
                        context=context,
                    )
                    try:
                        if now is None:
                            await asyncio.wait({task}, timeout=self.background_after)
                    except asyncio.CancelledError:
                        progress.kill()
                        task.cancel()
                        self._close_tool_calls(tool_calls, results)
                        yield RunCancelled(
                            run_id=run_id, reason=self._cancel_reason or "Run cancelled"
                        )
                        return
                    if not task.done():
                        job = self.background.adopt(call, task, progress, run_id, started_at)
                        yield BackgroundTaskStarted(
                            run_id=run_id,
                            session_id=self.background.session_id,
                            task_id=job.id,
                            tool_call_id=call.id,
                            tool_name=call.name,
                            arguments=call.arguments,
                        )
                        results.append(
                            ToolResultBlock(
                                tool_use_id=call.id,
                                tool_name=call.name,
                                content=self._moved_text(job.id, asked=now is not None),
                            )
                        )
                        moved_to_background = True
                        continue
                    result = task.result()
                else:
                    result = await self.tool_registry.execute(call, context=self.tool_context)
                if result.is_error:
                    yield ToolCallFailed(
                        run_id=run_id,
                        message_id=message_id,
                        tool_call_id=call.id,
                        tool_name=call.name,
                        error=result.content,
                    )
                else:
                    yield ToolCallCompleted(
                        run_id=run_id,
                        message_id=message_id,
                        tool_call_id=call.id,
                        tool_name=call.name,
                        result=result.content,
                    )
                results.append(result)

            self.session.add_tool_results(results)
            screenshot_followup_added = add_screenshot_followup(
                self.session, results, mime_type=self.SCREENSHOT_TOOL_IMAGE_MIME_TYPE, vision=self.vision
            )
            if self.vision and self.video:
                add_video_followup(self.session, results, tool_calls, self.tool_context)
            awaiting_final_response = True

        if self._cancel_reason:
            yield RunCancelled(run_id=run_id, reason=self._cancel_reason)
            return

        # Turn exhaustion is a stop, not a crash: the work done so far stands.
        self._steerable = False
        yield usage()
        yield RunCompleted(
            run_id=run_id,
            final_message_id=last_message_id,
            result=AgentResult(
                run_id=run_id,
                final_message_id=last_message_id,
                output_text=MAX_TURNS_MESSAGE,
                stop_reason="max_turns",
            ),
        )

    # -------------------------------------------------------------- internals

    def _moved_text(self, job_id: str, asked: bool = False) -> str:
        where = (
            f"Started in the background as {job_id}."
            if asked
            else f"Still running after {self.background_after:.0f}s, so it continues in the "
            f"background as {job_id}."
        )
        return (
            f"{where} Do not run it again. Its output will arrive in a "
            "message when it finishes, and you will be called back then. Carry on with "
            "work that does not depend on it; if everything does, end your turn with a "
            f'short note. bash_output("{job_id}") shows its output so far, and '
            f'kill_shell("{job_id}") stops it.'
        )

    def _add_user_blocks(self, blocks: list[ContentBlock]) -> None:
        history = self.session.messages
        last = history[-1] if history else None
        if last is not None and last.role == "user":
            # Into the user message already last (a prompt, or tool results):
            # some model templates refuse two user messages in a row.
            before = (
                [TextBlock(text=last.content)] if isinstance(last.content, str) else list(last.content)
            )
            merged = Message(role="user", content=[*before, *blocks])
            self.session.replace_history([*history[:-1], merged])
        else:
            self.session.add_message(Message(role="user", content=blocks))

    def _deliver_steering(self, run_id: str) -> list[SteeringDelivered]:
        """Put what the person typed during the run into the conversation."""
        queued, self._steering = self._steering, []
        events = []
        for steer_id, text, images in queued:
            blocks: list[ContentBlock] = [
                TextBlock(text=f"[The user added, while you were working]\n{text}")
            ] if text else []
            blocks += [ImageBlock(data=data, mime_type="image/png") for data in images]
            if blocks:
                self._add_user_blocks(blocks)
            events.append(SteeringDelivered(run_id=run_id, steer_id=steer_id, text=text))
        return events

    def _deliver_background(self, run_id: str, resumed: bool) -> BackgroundTasksDelivered | None:
        """Put finished background tasks' results into the conversation."""
        jobs = self.background.drain() if self.background is not None else []
        if not jobs:
            return None
        text = "\n\n".join(job.notice() for job in jobs)
        self._add_user_blocks([TextBlock(text=text)])
        names = ", ".join(job.label for job in jobs)
        return BackgroundTasksDelivered(
            run_id=run_id,
            task_ids=[job.id for job in jobs],
            summary=f"Background task finished: {names}",
            resumed=resumed,
        )

    def _close_tool_calls(
        self, calls: list[ToolUseBlock], results: list[ToolResultBlock]
    ) -> None:
        """Answer every call of a cancelled batch.

        Providers refuse a history with a tool call and no result, so without
        this the next prompt in the session would fail.
        """
        answered = {result.tool_use_id for result in results}
        closed = list(results)
        for call in calls:
            if call.id not in answered:
                closed.append(
                    ToolResultBlock(
                        tool_use_id=call.id,
                        tool_name=call.name,
                        content="Cancelled by the user.",
                        is_error=True,
                    )
                )
        self.session.add_tool_results(closed)

    def _history_limit(self, budget: int, overhead: int) -> int:
        """The history's share of the budget, in estimated tokens."""
        scaled = int(budget / self.session.token_scale)
        return max(scaled - overhead, scaled // 2)

    async def _compact(
        self,
        run_id: str,
        settings: LoopSettings,
        tool_defs: Any,
        outcome: list[str],
        *,
        reason: str,
        force: bool = False,
        clear_first: bool = True,
        keep_tokens: int | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        """Clear old tool outputs, then summarise if that was not enough.

        ``force`` summarises whatever the estimate says. ``outcome`` gets
        "cancelled" (the caller must stop; the event is already sent),
        "compacted", "failed", or nothing when nothing was needed.
        """
        # Compaction is decided before its event is emitted: a
        # `context_compaction_started` with no matching completion would
        # leave the UI with a spinner that never clears.
        cleared: str | None = None
        if clear_first and (force or compaction_needed(self.session, settings)):
            yield ContextCompactionStarted(run_id=run_id, reason=reason)
            cleared = self._clear_old_outputs(settings)
            if cleared is not None:
                yield ContextCompactionCompleted(run_id=run_id, reason=cleared)
                outcome.append("compacted")
        if (force and cleared is None) or compaction_needed(self.session, settings):
            # Clearing was not enough (or the person's messages, not tool
            # output, fill the window): summarise. From the original
            # history — the cleared copy was discarded — so the summary
            # sees every output it describes.
            if cleared is not None or not clear_first:
                yield ContextCompactionStarted(run_id=run_id, reason=reason)
            try:
                summary = await self.session.compact_context(
                    provider=self.provider,
                    summary_type="detailed",
                    keep_tokens=keep_tokens or int(settings.max_context_tokens * COMPACTION_KEEP_FRACTION),
                    tools=tool_defs,
                    chat_kwargs=(
                        {"reasoning_effort": self.reasoning_effort}
                        if self.reasoning_effort
                        else {}
                    ),
                    is_prompt=is_user_prompt,
                    max_request_tokens=settings.max_context_tokens,
                )
            except asyncio.CancelledError:
                outcome[:] = ["cancelled"]
                yield RunCancelled(
                    run_id=run_id, reason=self._cancel_reason or "Run cancelled"
                )
                return
            except Exception as exc:
                outcome[:] = ["failed"]
                yield ContextCompactionFailed(run_id=run_id, reason=reason, error=str(exc))
            else:
                # Every started compaction ends with one of these two. An
                # empty summary, or "No messages to compact.", sent
                # neither, and the app showed "compacting context" until
                # it was restarted.
                if summary and not summary.startswith(("Compaction failed", "No messages")):
                    outcome[:] = ["compacted"]
                    yield ContextCompactionCompleted(run_id=run_id, reason=reason)
                else:
                    outcome[:] = ["failed"]
                    yield ContextCompactionFailed(
                        run_id=run_id,
                        reason=reason,
                        error=summary or "the history did not need compacting",
                    )

    async def _compact_command(
        self, run_id: str, settings: LoopSettings, tool_defs: Any, usage: Callable[..., ContextUsage]
    ) -> AsyncIterator[RuntimeEvent]:
        """``/compact``: summarise now, and say what it saved."""
        yield RunStarted(run_id=run_id)
        before = usage().used_tokens
        outcome: list[str] = []
        if len(self.session.messages) >= 3:
            # A quarter of the window can be the whole of a short history,
            # and then nothing would be summarised; asked for, it shrinks.
            keep = min(
                int(settings.max_context_tokens * COMPACTION_KEEP_FRACTION),
                self.session.token_count // 4,
            )
            async for event in self._compact(
                run_id, settings, tool_defs, outcome,
                reason=COMMAND_REASON, force=True, clear_first=False, keep_tokens=max(keep, 1),
            ):
                yield event
        if outcome == ["cancelled"]:
            return
        after = usage()
        if outcome == ["compacted"]:
            text = f"Compacted the conversation: about {before:,} → {after.used_tokens:,} tokens."
        elif outcome == ["failed"]:
            text = "Compaction failed; the conversation is unchanged or kept only its latest part."
        else:
            text = "Nothing to compact yet."
        yield after
        message_id = next_id("msg")
        # Shown as the reply, never saved: the model did not say it.
        yield MessageStarted(run_id=run_id, message_id=message_id)
        yield MessageDelta(run_id=run_id, message_id=message_id, delta=text)
        yield MessageCompleted(run_id=run_id, message_id=message_id, output_text=text)
        yield RunCompleted(
            run_id=run_id,
            final_message_id=message_id,
            result=AgentResult(run_id=run_id, final_message_id=message_id, output_text=text),
        )

    async def _gate_tool_call(
        self,
        run_id: str,
        call: ToolUseBlock,
        out: list["PermissionDecision | None"],
    ) -> AsyncIterator[RuntimeEvent]:
        """Rule on a tool call, yielding the prompt pair when the user is asked.

        The order is the whole point: ``permission_requested`` goes out *before*
        ``gate.decide`` is awaited, because in the service ``decide`` blocks on
        the client's answer. Ask after blocking and the run deadlocks.

        The decision is appended to ``out`` — an async generator cannot return
        one — and the caller must treat ``out == []`` as "no gate installed".
        """
        gate = self.permission_gate
        if gate is None:
            out.append(None)
            return

        request = PermissionRequest(
            tool_name=call.name,
            arguments=call.arguments,
            risk=(risk := call_risk(call.name, call.arguments)),
            reason=risk_reason(call.name, risk),
            run_id=run_id,
            request_id=next_id("req"),
        )
        prompted = _requires_prompt(gate, request)

        if prompted:
            yield PermissionRequested(
                run_id=run_id,
                request_id=request.request_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                reason=request.reason,
            )

        try:
            decision = await gate.decide(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a broken gate must not run the tool
            decision = PermissionDecision(
                granted=False, reason=f"permission gate failed: {exc}"
            )

        out.append(decision)

        if prompted:
            yield PermissionResolved(
                run_id=run_id,
                request_id=request.request_id,
                granted=decision.granted,
                scope=decision.scope,
                reason=decision.reason,
            )

    def _clear_old_outputs(self, settings: LoopSettings) -> str | None:
        """Clear old tool outputs if that alone brings the history down far
        enough; the reason to report, or None when it did not (and nothing
        was changed).

        The target is well under the compaction point, so the next one — and
        the prompt-cache rebuild every compaction costs — is many turns away.
        """
        budget = settings.max_context_tokens
        clearing = clear_old_tool_outputs(
            self.session.messages,
            target_tokens=int(budget * CLEAR_TARGET_FRACTION),
            keep_tokens=int(budget * COMPACTION_KEEP_FRACTION),
            outputs_dir=self.tool_context.outputs_dir,
        )
        if not clearing.cleared or clearing.tokens_after > budget * CLEAR_TARGET_FRACTION:
            return None
        try:
            clearing.write(self.tool_context.outputs_dir)
        except OSError:
            # A placeholder that promises a recall nobody can serve is worse
            # than a summary.
            return None
        self.session.replace_history(clearing.messages)
        noun = "output" if clearing.cleared == 1 else "outputs"
        return f"cleared {clearing.cleared} old tool {noun} (−{clearing.freed:,} tokens)"

    def _provider_kwargs_for_turn(
        self, base_kwargs: dict[str, Any], screenshot_followup_added: bool
    ) -> dict[str, Any]:
        """A screenshot follow-up is a vision turn: cap its output.

        The image dominates the context, and an unbounded reply on a local
        model stalls the run for minutes.
        """
        kwargs = dict(base_kwargs)
        if self.reasoning_effort:
            kwargs.setdefault("reasoning_effort", self.reasoning_effort)
        if screenshot_followup_added:
            kwargs["max_tokens"] = min(
                kwargs.get("max_tokens", 8192), self.SCREENSHOT_FOLLOWUP_MAX_TOKENS
            )
        return kwargs


def _fixed_overhead(system_prompt: str, tool_defs: Sequence[ToolDef] | None) -> int:
    """Tokens every request spends before the history: prompt and tool schemas."""
    import json

    from openagent.core.utils import count_tokens_for_messages

    tools = [
        {"name": t.name, "description": t.description, "parameters": t.parameters}
        for t in tool_defs or ()
    ]
    return count_tokens_for_messages(
        [
            {"role": "system", "content": system_prompt or ""},
            {"role": "system", "content": json.dumps(tools)},
        ]
    )


#: What a client may ask the model to spend on thinking. The literals are wire
#: values, sent to the provider as `reasoning_effort`.
REASONING_EFFORTS: tuple[str, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

__all__ = ["MAX_TURNS_MESSAGE", "REASONING_EFFORTS", "AgentRunner"]
