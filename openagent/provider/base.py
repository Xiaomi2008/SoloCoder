from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
import inspect
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


#: The header a local server keeps a conversation's slot by (basalt, and load
#: balancers in front of llama.cpp). Without it basalt guesses the slot from
#: the prompt, and a compacted history matches none and is read again in full.
SESSION_HEADER = "x-session-id"


def session_headers(kwargs: dict[str, Any]) -> dict[str, str] | None:
    """Take the runner's ``session_id`` out of ``kwargs``, as ``extra_headers``.

    The runner sends it on every turn; an API that has no such field must not
    see it, so every adapter pops it, and the SDK-based ones send it on as a
    header, which a server that does not know it ignores.
    """
    session_id = kwargs.pop("session_id", None)
    return {SESSION_HEADER: session_id} if session_id else None


class BaseProvider(ABC):
    def __init__(self, model: str, api_key: str | None = None, **kwargs: Any) -> None:
        self.model = model
        self.api_key = api_key

    async def close(self) -> None:
        """Release the underlying HTTP client.

        Every adapter owns a client, and a long-lived app (the Mac shell, the
        service) that builds a provider per turn and never closes it leaks a
        connection pool each time. The default handles SDK clients, whose
        ``close`` is sync on some SDKs and async on others; adapters with more
        to clean up override this.
        """
        client = getattr(self, "_client", None)
        closer = getattr(client, "close", None)
        if not callable(closer):
            return
        result = closer()
        if inspect.isawaitable(result):
            await result

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
                    invalid=block.invalid,
                )

        yield ProviderMessageCompleted(message_id=message_id)
