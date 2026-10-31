from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Literal

from openagent.provider.base import BaseProvider

from .types import (
    ContentBlock,
    ImageBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    text_message,
    tool_result_message,
)


@dataclass
class Session:
    system_prompt: str = ""
    _messages: list[Message] = field(default_factory=list)

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    def add(
        self, role: Literal["user", "assistant", "system"], content: str
    ) -> Message:
        msg = text_message(role, content)
        self._messages.append(msg)
        return msg

    def add_message(self, message: Message) -> None:
        self._messages.append(message)

    def replace_history(self, messages: list[Message]) -> None:
        self._messages = list(messages)

    def add_tool_results(self, results: list[ToolResultBlock]) -> Message:
        msg = tool_result_message(results)
        self._messages.append(msg)
        return msg

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)

    def to_list(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for msg in self._messages:
            if isinstance(msg.content, str):
                out.append({"role": msg.role, "content": msg.content})
            else:
                blocks = []
                for b in msg.content:
                    if isinstance(b, TextBlock):
                        blocks.append({"type": "text", "text": b.text})
                    elif isinstance(b, ImageBlock):
                        blocks.append(
                            {"type": "image_url", "image_url": {"url": b.url}}
                        )
                    elif isinstance(b, ToolUseBlock):
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": b.id,
                                "name": b.name,
                                "arguments": b.arguments,
                            }
                        )
                    elif isinstance(b, ToolResultBlock):
                        block = {
                            "type": "tool_result",
                            "tool_use_id": b.tool_use_id,
                            "content": b.content,
                            "is_error": b.is_error,
                        }
                        if b.tool_name is not None:
                            block["tool_name"] = b.tool_name
                        blocks.append(block)
                out.append({"role": msg.role, "content": blocks})
        return out

    def add_user_multimodal(
        self,
        text: str | None = None,
        image_data: str | None = None,
        image_mime_type: str = "image/png",
        images: Sequence[str] | None = None,
    ) -> Message:
        """Add a user message that can contain text, images, or both.

        Args:
            text: Optional text content
            image_data: Optional base64-encoded image data
            image_mime_type: MIME type for the image blocks
            images: Further base64-encoded images, appended after ``image_data``

        Returns:
            Message with text and/or image blocks
        """
        blocks: list[ContentBlock] = []

        if text:
            blocks.append(TextBlock(text=text))

        for data in [image_data, *(images or ())]:
            if data:
                blocks.append(ImageBlock(data=data, mime_type=image_mime_type))

        # If only image provided, wrap in user message
        if not blocks:
            return self.add("user", "")

        # If only text, use text_message for consistency
        if len(blocks) == 1 and isinstance(blocks[0], TextBlock):
            return self.add("user", text)

        msg = Message(role="user", content=blocks)
        self._messages.append(msg)
        return msg

    def add_user_image(self, image_data: str, mime_type: str = "image/png") -> Message:
        """Add an image message from the user (for vision-capable models).

        Args:
            image_data: Base64-encoded image data
            mime_type: MIME type of the image (default: image/png)
        """
        image_block = ImageBlock(data=image_data, mime_type=mime_type)
        msg = Message(role="user", content=[image_block])
        self._messages.append(msg)
        return msg

    def add_text(
        self, role: Literal["user", "assistant", "system"], text: str
    ) -> Message:
        """Add a text message with a specific role."""
        msg = text_message(role, text)
        self._messages.append(msg)
        return msg

    def save(self, path: str | Path, *, title: str | None = None) -> None:
        """Save session to a JSON file.

        Args:
            path: Path to save the session to
            title: A display name for the session, written first so a reader can
                find it at the head of the file. `Session` does not own its name
                — the service does, the way it owns the id — so this is a
                pass-through, not a field: the caller reads the stored title,
                derives a new one, and hands it back here.
        """
        data = {}
        if title:
            data["title"] = title
        data["system_prompt"] = self.system_prompt
        data["messages"] = self.to_list()
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Session":
        """Load session from a JSON file.

        Args:
            path: Path to load the session from

        Returns:
            Loaded Session instance
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        session = cls(system_prompt=data.get("system_prompt", ""))

        for msg_data in data.get("messages", []):
            role = msg_data["role"]
            content = msg_data["content"]

            if isinstance(content, str):
                session._messages.append(Message(role=role, content=content))
            else:
                blocks: list[ContentBlock] = []
                for block_data in content:
                    block_type = block_data.get("type")
                    if block_type == "text":
                        blocks.append(TextBlock(text=block_data["text"]))
                    elif block_type == "image_url":
                        # Saved as a data URL: "data:<mime>;base64,<data>".
                        url = block_data.get("image_url", {}).get("url", "")
                        header, _, data = url.partition(",")
                        if header.startswith("data:") and data:
                            mime_type = header[len("data:"):].split(";")[0] or "image/png"
                            blocks.append(ImageBlock(data=data, mime_type=mime_type))
                    elif block_type == "tool_use":
                        blocks.append(
                            ToolUseBlock(
                                id=block_data["id"],
                                name=block_data["name"],
                                arguments=block_data["arguments"],
                            )
                        )
                    elif block_type == "tool_result":
                        blocks.append(
                            ToolResultBlock(
                                tool_use_id=block_data["tool_use_id"],
                                tool_name=block_data.get("tool_name"),
                                content=block_data["content"],
                                is_error=block_data.get("is_error", False),
                            )
                        )
                session._messages.append(Message(role=role, content=blocks))

        return session

    def to_list_for_compaction(self) -> list[dict[str, Any]]:
        """Convert messages to list format suitable for compaction.

        Returns:
            List of message dictionaries in API-compatible format
        """
        out: list[dict[str, Any]] = []
        for msg in self._messages:
            if isinstance(msg.content, str):
                out.append({"role": msg.role, "content": msg.content})
            else:
                blocks = []
                for b in msg.content:
                    if isinstance(b, TextBlock):
                        blocks.append({"type": "text", "text": b.text})
                    elif isinstance(b, ImageBlock):
                        blocks.append(
                            {"type": "image_url", "image_url": {"url": b.url}}
                        )
                    elif isinstance(b, ToolUseBlock):
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": b.id,
                                "name": b.name,
                                "arguments": b.arguments,
                            }
                        )
                    elif isinstance(b, ToolResultBlock):
                        block = {
                            "type": "tool_result",
                            "tool_use_id": b.tool_use_id,
                            "content": b.content,
                            "is_error": b.is_error,
                        }
                        if b.tool_name is not None:
                            block["tool_name"] = b.tool_name
                        blocks.append(block)
                out.append({"role": msg.role, "content": blocks})
        return out

    def _get_compaction_tail(self, keep_recent: int) -> list[Message]:
        """The last ``keep_recent`` messages, never starting on a tool result.

        Any message may start the tail: tool calls and their results count
        like everything else. This used to require a *user* message at the
        start, and in an agent run the only one is the prompt before 60 tool
        steps — so the "tail" was the whole run, compaction freed nothing, and
        it ran again on the next turn.
        """
        if keep_recent <= 0 or not self._messages:
            return []
        return self._messages[self._safe_start(len(self._messages) - keep_recent) :]

    def _safe_start(self, start: int) -> int:
        """Move ``start`` back so a tool result keeps the call it answers."""
        start = max(0, min(start, len(self._messages) - 1))
        while start > 0 and self._messages[start].role == "tool_result":
            start -= 1
        return start

    def _tail_start_by_tokens(self, keep_tokens: int) -> int:
        """Where the verbatim tail starts: the newest messages that fit in
        ``keep_tokens``, and at least the last step."""
        from .utils import count_tokens_for_messages

        messages = self.to_list_for_compaction()
        start = len(messages)
        used = 0
        for index in range(len(messages) - 1, -1, -1):
            cost = count_tokens_for_messages([messages[index]])
            if start < len(messages) and used + cost > keep_tokens:
                break
            used += cost
            start = index
        return self._safe_start(start)

    async def compact_context(
        self,
        provider: BaseProvider,
        keep_recent: int = 5,
        summary_type: str = "detailed",
        *,
        keep_tokens: int | None = None,
        tools: Sequence[Any] | None = None,
        chat_kwargs: dict[str, Any] | None = None,
        is_prompt: Any = None,
    ) -> str:
        """Summarise the older history into one message; keep the recent part.

        ``keep_tokens`` sizes the verbatim tail by tokens (at least the last
        step); without it the tail is the last ``keep_recent`` messages. The
        newest message ``is_prompt`` accepts — the person's latest request — is
        kept verbatim even when it is older than the tail, so the agent never
        knows its task only from a summary.

        The summary request is the conversation as the model last saw it —
        same system prompt, same tools, same messages — with the instruction
        appended. A server's prompt cache (llama.cpp's, OpenAI's) matches the
        longest common prefix, so it serves the whole history from cache and
        processes only the instruction. The old request re-sent the history
        as JSON text under a different system prompt: nothing matched, and a
        200k-token history was prefilled from scratch just to be summarised.
        Pass ``tools`` as the runs send them, or the prefix differs at the
        tool schemas.
        """
        if len(self._messages) < 2:
            return "No messages to compact."
        start = (
            self._tail_start_by_tokens(keep_tokens)
            if keep_tokens is not None
            else self._safe_start(len(self._messages) - keep_recent)
        )
        if start <= 0:
            return "No messages to compact."
        tail = list(self._messages[start:])
        prompt_check = is_prompt or (lambda m: m.role == "user" and bool(m.text.strip()))
        latest = next(
            (m for m in reversed(self._messages) if prompt_check(m)), None
        )
        if latest is not None and all(m is not latest for m in tail):
            tail.insert(0, latest)

        instruction = _SUMMARY_INSTRUCTIONS.get(summary_type, _SUMMARY_INSTRUCTIONS["detailed"])
        instruction += (
            f"\n\nThe last {len(self._messages) - start} messages stay in the conversation "
            "word for word; concentrate on everything before them. Reply with the "
            "summary as plain text only; do not call any tools."
        )
        request = [*self._messages, Message(role="user", content=instruction)]
        tool_defs = list(tools) if tools else None
        kwargs = dict(chat_kwargs or {})
        try:
            response = await provider.chat(
                messages=request, tools=tool_defs, system_prompt=self.system_prompt, **kwargs
            )
            summary_text = response.text.strip()
            if not summary_text:
                # A thinking model can spend the whole reply on reasoning, or
                # answer with a tool call, and return no text (qwen3.8-flash
                # did). Ask again without thinking; a backend that does not
                # take the knob raises into the fallback below.
                response = await provider.chat(
                    messages=request,
                    tools=tool_defs,
                    system_prompt=self.system_prompt,
                    **{**kwargs, "reasoning_effort": "none"},
                )
                summary_text = response.text.strip()
            if not summary_text:
                # Replacing the history with an empty summary erased the
                # whole conversation and reported success.
                raise ValueError("the model returned an empty summary")

            self.replace_history(
                [Message(role="system", content=f"Conversation summary:\n\n{summary_text}")] + tail
            )
            return (
                f"Context compacted. Summary:\n{summary_text[:200]}..."
                if len(summary_text) > 200
                else summary_text
            )

        except Exception as e:
            # No summary: keep the same tail, so the history still shrinks.
            self.replace_history(tail)
            return f"Compaction failed ({e}). Kept last {len(self._messages)} messages."

    def check_compaction_needed(
        self, max_tokens: int = 128000, threshold: float = 0.8
    ) -> bool:
        """Check if context compaction is needed based on token count.

        Args:
            max_tokens: Maximum allowed tokens (default: 128000)
            threshold: Trigger threshold as fraction of max (default: 0.8 = 80%)

        Returns:
            True if compaction is needed, False otherwise
        """
        from .utils import count_tokens_for_messages

        messages_list = self.to_list_for_compaction()
        current_tokens = count_tokens_for_messages(messages_list)

        return current_tokens > (max_tokens * threshold)

    @property
    def token_count(self) -> int:
        """Get approximate token count of current session.

        Returns:
            Estimated number of tokens in conversation history
        """
        from .utils import count_tokens_for_messages

        messages_list = self.to_list_for_compaction()
        return count_tokens_for_messages(messages_list)


_SUMMARY_INSTRUCTIONS = {
    "brief": (
        "Summarise the conversation so far in at most 200 words: key decisions, "
        "files created or modified, current task status, and constraints."
    ),
    "structured": (
        "Summarise the conversation so far under these headings, 2-3 bullets each: "
        "Key Decisions; Files Modified; Tasks & Progress; Project State; Important Constraints."
    ),
    "detailed": (
        "Summarise the conversation so far so that the work can continue from the "
        "summary alone. Cover: the user's requests and goals; key decisions and why; "
        "every file created, modified or deleted and what changed; commands run and "
        "what they showed (errors, test results); what is finished and what remains; "
        "and constraints or preferences the user stated. Keep exact file paths, "
        "names and error messages."
    ),
}
