from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from openagent.provider.base import BaseProvider

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
        """Return a recent message slice that starts on a conversation boundary."""
        if keep_recent <= 0:
            return []

        start = max(0, len(self._messages) - keep_recent)

        for index in range(start, len(self._messages)):
            if self._messages[index].role in {"user", "system"}:
                start = index
                break
        else:
            while start > 0 and self._messages[start].role not in {"user", "system"}:
                start -= 1

        return list(self._messages[start:])

    async def compact_context(
        self,
        provider: BaseProvider,
        keep_recent: int = 5,
        summary_type: str = "detailed",
    ) -> str:
        """Compact conversation history by summarizing past messages.

        Args:
            provider: LLM provider for generating summary
            keep_recent: Number of recent messages to preserve (default: 5)
            summary_type: Type of summary ("brief", "detailed", "structured")

        Returns:
            Generated summary text

        Note:
            This method replaces old messages with a summary while keeping
            the most recent N messages for immediate context.
        """
        from .utils import count_tokens_for_messages

        # Get current message list in API format
        messages = self.to_list_for_compaction()

        if len(messages) <= keep_recent:
            return "No messages to compact."

        # Prepare summary prompt based on type
        system_prompt = """You are a conversation summarizer for a coding agent. Your task is to create a concise yet informative summary of the conversation history that preserves important decisions, file changes, and project state."""

        if summary_type == "brief":
            user_prompt = """Create a brief summary (max 200 words) covering:
1. Key decisions made during this session
2. Files created or modified and their purposes
3. Current task status
4. Any important constraints or requirements

Keep it concise but preserve essential information."""
        elif summary_type == "structured":
            user_prompt = """Create a structured summary with the following sections:

## Key Decisions
- List major decisions made

## Files Modified
- File paths and what was changed/purpose

## Tasks & Progress
- Current task status and completion percentage

## Project State
- Brief description of current project state

## Important Constraints
- Any limitations or requirements to remember

Keep each section concise (2-3 bullet points max)."""
        else:  # detailed
            user_prompt = """Create a comprehensive summary covering:
1. Key decisions made during this session and their rationale
2. All files created, modified, or deleted with their purposes
3. Tasks that were started/completed and current status
4. Important constraints, requirements, or preferences mentioned
5. Current project state and next steps

Be thorough but avoid unnecessary details."""

        # Prepare messages for summarization (exclude the last few to keep context)
        summary_messages = (
            messages[:-keep_recent] if len(messages) > keep_recent else []
        )

        # Create a compacted message list with summary placeholder
        compaction_request = {
            "role": "user",
            "content": f"""Here is part of our conversation history. Please summarize the key information:

{json.dumps(summary_messages, indent=2)}

Please provide the summary now.""",
        }

        # Call LLM to generate summary
        try:
            response = await provider.chat(
                messages=[
                    Message(role="system", content=system_prompt),
                    Message(role="user", content=compaction_request["content"]),
                ],
                tools=None,
                system_prompt="",
            )

            summary_text = response.text

            # Replace old messages with summary and keep recent ones
            self.replace_history(
                [
                    Message(
                        role="system",
                        content=f"Conversation summary:\n\n{summary_text}",
                    )
                ]
                + self._get_compaction_tail(keep_recent)
            )

            return (
                f"Context compacted. Summary:\n{summary_text[:200]}..."
                if len(summary_text) > 200
                else summary_text
            )

        except Exception as e:
            # Fallback: simple truncation without LLM summarization
            self.replace_history(self._get_compaction_tail(keep_recent * 2))
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
