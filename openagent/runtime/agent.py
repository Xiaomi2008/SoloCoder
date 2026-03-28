from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

from openagent.core.session import Session
from openagent.core.types import ImageBlock, Message, TextBlock
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


class Agent:
    EMPTY_RESPONSE_RETRY_MESSAGE = (
        "Your previous response was empty. Continue the task and reply with either "
        "a non-empty assistant message or valid tool calls."
    )

    def __init__(self, provider: BaseProvider, system_prompt: str = "") -> None:
        self.provider = provider
        self.session = Session(system_prompt=system_prompt)

    async def run(self, user_input: str | None = None, **kwargs: Any) -> AgentResult:
        """Run the agent with optional text input.

        For multimodal input, use the new multimodal method.
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
        async for event in self._run_multimodal_events(text, image_data, **kwargs):
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
        async for event in self._run_events(user_input, **kwargs):
            yield event

    async def stream_multimodal(
        self,
        text: str | None = None,
        image_data: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[RuntimeEvent]:
        async for event in self._run_multimodal_events(text, image_data, **kwargs):
            yield event

    async def _run_events(
        self, user_input: str | None = None, **kwargs: Any
    ) -> AsyncIterator[RuntimeEvent]:
        run_id = self._next_id("run")
        message_id = self._next_id("msg")
        max_context_tokens = kwargs.pop("max_context_tokens", 128000)
        compact_threshold = kwargs.pop("compact_threshold", 0.8)
        disable_compaction = kwargs.pop(
            "disable_compaction", getattr(self, "disable_compaction", False)
        )
        provider_kwargs = self._normalize_provider_kwargs(kwargs)
        max_empty_response_retries = provider_kwargs.pop(
            "max_empty_response_retries", 3
        )
        empty_response_attempts = 0

        # Handle multimodal input
        if user_input:
            self.session.add_user_multimodal(text=user_input)
        yield RunStarted(run_id=run_id)
        yield MessageStarted(run_id=run_id, message_id=message_id)

    async def _run_multimodal_events(
        self,
        text: str | None = None,
        image_data: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[RuntimeEvent]:
        """Handle multimodal input with images."""
        run_id = self._next_id("run")
        message_id = self._next_id("msg")
        max_context_tokens = kwargs.pop("max_context_tokens", 128000)
        compact_threshold = kwargs.pop("compact_threshold", 0.8)
        disable_compaction = kwargs.pop(
            "disable_compaction", getattr(self, "disable_compaction", False)
        )
        provider_kwargs = self._normalize_provider_kwargs(kwargs)
        max_empty_response_retries = provider_kwargs.pop(
            "max_empty_response_retries", 3
        )
        empty_response_attempts = 0

        # Add multimodal user message
        self.session.add_user_multimodal(text=text, image_data=image_data)

        yield RunStarted(run_id=run_id)
        yield MessageStarted(run_id=run_id, message_id=message_id)

        while True:
            try:
                if not disable_compaction and self.session.check_compaction_needed(
                    max_tokens=max_context_tokens, threshold=compact_threshold
                ):
                    await self.session.compact_context(
                        provider=self.provider,
                        keep_recent=5,
                        summary_type="detailed",
                    )

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

            empty_response_attempts += 1
            if empty_response_attempts > max_empty_response_retries:
                yield RunFailed(
                    run_id=run_id,
                    error=(
                        "The model returned empty responses repeatedly before finishing "
                        "the task. Try again, switch models, or lower tool complexity."
                    ),
                )
                return

            if (
                not self.session.messages
                or self.session.messages[-1].text != self.EMPTY_RESPONSE_RETRY_MESSAGE
            ):
                self.session.add("system", self.EMPTY_RESPONSE_RETRY_MESSAGE)

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
                "Bootstrap runtime Agent only supports a simple assistant text message response"
            )

        if isinstance(message.content, str):
            return message

        if all(isinstance(block, TextBlock) for block in message.content):
            return message

        raise ValueError(
            "Bootstrap runtime Agent only supports a simple assistant text message response"
        )

    @staticmethod
    def _next_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:24]}"

    @staticmethod
    def _normalize_provider_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
        provider_kwargs = dict(kwargs)
        provider_kwargs.pop("max_context_tokens", None)
        provider_kwargs.pop("compact_threshold", None)
        provider_kwargs.pop("disable_compaction", None)
        return provider_kwargs
