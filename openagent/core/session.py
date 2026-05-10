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

    Supports automatic context compression when message count or token budget
    exceeds threshold. Compression respects tool call/result pairing so that
    evicted messages never leave orphaned references.
    """

    system_prompt: str = ""
    _messages: list[Message] = field(default_factory=list)
    max_messages: int = 50  # Maximum messages before compression kicks in
    summary_threshold: int = 30  # Start summarizing after this many messages
    max_tokens: int = 80_000  # Approximate token budget before compression

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    def add(self, role: Literal["user", "assistant", "system"], content: str) -> Message:
        msg = text_message(role, content)
        self._messages.append(msg)
        self._maybe_compress()
        return msg

    def add_message(self, message: Message) -> None:
        self._messages.append(message)
        self._maybe_compress()

    def add_tool_results(self, results: list[ToolResultBlock]) -> Message:
        msg = tool_result_message(results)
        self._messages.append(msg)
        self._maybe_compress()
        return msg

    def _estimate_tokens(self, msg: Message) -> int:
        """Rough estimate of tokens in a message (~4 chars per token)."""
        if isinstance(msg.content, str):
            return max(4, len(msg.content) // 4)
        total = 0
        for block in msg.content:
            if isinstance(block, TextBlock):
                total += max(4, len(block.text) // 4)
            elif isinstance(block, ToolUseBlock):
                total += 12  # overhead for tool call structure
                total += len(json.dumps(block.arguments)) // 4
            elif isinstance(block, ToolResultBlock):
                total += 12  # overhead for tool result structure
                total += max(4, len(block.content) // 4)
        return total

    def _total_tokens(self) -> int:
        return sum(self._estimate_tokens(m) for m in self._messages)

    def _maybe_compress(self) -> None:
        """Compress messages if they exceed the threshold."""
        if len(self._messages) <= self.summary_threshold:
            return

        # Compress when we exceed max_messages or token budget
        if len(self._messages) > self.max_messages or self._total_tokens() > self.max_tokens:
            self._compress_old_messages()

    def _group_into_turns(self) -> list[list[Message]]:
        """Group messages into turns, keeping tool calls paired with results.

        A turn is a contiguous group of messages that should not be split:
        - assistant message with ToolUseBlocks + following tool_result message
        - standalone user/assistant/system messages each form their own turn
        """
        turns: list[list[Message]] = []
        i = 0
        while i < len(self._messages):
            msg = self._messages[i]
            turn = [msg]

            if msg.role == "assistant" and msg.has_tool_calls:
                # Include the next message if it's a tool_result (paired)
                if (i + 1 < len(self._messages) and
                        self._messages[i + 1].role == "tool_result"):
                    turn.append(self._messages[i + 1])
                    i += 2
                else:
                    i += 1
            else:
                i += 1

            turns.append(turn)
        return turns

    def _compress_old_messages(self) -> None:
        """Compress older messages by summarizing them.

        Groups messages into turns (tool calls paired with results), then keeps
        the first few turns for context, the last few turns for recency, and
        summarizes everything in between.
        """
        turns = self._group_into_turns()
        if len(turns) <= 5:
            return  # Not enough turns to compress

        # Keep first 2 turns for context and last 3 turns for recency
        keep_head = min(2, len(turns) - 1)
        keep_tail = min(3, len(turns) - keep_head)

        turns_to_summarize = turns[keep_head : len(turns) - keep_tail]
        if not turns_to_summarize:
            return

        # Flatten turns to summarize into a message list
        messages_to_summarize: list[Message] = []
        for turn in turns_to_summarize:
            messages_to_summarize.extend(turn)

        summary_content = self._create_summary(messages_to_summarize)

        head_messages = [m for t in turns[:keep_head] for m in t]
        tail_messages = [m for t in turns[len(turns) - keep_tail :] for m in t]

        self._messages = (
            head_messages +
            [Message(role="assistant", content=summary_content)] +
            tail_messages
        )

    def _create_summary(self, messages: list[Message]) -> str:
        """Create a summary of the given messages.

        Includes operation counts, file paths, brief result snippets, and errors.
        """
        if not messages:
            return "No recent activity."

        # Count operations by type and collect result snippets
        operations: dict[str, int] = {}
        files_changed: set[str] = set()
        result_snippets: list[str] = []
        errors: list[str] = []

        for msg in messages:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        operations[block.name] = operations.get(block.name, 0) + 1
                        if block.name in ("write", "edit"):
                            fp = block.arguments.get("file") or block.arguments.get("path")
                            if fp:
                                files_changed.add(fp)
                    elif isinstance(block, ToolResultBlock):
                        if block.is_error:
                            snippet = block.content[:120]
                            errors.append(snippet)
                        else:
                            snippet = block.content[:80].split("\n")[0]
                            if len(result_snippets) < 10:
                                result_snippets.append(snippet)

        parts = ["\n--- Conversation Summary ---"]

        if operations:
            parts.append("\nOperations:")
            for op_name, count in sorted(operations.items(), key=lambda x: -x[1]):
                parts.append(f"  - {op_name}: {count} time(s)")

        if files_changed:
            parts.append(f"\nFiles modified: {len(files_changed)}")
            for f in list(files_changed)[:5]:
                parts.append(f"  - {f}")
            if len(files_changed) > 5:
                parts.append(f"  ... and {len(files_changed) - 5} more")

        if result_snippets:
            parts.append("\nResults:")
            for s in result_snippets:
                parts.append(f"  - {s}")

        if errors:
            parts.append("\nErrors:")
            for e in errors[:3]:
                parts.append(f"  ! {e}")

        parts.append("--- End Summary ---")
        return "\n".join(parts)

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
