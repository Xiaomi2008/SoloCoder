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
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from openagent.core.session import Session
from openagent.core.tool import ToolContext, ToolRegistry, tool
from openagent.core.types import (
    ContentBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from openagent.provider.base import BaseProvider
from openagent.providers import (
    ProviderReasoningDelta,
    ProviderError,
    ProviderTextDelta,
    ProviderToolCall,
)
from openagent.runtime.events import (
    ReasoningDelta,
    AgentResult,
    ContextCompactionCompleted,
    ContextCompactionFailed,
    ContextCompactionStarted,
    MessageCompleted,
    MessageDelta,
    MessageFailed,
    MessageStarted,
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
from openagent.runtime.loop import (
    EMPTY_RESPONSE_EXHAUSTED_MESSAGE,
    EmptyResponseGuard,
    LoopSettings,
    add_screenshot_followup,
    compaction_needed,
    is_empty_final_response,
    maybe_compact_context,
    next_id,
)
from openagent.runtime.permissions import (
    PermissionDecision,
    PermissionGate,
    PermissionRequest,
    RiskGate,
    risk_reason,
    tool_risk,
)

MAX_TURNS_MESSAGE = (
    "The model kept requesting more tool work after the turn limit was reached. "
    "Try again, increase max_turns, or switch to a more reliable model."
)

COMPACTION_REASON = "context approaching limit"


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
        self._cancel_reason: str | None = None

    # ----------------------------------------------------------- entry points

    def cancel(self, reason: str = "Run cancelled") -> None:
        """Ask the run to stop.

        Cooperative: the loop checks at turn and tool boundaries. A run blocked
        in a provider call stops when that call returns; a caller that wants it
        stopped *now* cancels the asyncio task, which lands in a
        ``CancelledError`` branch below and still ends on ``run_cancelled``.
        """
        self._cancel_reason = reason

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

        ``images`` are base64 strings; the first, if any, is attached to the
        user message (``Session`` carries one image per message).

        ``run_id`` may be supplied by a caller that has to answer with the id
        *before* the first event arrives — the service returns it from
        ``run.start``, and the client uses it for ``run.cancel``.
        """
        run_id = run_id or next_id("run")
        settings, provider_kwargs = LoopSettings.from_kwargs(kwargs)
        guard = EmptyResponseGuard(settings.max_empty_response_retries)
        tool_defs = self.tool_registry.definitions or None

        if prompt or images:
            self.session.add_user_multimodal(
                text=prompt, image_data=(list(images) + [None])[0] if images else None
            )

        yield RunStarted(run_id=run_id)

        turn = 0
        awaiting_final_response = False
        #: The loop grants exactly one turn past ``max_turns``, for the final
        #: text after the last tool batch. Granting it on every iteration — as
        #: the old loop did — made a model that keeps calling tools an infinite
        #: loop, because every iteration ends by setting the flag again.
        grace_used = False
        last_message_id = ""
        screenshot_followup_added = False

        while turn < self.max_turns or (awaiting_final_response and not grace_used):
            if self._cancel_reason:
                yield RunCancelled(run_id=run_id, reason=self._cancel_reason)
                return

            # Compaction is decided before its event is emitted: a
            # `context_compaction_started` with no matching completion would
            # leave the UI with a spinner that never clears.
            if compaction_needed(self.session, settings):
                yield ContextCompactionStarted(run_id=run_id, reason=COMPACTION_REASON)
                try:
                    summary = await maybe_compact_context(
                        self.session, self.provider, settings
                    )
                except asyncio.CancelledError:
                    yield RunCancelled(
                        run_id=run_id, reason=self._cancel_reason or "Run cancelled"
                    )
                    return
                except Exception as exc:
                    yield ContextCompactionFailed(
                        run_id=run_id, reason=COMPACTION_REASON, error=str(exc)
                    )
                else:
                    if summary and summary.startswith("Compaction failed"):
                        yield ContextCompactionFailed(
                            run_id=run_id, reason=COMPACTION_REASON, error=summary
                        )
                    elif summary:
                        yield ContextCompactionCompleted(
                            run_id=run_id, reason=COMPACTION_REASON
                        )

            message_id = next_id("msg")
            last_message_id = message_id
            yield MessageStarted(run_id=run_id, message_id=message_id)

            turn_kwargs = self._provider_kwargs_for_turn(
                provider_kwargs, screenshot_followup_added
            )
            text_parts: list[str] = []
            tool_calls: list[ToolUseBlock] = []
            provider_error: str | None = None

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
                                name=chunk.name, arguments=chunk.arguments, id=chunk.id
                            )
                        )
                    elif isinstance(chunk, ProviderError):
                        provider_error = chunk.error
            except asyncio.CancelledError:
                yield RunCancelled(
                    run_id=run_id, reason=self._cancel_reason or "Run cancelled"
                )
                return
            except Exception as exc:
                yield MessageFailed(run_id=run_id, message_id=message_id, error=str(exc))
                yield RunFailed(run_id=run_id, error=str(exc))
                return

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

            # The assistant message closes before tool events begin, so a client
            # renders one message widget with tool rows under it.
            yield MessageCompleted(run_id=run_id, message_id=message_id, output_text=text)

            if not tool_calls:
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
            for call in tool_calls:
                if self._cancel_reason:
                    yield RunCancelled(run_id=run_id, reason=self._cancel_reason)
                    return

                decisions: list[PermissionDecision | None] = []
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
                self.session, results, mime_type=self.SCREENSHOT_TOOL_IMAGE_MIME_TYPE
            )
            awaiting_final_response = True

        if self._cancel_reason:
            yield RunCancelled(run_id=run_id, reason=self._cancel_reason)
            return

        # Turn exhaustion is a stop, not a crash: the work done so far stands.
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
            risk=tool_risk(call.name),
            reason=risk_reason(call.name),
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


#: What a client may ask the model to spend on thinking. The literals are wire
#: values, sent to the provider as `reasoning_effort`.
REASONING_EFFORTS: tuple[str, ...] = ("none", "minimal", "low", "medium", "high")

__all__ = ["MAX_TURNS_MESSAGE", "REASONING_EFFORTS", "AgentRunner"]
