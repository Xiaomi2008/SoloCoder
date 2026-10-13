"""Test support: fakes that speak the engine's real protocols.

Kept out of ``conftest.py`` so tests can ``import`` them explicitly — a fixture
hides what a test depends on, and these types are the dependency.
"""

from __future__ import annotations

from typing import Any

from openagent.core.types import Message, ToolDef
from openagent.providers import (
    ProviderError,
    ProviderMessageCompleted,
    ProviderMessageStarted,
    ProviderReasoningDelta,
    ProviderTextDelta,
    ProviderToolCall,
)


class ScriptedStreamProvider:
    """A provider whose ``stream`` replays a script, one turn per entry.

    The runner consumes ``stream``, not ``chat``, so this is the seam the
    engine's tests are written against: each turn is a list of provider stream
    events, and every call is recorded so a test can assert what the model was
    actually shown (tools, system prompt, message history).
    """

    def __init__(self, turns: list[list[Any]] | None = None, model: str = "scripted") -> None:
        self.turns = list(turns or [])
        self.model = model
        self.api_key = None
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ):
        self.calls.append(
            {
                "messages": list(messages),
                "tools": tools,
                "system_prompt": system_prompt,
                "kwargs": kwargs,
            }
        )
        turn = self.turns.pop(0) if self.turns else text_turn("done")[1:-1]
        for event in turn:
            yield event

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> Message:
        self.calls.append(
            {
                "messages": list(messages),
                "tools": tools,
                "system_prompt": system_prompt,
                "kwargs": kwargs,
            }
        )
        return Message(role="assistant", content="done")

    async def close(self) -> None:
        self.closed = True


class HangingProvider:
    """A provider that never returns — for cancellation tests."""

    model = "hanging"
    api_key = None

    def __init__(self) -> None:
        self.entered = False
        self.closed = False

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ):
        import asyncio

        self.entered = True
        yield ProviderMessageStarted(message_id="msg_hang")
        await asyncio.Event().wait()  # never resolves; the caller cancels

    async def chat(self, *args: Any, **kwargs: Any) -> Message:  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:
        self.closed = True
        return None


def text_turn(*deltas: str, message_id: str = "msg_script") -> list[Any]:
    """One assistant message, streamed in pieces."""
    return [
        ProviderMessageStarted(message_id=message_id),
        *[ProviderTextDelta(message_id=message_id, delta=d) for d in deltas],
        ProviderMessageCompleted(message_id=message_id),
    ]


def reasoning_turn(
    *reasoning: str, text: str, message_id: str = "msg_script"
) -> list[Any]:
    """A reasoning model's turn: private tokens first, then the answer.

    The shape `swift-1.5-iq3_s` really produces: ~1,900 reasoning tokens before
    the first answer character.
    """
    return [
        ProviderMessageStarted(message_id=message_id),
        *[ProviderReasoningDelta(message_id=message_id, delta=r) for r in reasoning],
        ProviderTextDelta(message_id=message_id, delta=text),
        ProviderMessageCompleted(message_id=message_id),
    ]


def tool_turn(
    name: str,
    arguments: dict[str, Any],
    *,
    id: str = "call_1",
    text: str = "",
    message_id: str = "msg_script",
) -> list[Any]:
    events: list[Any] = [ProviderMessageStarted(message_id=message_id)]
    if text:
        events.append(ProviderTextDelta(message_id=message_id, delta=text))
    events.append(ProviderToolCall(message_id=message_id, id=id, name=name, arguments=arguments))
    events.append(ProviderMessageCompleted(message_id=message_id))
    return events


def empty_turn(*, message_id: str = "msg_script") -> list[Any]:
    return [
        ProviderMessageStarted(message_id=message_id),
        ProviderMessageCompleted(message_id=message_id),
    ]


def error_turn(error: str, *, message_id: str = "msg_script") -> list[Any]:
    return [
        ProviderMessageStarted(message_id=message_id),
        ProviderError(message_id=message_id, error=error),
    ]


class RaisingTurn(list):
    """A stream that raises mid-flight, as a dropped connection does."""

    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error

    def __iter__(self):
        raise RuntimeError(self.error)


def raising_turn(error: str) -> list[Any]:
    return RaisingTurn(error)


__all__ = [
    "HangingProvider",
    "RaisingTurn",
    "ScriptedStreamProvider",
    "empty_turn",
    "error_turn",
    "raising_turn",
    "text_turn",
    "tool_turn",
]
