"""Clearing old tool outputs: the cheap first step of compaction.

Most of a long agent run is tool output read once and never needed again:
file contents, test logs, command output. Replacing the old ones with a
one-line placeholder frees most of the context without calling the model, so
nothing can come back empty and no decision is lost — the assistant's own
messages, which say what each output showed, stay as they are.

The cleared text is saved to a file first, and the placeholder names the
``recall_output`` id that reads it back. Clearing runs on a copy: if it does
not free enough, the caller discards the copy and summarises the original, so
a summary always sees the outputs it summarises.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openagent.core.types import Message, ToolResultBlock, ToolUseBlock
from openagent.core.utils import count_tokens_for_messages

#: Results newer than the last this-many tool steps are never cleared.
KEEP_TOOL_STEPS = 3
#: Results under this many tokens are kept: clearing them saves nothing, and
#: they are often the useful part ("tests passed", "saved").
MIN_CLEAR_TOKENS = 200
#: A string argument of an old tool call above this many tokens is shortened
#: (write_file's content is the whole file, which is on disk anyway).
MIN_CLEAR_INPUT_TOKENS = 500
#: Results of tools that hold state or the person's words are never cleared.
KEEP_TOOLS = frozenset(
    {"todo_list", "todo_write", "todo_update", "ask_user_question", "recall_output"}
)
CLEARED_PREFIX = "[output cleared to save context"
_INPUT_CLEARED = "[cleared to save context"
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")


def output_file(outputs_dir: str | Path, ref: str) -> Path:
    """Where a cleared output with recall id ``ref`` is saved."""
    return Path(outputs_dir) / f"{_SAFE_ID.sub('_', ref)}.txt"


@dataclass
class Clearing:
    """A cleared copy of the history, not yet applied."""

    messages: list[Message]
    cleared: int
    tokens_before: int
    tokens_after: int
    #: (recall id, text) pairs to write when the copy is applied.
    saves: list[tuple[str, str]] = field(default_factory=list)

    @property
    def freed(self) -> int:
        return self.tokens_before - self.tokens_after

    def write(self, outputs_dir: str | Path | None) -> None:
        if outputs_dir is None:
            return
        for ref, text in self.saves:
            path = output_file(outputs_dir, ref)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")


def _tokens(text: str) -> int:
    return count_tokens_for_messages([{"role": "user", "content": text}]) - 4


def _message_tokens(message: Message) -> int:
    from openagent.core.session import Session

    probe = Session()
    probe.replace_history([message])
    return probe.token_count


def _protected_from(messages: list[Message], keep_tokens: int, keep_steps: int) -> int:
    """Index of the first message clearing must not touch: the newest
    ``keep_steps`` tool steps or the newest ``keep_tokens``, whichever
    reaches further back."""
    by_steps = len(messages)
    steps = 0
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role == "assistant" and messages[index].has_tool_calls:
            steps += 1
            by_steps = index
            if steps >= keep_steps:
                break
    by_tokens = len(messages)
    used = 0
    for index in range(len(messages) - 1, -1, -1):
        used += _message_tokens(messages[index])
        if used > keep_tokens:
            break
        by_tokens = index
    return min(by_steps, by_tokens)


def _describe_call(call: ToolUseBlock | None) -> str:
    if call is None:
        return ""
    args = json.dumps(call.arguments, ensure_ascii=False)
    return args if len(args) <= 80 else args[:77] + "..."


def clear_old_tool_outputs(
    messages: list[Message],
    *,
    target_tokens: int,
    keep_tokens: int,
    outputs_dir: str | Path | None = None,
    keep_steps: int = KEEP_TOOL_STEPS,
) -> Clearing:
    """Clear old tool outputs, oldest first, until the history fits
    ``target_tokens``. Works on a copy; ``messages`` is not changed."""
    working = copy.deepcopy(messages)
    before = sum(_message_tokens(m) for m in working)
    total = before
    result = Clearing(messages=working, cleared=0, tokens_before=before, tokens_after=before)
    protect = _protected_from(working, keep_tokens, keep_steps)
    calls = {
        block.id: block
        for message in working
        if isinstance(message.content, list)
        for block in message.content
        if isinstance(block, ToolUseBlock)
    }
    recall = outputs_dir is not None

    for index in range(protect):
        if total <= target_tokens:
            break
        message = working[index]
        if not isinstance(message.content, list):
            continue
        for block in message.content:
            if total <= target_tokens:
                break
            if isinstance(block, ToolResultBlock):
                if block.tool_name in KEEP_TOOLS or block.content.startswith(CLEARED_PREFIX):
                    continue
                cost = _tokens(block.content)
                if cost < MIN_CLEAR_TOKENS:
                    continue
                call = calls.get(block.tool_use_id)
                first = next((line.strip() for line in block.content.splitlines() if line.strip()), "")
                where = (
                    f'recall_output("{block.tool_use_id}") reads it'
                    if recall
                    else "rerun the tool if you need it"
                )
                block.content = (
                    f"{CLEARED_PREFIX}: {block.tool_name} {_describe_call(call)} · {cost:,} tokens"
                    f' · first line: "{first[:120]}" — {where}]'
                )
                result.saves.append((block.tool_use_id, _original(messages, index, block)))
                total -= cost - _tokens(block.content)
                result.cleared += 1
            elif isinstance(block, ToolUseBlock):
                for key, value in list(block.arguments.items()):
                    if not isinstance(value, str) or value.startswith(_INPUT_CLEARED):
                        continue
                    cost = _tokens(value)
                    if cost < MIN_CLEAR_INPUT_TOKENS:
                        continue
                    ref = f"{block.id}.{key}"
                    lines = value.splitlines()
                    where = f'recall_output("{ref}") reads it' if recall else "it is not kept"
                    block.arguments[key] = (
                        f"{_INPUT_CLEARED}: {cost:,} tokens, {len(lines)} lines, "
                        f"beginning {lines[0][:80] if lines else ''!r} — {where}]"
                    )
                    result.saves.append((ref, value))
                    total -= cost - _tokens(block.arguments[key])
                    result.cleared += 1

    result.tokens_after = total
    return result


def _original(messages: list[Message], index: int, cleared: ToolResultBlock) -> str:
    """The uncleared text of ``cleared``, from the history it was copied from."""
    source = messages[index].content
    for block in source if isinstance(source, list) else ():
        if isinstance(block, ToolResultBlock) and block.tool_use_id == cleared.tool_use_id:
            return block.content
    return ""


__all__ = [
    "CLEARED_PREFIX",
    "KEEP_TOOLS",
    "KEEP_TOOL_STEPS",
    "Clearing",
    "clear_old_tool_outputs",
    "output_file",
]
