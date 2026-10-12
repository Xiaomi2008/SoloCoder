"""Shared building blocks for the agent run loop.

Both ``openagent.runtime.RuntimeAgent`` (tool-less, event-streaming) and
``openagent.core.agent.Agent`` (full tool loop) implement the same turn
mechanics: context-window compaction, empty-response retry, id generation.
This module is the single home for that policy so the two agents cannot drift.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from openagent.core.session import Session
from openagent.core.types import Message
from openagent.provider.base import BaseProvider

EMPTY_RESPONSE_RETRY_MESSAGE = (
    "Your previous response was empty. Continue the task and reply with either "
    "a non-empty assistant message or valid tool calls."
)

EMPTY_RESPONSE_EXHAUSTED_MESSAGE = (
    "The model returned empty responses repeatedly before finishing "
    "the task. Try again, switch models, or lower tool complexity."
)

DEFAULT_MAX_CONTEXT_TOKENS = 128000
DEFAULT_COMPACT_THRESHOLD = 0.8
DEFAULT_MAX_EMPTY_RESPONSE_RETRIES = 3


@dataclass
class LoopSettings:
    """Turn-loop settings, separated from provider kwargs.

    Keys consumed here must never reach the provider, which is why
    :meth:`from_kwargs` pops them out of the kwargs dict it is given.
    """

    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS
    compact_threshold: float = DEFAULT_COMPACT_THRESHOLD
    disable_compaction: bool = False
    max_empty_response_retries: int = DEFAULT_MAX_EMPTY_RESPONSE_RETRIES

    @classmethod
    def from_kwargs(cls, kwargs: dict[str, Any]) -> tuple[LoopSettings, dict[str, Any]]:
        """Split loop settings out of a kwargs dict.

        Returns the settings and a *new* dict holding only provider kwargs.
        ``disable_compaction`` may also be set as an instance attribute on the
        agent, which the caller passes in via ``disable_compaction_default``.
        """
        provider_kwargs = dict(kwargs)
        settings = cls(
            max_context_tokens=provider_kwargs.pop(
                "max_context_tokens", DEFAULT_MAX_CONTEXT_TOKENS
            ),
            compact_threshold=provider_kwargs.pop(
                "compact_threshold", DEFAULT_COMPACT_THRESHOLD
            ),
            disable_compaction=provider_kwargs.pop("disable_compaction", False),
            max_empty_response_retries=provider_kwargs.pop(
                "max_empty_response_retries", DEFAULT_MAX_EMPTY_RESPONSE_RETRIES
            ),
        )
        return settings, provider_kwargs

    def apply_instance_override(self, agent: Any) -> None:
        """Honor a ``disable_compaction`` attribute set on the agent instance."""
        if not self.disable_compaction:
            self.disable_compaction = bool(getattr(agent, "disable_compaction", False))


async def maybe_compact_context(
    session: Session, provider: BaseProvider, settings: LoopSettings, *, keep_recent: int = 5
) -> str | None:
    """Compact the session when it approaches the context limit.

    Returns the summary text when a compaction ran, else ``None``.
    """
    if settings.disable_compaction:
        return None
    if not session.check_compaction_needed(
        max_tokens=settings.max_context_tokens, threshold=settings.compact_threshold
    ):
        return None
    return await session.compact_context(
        provider=provider, keep_recent=keep_recent, summary_type="detailed"
    )


def is_empty_final_response(message: Message) -> bool:
    """A response with neither tool calls nor text — the model gave up."""
    return not message.has_tool_calls and not message.text.strip()


class EmptyResponseGuard:
    """Counts consecutive empty responses and manages the retry nudge.

    The retry prompt is only appended when it is not already the last message,
    so a run that keeps failing does not stack identical system messages.
    """

    def __init__(self, max_retries: int) -> None:
        self.max_retries = max_retries
        self.attempts = 0

    @property
    def exhausted(self) -> bool:
        return self.attempts > self.max_retries

    def record_empty(self) -> None:
        self.attempts += 1

    def record_success(self) -> None:
        self.attempts = 0

    def ensure_retry_prompt(self, session: Session) -> None:
        if (
            not session.messages
            or session.messages[-1].text != EMPTY_RESPONSE_RETRY_MESSAGE
        ):
            session.add("system", EMPTY_RESPONSE_RETRY_MESSAGE)


def next_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"
