from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .types import (
    ContentBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    text_message,
    tool_result_message,
)


@dataclass
class Session:
    """Manages conversation state and persistence.

    Supports automatic context compression when message count exceeds threshold,
    which helps manage token usage in long conversations.
    """

    system_prompt: str = ""
    _messages: list[Message] = field(default_factory=list)
    max_messages: int = 50  # Maximum messages before compression kicks in
    summary_threshold: int = 30  # Start summarizing after this many messages

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    def add(self, role: Literal["user", "assistant", "system"], content: str) -> Message:
        msg = text_message(role, content)
        self._messages.append(msg)
        # Check if we need to compress after adding a message
        self._maybe_compress()
        return msg

    def add_message(self, message: Message) -> None:
        self._messages.append(message)
        # Check if we need to compress after adding a message
        self._maybe_compress()

    def add_tool_results(self, results: list[ToolResultBlock]) -> Message:
        msg = tool_result_message(results)
        self._messages.append(msg)
        return msg

    def _maybe_compress(self) -> None:
        """Compress messages if they exceed the threshold."""
        if len(self._messages) <= self.summary_threshold:
            return

        # Compress when we exceed max_messages
        if len(self._messages) > self.max_messages:
            self._compress_old_messages()

    def _compress_old_messages(self) -> None:
        """Compress older messages by summarizing them.

        Keeps the system prompt, first few messages (for context), and recent messages.
        Older tool calls and their results are summarized into a single summary message.
        """
        if len(self._messages) <= 5:
            return  # Can't compress further

        # Keep: system prompt + first 2 user-assistant pairs + last 3 messages
        keep_count = min(7, len(self._messages) - 3)

        # Messages to summarize (everything between kept start and end)
        messages_to_summarize = self._messages[keep_count:-3] if len(self._messages) > 10 else []

        if not messages_to_summarize:
            return

        # Create a summary of the summarized messages
        summary_content = self._create_summary(messages_to_summarize)

        # Keep system prompt, initial context, summary, and recent messages
        new_messages = (
            self._messages[:keep_count] +
            [Message(role="assistant", content=summary_content)] +
            self._messages[-3:] if len(self._messages) > 3 else []
        )

        self._messages = new_messages

    def _create_summary(self, messages: list[Message]) -> str:
        """Create a summary of the given messages.

        Args:
            messages: Messages to summarize (typically tool calls and results)

        Returns:
            Summary text describing what happened in these messages
        """
        if not messages:
            return "No recent activity."

        # Count operations by type
        operations = {}
        for msg in messages:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        name = block.name
                        operations[name] = operations.get(name, 0) + 1

        # Generate summary text
        parts = ["\n\n--- Conversation Summary ---"]

        if operations:
            parts.append("\nRecent operations:")
            for op_name, count in sorted(operations.items(), key=lambda x: -x[1]):
                parts.append(f"  • {op_name}: {count} time(s)")

        # Add file changes summary
        files_changed = set()
        for msg in messages:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock) and block.name in ('write', 'edit'):
                        files_changed.add(block.arguments.get('file') or block.arguments.get('path'))

        if files_changed:
            parts.append(f"\nFiles modified: {len(files_changed)}")
            for f in list(files_changed)[:5]:  # Limit to 5 files
                parts.append(f"  • {f}")
            if len(files_changed) > 5:
                parts.append(f"  ... and {len(files_changed) - 5} more")

        parts.append("\n--- End Summary ---\n")
        return "".join(parts)

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
                    elif isinstance(b, ToolUseBlock):
                        blocks.append({
                            "type": "tool_use",
                            "id": b.id,
                            "name": b.name,
                            "arguments": b.arguments,
                        })
                    elif isinstance(b, ToolResultBlock):
                        blocks.append({
                            "type": "tool_result",
                            "tool_use_id": b.tool_use_id,
                            "content": b.content,
                            "is_error": b.is_error,
                        })
                out.append({"role": msg.role, "content": blocks})
        return out

    def save(self, path: str | Path) -> None:
        """Save session to a JSON file.

        Args:
            path: Path to save the session to
        """
        data = {
            "system_prompt": self.system_prompt,
            "messages": self.to_list(),
        }
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
                    elif block_type == "tool_use":
                        blocks.append(ToolUseBlock(
                            id=block_data["id"],
                            name=block_data["name"],
                            arguments=block_data["arguments"],
                        ))
                    elif block_type == "tool_result":
                        blocks.append(ToolResultBlock(
                            tool_use_id=block_data["tool_use_id"],
                            content=block_data["content"],
                            is_error=block_data.get("is_error", False),
                        ))
                session._messages.append(Message(role=role, content=blocks))

        return session
