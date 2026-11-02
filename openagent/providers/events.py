from __future__ import annotations

import time
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


@dataclass(frozen=True)
class ProviderUsage(ProviderStreamEvent):
    """Token counts for one call and how fast it went, sent before it completes.

    ``prefill_tps`` is prompt tokens read per second, ``decode_tps`` tokens
    written per second (reasoning included). A server that measures them
    (llama.cpp's ``timings``) is used as is; otherwise they come from the
    client's clock, so prefill includes network and queueing time. Any field
    may be None when the server reports nothing to compute it from.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    prefill_tps: float | None = None
    decode_tps: float | None = None
    time_to_first_token: float | None = None
    type: ClassVar[Literal["provider_usage"]] = "provider_usage"


class CallClock:
    """Times one model call: when it was sent and when the first token came."""

    def __init__(self) -> None:
        self.sent = time.monotonic()
        self.first: float | None = None

    def token(self) -> None:
        if self.first is None:
            self.first = time.monotonic()

    def usage(
        self,
        message_id: str,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_tokens: int | None = None,
        prefill_tps: float | None = None,
        decode_tps: float | None = None,
        measure_prefill: bool = True,
    ) -> ProviderUsage | None:
        """The usage event, with speeds the server did not send measured here.

        None when there is nothing to show: no token counts and no speeds.
        """
        if input_tokens is None and output_tokens is None and decode_tps is None:
            return None
        ttft = self.first - self.sent if self.first is not None else None
        uncached = (input_tokens or 0) - (cached_tokens or 0)
        if prefill_tps is None and measure_prefill and uncached > 0 and ttft and ttft > 0:
            prefill_tps = uncached / ttft
        if decode_tps is None and self.first is not None and output_tokens and output_tokens > 1:
            elapsed = time.monotonic() - self.first
            if elapsed > 0.05:
                decode_tps = (output_tokens - 1) / elapsed
        return ProviderUsage(
            message_id=message_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            prefill_tps=prefill_tps,
            decode_tps=decode_tps,
            time_to_first_token=ttft,
        )


ProviderTerminalEvent = ProviderMessageCompleted | ProviderError


__all__ = [
    "CallClock",
    "ProviderError",
    "ProviderMessageCompleted",
    "ProviderMessageStarted",
    "ProviderReasoningDelta",
    "ProviderStreamEvent",
    "ProviderTerminalEvent",
    "ProviderTextDelta",
    "ProviderToolCall",
    "ProviderUsage",
]
