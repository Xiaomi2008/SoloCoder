from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Literal


@dataclass(frozen=True)
class ProviderStreamEvent(ABC):
    message_id: str

    @property
    @abstractmethod
    def type(self) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class ProviderMessageStarted(ProviderStreamEvent):
    type: ClassVar[Literal["provider_message_started"]] = "provider_message_started"


@dataclass(frozen=True)
class ProviderTextDelta(ProviderStreamEvent):
    delta: str
    type: ClassVar[Literal["provider_text_delta"]] = "provider_text_delta"


@dataclass(frozen=True)
class ProviderReasoningDelta(ProviderStreamEvent):
    """A chunk of the model's private reasoning, streamed before the answer.

    Reasoning models (Swift 1.5, DeepSeek-R1, Claude with thinking, o-series)
    spend most of their generation budget here: measured on `swift-1.5-iq3_s`,
    ~1,900 reasoning tokens before the first answer character, ~10 seconds of
    it. A client that reads only text deltas shows a frozen spinner for that
    whole time, which reads as a hang. It is not a hang, and it should not look
    like one.
    """

    delta: str
    type: ClassVar[Literal["provider_reasoning_delta"]] = "provider_reasoning_delta"


@dataclass(frozen=True)
class ProviderMessageCompleted(ProviderStreamEvent):
    type: ClassVar[Literal["provider_message_completed"]] = "provider_message_completed"


@dataclass(frozen=True)
class ProviderToolCall(ProviderStreamEvent):
    id: str
    name: str
    arguments: dict[str, Any]
    type: ClassVar[Literal["provider_tool_call"]] = "provider_tool_call"


@dataclass(frozen=True)
class ProviderError(ProviderStreamEvent):
    error: str
    type: ClassVar[Literal["provider_error"]] = "provider_error"


ProviderTerminalEvent = ProviderMessageCompleted | ProviderError


__all__ = [
    "ProviderError",
    "ProviderMessageCompleted",
    "ProviderMessageStarted",
    "ProviderReasoningDelta",
    "ProviderStreamEvent",
    "ProviderTerminalEvent",
    "ProviderTextDelta",
    "ProviderToolCall",
]
