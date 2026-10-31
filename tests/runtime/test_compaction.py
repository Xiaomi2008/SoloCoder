"""Compaction: what it keeps, what it summarises, and the request it sends."""

from __future__ import annotations

from typing import Any

from openagent.core.session import Session
from openagent.core.types import Message, ToolDef, ToolResultBlock, ToolUseBlock
from openagent.runtime.loop import SCREENSHOT_FOLLOWUP_TEXT, is_user_prompt

TOOLS = [ToolDef(name="bash", description="Run a command.", parameters={"type": "object"})]


class Summariser:
    """Records each summary request; answers from ``replies``."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies) or ["the summary"]
        self.requests: list[dict[str, Any]] = []

    async def chat(self, messages, tools=None, system_prompt="", **kwargs):
        self.requests.append(
            {"messages": list(messages), "tools": tools, "system_prompt": system_prompt, "kwargs": kwargs}
        )
        return Message(role="assistant", content=self.replies.pop(0))


def agent_run(steps: int = 30, output: str = "out " * 200) -> Session:
    """Two prompts, the second followed by ``steps`` tool steps."""
    session = Session(system_prompt="You are a coding agent.")
    session.add_message(Message(role="user", content="first prompt"))
    session.add_message(Message(role="assistant", content="first answer"))
    session.add_message(Message(role="user", content="fix the failing test"))
    for i in range(steps):
        session.add_message(
            Message(role="assistant", content=[ToolUseBlock(id=f"t{i}", name="bash", arguments={"cmd": f"step {i}"})])
        )
        session.add_tool_results([ToolResultBlock(tool_use_id=f"t{i}", tool_name="bash", content=output)])
    return session


async def test_a_long_tool_run_actually_shrinks():
    """The tail used to have to start on a user message; in an agent run that
    is the prompt before every tool step, so 61 of 63 messages were kept."""
    session = agent_run()
    before = len(session.messages)

    await session.compact_context(Summariser(), keep_tokens=1000, tools=TOOLS, is_prompt=is_user_prompt)

    assert before == 63
    # Summary + the pinned request + four ~200-token steps that fit in 1,000.
    assert len(session.messages) == 10
    assert session.messages[0].role == "system"
    assert session.messages[0].text.startswith("Conversation summary:")


async def test_the_latest_request_is_kept_word_for_word():
    session = agent_run()

    await session.compact_context(Summariser(), keep_tokens=1000, tools=TOOLS, is_prompt=is_user_prompt)

    assert session.messages[1].role == "user"
    assert session.messages[1].text == "fix the failing test"
    assert session.messages[2].role == "assistant"


async def test_a_tool_result_never_loses_its_call():
    session = agent_run()

    for keep in (1, 300, 700, 1500):
        trial = agent_run()
        await trial.compact_context(Summariser(), keep_tokens=keep, tools=TOOLS)
        kept = trial.messages[1:]
        for index, message in enumerate(kept):
            if message.role == "tool_result":
                call = kept[index - 1]
                assert call.role == "assistant" and call.has_tool_calls, keep
    assert session.messages


async def test_the_tail_is_sized_by_tokens_not_count():
    small = agent_run(output="ok")
    large = agent_run(output="out " * 2000)

    await small.compact_context(Summariser(), keep_tokens=1500, tools=TOOLS)
    await large.compact_context(Summariser(), keep_tokens=1500, tools=TOOLS)

    assert len(small.messages) > len(large.messages)


async def test_the_summary_request_starts_with_the_conversation_itself():
    """So a prompt cache serves the history and only the instruction is new."""
    session = agent_run(steps=5)
    history = list(session.messages)
    summariser = Summariser()

    await session.compact_context(
        summariser, keep_tokens=200, tools=TOOLS, chat_kwargs={"reasoning_effort": "low"}
    )

    request = summariser.requests[0]
    assert request["messages"][:-1] == history
    assert request["messages"][-1].role == "user"
    assert "do not call any tools" in request["messages"][-1].text
    assert request["system_prompt"] == "You are a coding agent."
    assert request["tools"] == TOOLS
    assert request["kwargs"] == {"reasoning_effort": "low"}


async def test_a_failed_summary_still_shrinks_to_the_tail():
    session = agent_run()

    class Broken:
        async def chat(self, *args, **kwargs):
            raise RuntimeError("server down")

    result = await session.compact_context(Broken(), keep_tokens=1000, tools=TOOLS, is_prompt=is_user_prompt)

    assert result.startswith("Compaction failed")
    assert len(session.messages) < 10
    assert session.messages[0].text == "fix the failing test"


async def test_nothing_to_summarise_leaves_the_history_alone():
    session = Session()
    session.add_message(Message(role="user", content="hi " * 5000))

    result = await session.compact_context(Summariser(), keep_tokens=10)

    assert result == "No messages to compact."
    assert len(session.messages) == 1


def test_a_screenshot_follow_up_is_not_the_persons_request():
    assert is_user_prompt(Message(role="user", content="fix the failing test"))
    assert not is_user_prompt(Message(role="user", content=f"{SCREENSHOT_FOLLOWUP_TEXT} more"))
    assert not is_user_prompt(Message(role="assistant", content="done"))
