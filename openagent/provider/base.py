from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
import uuid
from typing import Any

from openagent.core.types import Message, TextBlock, ToolDef, ToolUseBlock
from openagent.providers import (
    ProviderError,
    ProviderMessageCompleted,
    ProviderMessageStarted,
    ProviderStreamEvent,
    ProviderTextDelta,
    ProviderToolCall,
)


class BaseProvider(ABC):
    def __init__(self, model: str, api_key: str | None = None, **kwargs: Any) -> None:
        self.model = model
        self.api_key = api_key

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> Message:
        """Send messages to the LLM and return the assistant response."""

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> AsyncIterator[ProviderStreamEvent]:
        """Stream provider stream events from the LLM response.

        Default implementation adapts non-streaming chat output into the
        bootstrap provider event contract. Subclasses can override for true
        provider-native streaming support.
        """
        message_id = f"msg_{uuid.uuid4().hex[:24]}"

        yield ProviderMessageStarted(message_id=message_id)

        try:
            response = await self.chat(messages, tools, system_prompt, **kwargs)
        except Exception as exc:
            yield ProviderError(message_id=message_id, error=str(exc))
            return

        if isinstance(response.content, str):
            if response.content:
                yield ProviderTextDelta(message_id=message_id, delta=response.content)
            yield ProviderMessageCompleted(message_id=message_id)
            return

        for block in response.content:
            if isinstance(block, TextBlock) and block.text:
                yield ProviderTextDelta(message_id=message_id, delta=block.text)
            elif isinstance(block, ToolUseBlock):
                yield ProviderToolCall(
                    message_id=message_id,
                    id=block.id,
                    name=block.name,
                    arguments=block.arguments,
                )

        yield ProviderMessageCompleted(message_id=message_id)
