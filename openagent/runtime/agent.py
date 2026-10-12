from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from openagent.core.session import Session
from openagent.core.types import Message, TextBlock
from openagent.provider.base import BaseProvider
from openagent.runtime.events import (
    AgentResult,
    MessageCompleted,
    MessageDelta,
    RunCancelled,
    MessageStarted,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
)
from openagent.runtime.loop import (
    EMPTY_RESPONSE_EXHAUSTED_MESSAGE,
    EmptyResponseGuard,
    LoopSettings,
    maybe_compact_context,
    next_id,
)


class RuntimeAgent:
    """Minimal, tool-less agent that emits the canonical runtime event stream.

    This is the streaming/transport-facing agent: one user turn, one assistant
    text reply, no tool execution. ``openagent.core.agent.Agent`` is the full
    agent (tools, managers, MCP, display) and delegates here when it is
    constructed without tools, so both paths share one event contract.
    """

    EMPTY_RESPONSE_RETRY_MESSAGE = (
        "Your previous response was empty. Continue the task and reply with either "
        "a non-empty assistant message or valid tool calls."
    )

    def __init__(self, provider: BaseProvider, system_prompt: str = "") -> None:
        self.provider = provider
        self.session = Session(system_prompt=system_prompt)

    async def run(self, user_input: str | None = None, **kwargs: Any) -> AgentResult:
        """Run the agent with optional text input.

        For multimodal input, use :meth:`run_multimodal`.
        """
        async for event in self.stream(user_input, **kwargs):
            if isinstance(event, RunCompleted):
                return event.result
            if isinstance(event, RunFailed):
                raise RuntimeError(event.error)
            if isinstance(event, RunCancelled):
                raise RuntimeError(event.reason)

        raise RuntimeError("Runtime agent completed without a terminal event")

    async def run_multimodal(
        self,
        text: str | None = None,
        image_data: str | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        """Run the agent with multimodal input (text and/or images).

        Args:
            text: Optional text prompt
            image_data: Optional base64-encoded image data

        Returns:
            AgentResult with the model's response
        """
        async for event in self.stream_multimodal(text, image_data, **kwargs):
            if isinstance(event, RunCompleted):
                return event.result
            if isinstance(event, RunFailed):
                raise RuntimeError(event.error)
            if isinstance(event, RunCancelled):
                raise RuntimeError(event.reason)

        raise RuntimeError("Runtime agent completed without a terminal event")

    async def stream(
        self, user_input: str | None = None, **kwargs: Any
    ) -> AsyncIterator[RuntimeEvent]:
        async for event in self._run_events(
            text=user_input, add_message=bool(user_input), **kwargs
        ):
            yield event

    async def stream_multimodal(
        self,
        text: str | None = None,
        image_data: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[RuntimeEvent]:
        async for event in self._run_events(
            text=text, image_data=image_data, add_message=True
        ):
            yield event

    async def _run_events(
        self,
        text: str | None = None,
        image_data: str | None = None,
        *,
        add_message: bool,
        **kwargs: Any,
    ) -> AsyncIterator[RuntimeEvent]:
        """Single implementation shared by the text and multimodal entry points."""
        run_id = next_id("run")
        message_id = next_id("msg")
        settings, provider_kwargs = LoopSettings.from_kwargs(kwargs)
        settings.apply_instance_override(self)
        guard = EmptyResponseGuard(settings.max_empty_response_retries)

        if add_message:
            self.session.add_user_multimodal(text=text, image_data=image_data)

        yield RunStarted(run_id=run_id)
        yield MessageStarted(run_id=run_id, message_id=message_id)

        while True:
            try:
                await maybe_compact_context(self.session, self.provider, settings)
                response = await self.provider.chat(
                    messages=self.session.messages,
                    tools=None,
                    system_prompt=self.session.system_prompt,
                    **provider_kwargs,
                )
            except asyncio.CancelledError:
                yield RunCancelled(run_id=run_id, reason="Run cancelled")
                return
            except Exception as exc:
                yield RunFailed(run_id=run_id, error=str(exc))
                return

            assistant_message = self._assistant_message(response)
            if assistant_message.text.strip():
                break

            guard.record_empty()
            if guard.exhausted:
                yield RunFailed(run_id=run_id, error=EMPTY_RESPONSE_EXHAUSTED_MESSAGE)
                return
            guard.ensure_retry_prompt(self.session)

        output_text = assistant_message.text
        self.session.add_message(assistant_message)

        yield MessageDelta(run_id=run_id, message_id=message_id, delta=output_text)
        yield MessageCompleted(
            run_id=run_id,
            message_id=message_id,
            output_text=output_text,
        )

        result = AgentResult(
            run_id=run_id,
            final_message_id=message_id,
            output_text=output_text,
        )
        yield RunCompleted(
            run_id=run_id,
            final_message_id=message_id,
            result=result,
        )

    @staticmethod
    def _assistant_message(message: Message) -> Message:
        if message.role != "assistant":
            raise ValueError(
                "RuntimeAgent only supports a simple assistant text message response"
            )

        if isinstance(message.content, str):
            return message

        if all(isinstance(block, TextBlock) for block in message.content):
            return message

        raise ValueError(
            "RuntimeAgent only supports a simple assistant text message response"
        )


#: Back-compat alias. The canonical name is ``RuntimeAgent``; ``Agent`` is kept
#: so ``from openagent.runtime import Agent`` keeps working for existing callers.
Agent = RuntimeAgent
