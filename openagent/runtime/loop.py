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
from openagent.core.types import ImageBlock, Message, TextBlock
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
#: Messages a compaction keeps verbatim when no token budget is given.
COMPACTION_KEEP_RECENT = 5
#: The share of the history budget a compaction keeps verbatim. With the
#: summary, a compacted history lands well under the compaction point, so the
#: next compaction (and its cache rebuild) is many turns away.
COMPACTION_KEEP_FRACTION = 0.25
#: Clearing old tool outputs must bring the history under this share of the
#: budget, or it is discarded in favour of a summary.
CLEAR_TARGET_FRACTION = 0.6


def is_user_prompt(message: Message) -> bool:
    """A request the person typed, not a screenshot the runtime attached."""
    if message.role != "user":
        return False
    text = message.text.strip()
    return bool(text) and not text.startswith(
        (SCREENSHOT_FOLLOWUP_TEXT, BROWSER_SCREENSHOT_FOLLOWUP_TEXT, OLD_SCREENSHOT_TEXT)
    )


def history_budget(context_window: int | None, max_output_tokens: int | None = None) -> int | None:
    """How many tokens the history may grow to: the window less the reply.

    The window holds the prompt *and* the answer. Compacting at 80% of the
    whole window left a 32k model with 26k of history and an 8k reply that
    no longer fit. The reply's share is capped at a quarter of the window, so
    a model that advertises a huge ``max_output_tokens`` keeps most of its
    window for history. None when the window is unknown.
    """
    if not context_window or context_window <= 0:
        return None
    reserve = min(max_output_tokens or 0, context_window // 4)
    return context_window - reserve
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


def compaction_needed(session: Session, settings: LoopSettings) -> bool:
    """Whether the next turn must compact, without running the summary model.

    The streaming runner asks this *before* emitting
    ``context_compaction_started``: a started event with no matching completed
    event breaks the ordering guarantee the UI is written against.
    """
    if settings.disable_compaction:
        return False
    # A prompt and one reply have nothing older to summarise: asking anyway
    # started a compaction that could only fail, every turn.
    if len(session.messages) < 3:
        return False
    return session.check_compaction_needed(
        max_tokens=settings.max_context_tokens, threshold=settings.compact_threshold
    )


async def maybe_compact_context(
    session: Session,
    provider: BaseProvider,
    settings: LoopSettings,
    *,
    keep_recent: int = COMPACTION_KEEP_RECENT,
    **options: Any,
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
        provider=provider, keep_recent=keep_recent, summary_type="detailed", **options
    )


def is_empty_final_response(message: Message) -> bool:
    """A response with neither tool calls nor text — the model gave up."""
    return not message.has_tool_calls and not message.text.strip()


SCREENSHOT_FOLLOWUP_TEXT = (
    "Latest screenshot from the screenshot tool. "
    "Use screenshot image coordinates only. "
    "If unsure, call get_screenshot_info() for the valid image size and scale."
)


# The GUI actions that capture the screen after acting; their result says so
# with the note below, which is only there when the capture worked.
COMPUTER_ACTION_TOOLS = frozenset(
    {"click", "double_click", "type_text", "key_combination", "move_mouse", "scroll"}
)
_AFTER_ACTION_NOTE = "A screenshot taken after it is attached."
KEEP_RECENT_SCREENSHOTS = 3
OLD_SCREENSHOT_TEXT = "[An earlier screenshot, removed to save context.]"


BROWSER_SCREENSHOT_FOLLOWUP_TEXT = (
    "Latest screenshot of the agent's browser page. "
    "Act on elements by their [n] ref from browser_snapshot, not by coordinates."
)


def add_screenshot_followup(
    session: Session,
    results: list[Any],
    *,
    mime_type: str = "image/jpeg",
) -> bool:
    """Feed a screenshot tool result back to a vision model as an image.

    The image is the capture itself, kept by the tool, and not the tool's
    result: called without ``return_base64`` (which is how a model calls it)
    the result is a one-line summary, and attaching that as image data showed
    the model nothing. Called with it, the result is ~20k characters of base64
    that the model would read as text — most of a 32k context per screenshot —
    so the result is cut to a summary here. ``results`` are the blocks already
    in the session, so the cut reaches the history too.

    Reports whether an image was attached, because the caller must then cap
    ``max_tokens`` for the next turn.
    """
    added = False
    desktop_capture = False
    desktop_result: Any = None
    for result in results:
        if getattr(result, "is_error", False) or not result.content or result.content.startswith("Error"):
            continue
        name = getattr(result, "tool_name", None)
        if name == "browser_screenshot":
            from openagent.tools.browser import last_screenshot_base64 as browser_capture

            page_image = browser_capture()
            if page_image:
                session.add_user_multimodal(
                    text=BROWSER_SCREENSHOT_FOLLOWUP_TEXT, image_data=page_image, image_mime_type=mime_type
                )
                added = True
            continue
        if name == "screenshot":
            desktop_capture = True
            desktop_result = result
        elif name in COMPUTER_ACTION_TOOLS and _AFTER_ACTION_NOTE in result.content:
            desktop_capture = True

    if desktop_capture:
        # One image per turn, the latest: every capture overwrites the last,
        # so three actions in a turn would have attached the same picture
        # three times.
        info = ""
        image_data: str | None = None
        try:
            from openagent.tools.computer_use import get_screenshot_info, last_screenshot_base64

            image_data = last_screenshot_base64()
            screenshot_info = get_screenshot_info()
            if not screenshot_info.startswith("No screenshot"):
                info = f" The valid image coordinate range is described here: {screenshot_info}"
        except Exception:
            info = ""
        if (
            image_data is None
            and desktop_result is not None
            and not desktop_result.content.startswith("Screenshot captured")
        ):
            # A screenshot tool other than ours that returns the image itself.
            image_data = desktop_result.content
        if image_data:
            if desktop_result is not None and desktop_result.content == image_data:
                desktop_result.content = "Screenshot captured; the image is attached in the next message."
            session.add_user_multimodal(
                text=f"{SCREENSHOT_FOLLOWUP_TEXT}{info}",
                image_data=image_data,
                image_mime_type=mime_type,
            )
            added = True
    if added:
        drop_old_screenshots(session)
    return added


def drop_old_screenshots(session: Session, keep: int | None = None) -> int:
    """Strip the image from all but the last ``keep`` screenshot follow-ups.

    With a capture after every action, a 30-step task carried 30 screenshots,
    each re-sent on every turn, and only the last few describe the screen as
    it is. Anthropic's reference harness keeps the most recent three for the
    same reason. The text stays, so the history still shows that a capture
    happened. Returns how many images were dropped.
    """
    keep = KEEP_RECENT_SCREENSHOTS if keep is None else keep
    followups = [
        m
        for m in session.messages
        if m.role == "user"
        and isinstance(m.content, list)
        and any(isinstance(b, ImageBlock) for b in m.content)
        and any(
            isinstance(b, TextBlock)
            and b.text.startswith((SCREENSHOT_FOLLOWUP_TEXT, BROWSER_SCREENSHOT_FOLLOWUP_TEXT))
            for b in m.content
        )
    ]
    dropped = 0
    for message in followups[: max(0, len(followups) - keep)]:
        images = [b for b in message.content if isinstance(b, ImageBlock)]
        message.content = [TextBlock(text=OLD_SCREENSHOT_TEXT)]
        dropped += len(images)
    return dropped


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
