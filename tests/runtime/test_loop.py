"""Tests for the shared run-loop policy in openagent.runtime.loop."""

from __future__ import annotations

import pytest

from openagent.core.types import Message, ToolUseBlock
from openagent.runtime.loop import (
    EMPTY_RESPONSE_EXHAUSTED_MESSAGE,
    EMPTY_RESPONSE_RETRY_MESSAGE,
    EmptyResponseGuard,
    LoopSettings,
    is_empty_final_response,
    maybe_compact_context,
    next_id,
)


class FakeSession:
    """Minimal session stand-in for loop-policy tests."""

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.compact_calls: list[dict] = []
        self.needed = False

    def add(self, role: str, content: str) -> None:
        self.messages.append(Message(role=role, content=content))

    def check_compaction_needed(self, *, max_tokens: int, threshold: float) -> bool:
        return self.needed

    async def compact_context(self, *, provider, keep_recent: int, summary_type: str) -> str:
        self.compact_calls.append(
            {"provider": provider, "keep_recent": keep_recent, "summary_type": summary_type}
        )
        return "compacted summary text"


class FakeProvider:
    pass


# --- LoopSettings -----------------------------------------------------------


def test_loop_settings_splits_loop_keys_from_provider_kwargs() -> None:
    settings, provider_kwargs = LoopSettings.from_kwargs(
        {"temperature": 0, "max_context_tokens": 123, "compact_threshold": 0.5,
         "disable_compaction": True, "max_empty_response_retries": 1}
    )

    assert settings.max_context_tokens == 123
    assert settings.compact_threshold == 0.5
    assert settings.disable_compaction is True
    assert settings.max_empty_response_retries == 1
    # Core-only keys must never reach the provider.
    assert provider_kwargs == {"temperature": 0}


def test_loop_settings_defaults() -> None:
    settings, provider_kwargs = LoopSettings.from_kwargs({})
    assert settings.max_context_tokens == 128000
    assert settings.compact_threshold == 0.8
    assert settings.disable_compaction is False
    assert settings.max_empty_response_retries == 3
    assert provider_kwargs == {}


def test_loop_settings_leaves_original_kwargs_untouched() -> None:
    kwargs = {"temperature": 0, "max_context_tokens": 10}
    LoopSettings.from_kwargs(kwargs)
    assert kwargs == {"temperature": 0, "max_context_tokens": 10}


def test_apply_instance_override_reads_agent_attribute() -> None:
    settings, _ = LoopSettings.from_kwargs({})

    class Agent:
        disable_compaction = True

    settings.apply_instance_override(Agent())
    assert settings.disable_compaction is True


def test_apply_instance_override_keeps_explicit_kwarg() -> None:
    settings, _ = LoopSettings.from_kwargs({"disable_compaction": True})

    settings.apply_instance_override(object())
    assert settings.disable_compaction is True


# --- EmptyResponseGuard ----------------------------------------------------


def test_guard_exhausts_after_max_retries() -> None:
    guard = EmptyResponseGuard(max_retries=2)
    for _ in range(2):
        guard.record_empty()
        assert not guard.exhausted
    guard.record_empty()
    assert guard.exhausted


def test_guard_success_resets_counter() -> None:
    guard = EmptyResponseGuard(max_retries=1)
    guard.record_empty()
    guard.record_success()
    guard.record_empty()
    assert not guard.exhausted


def test_guard_ensure_retry_prompt_appends_once() -> None:
    session = FakeSession()
    guard = EmptyResponseGuard(max_retries=3)

    guard.ensure_retry_prompt(session)
    guard.ensure_retry_prompt(session)

    assert len(session.messages) == 1
    assert session.messages[0].text == EMPTY_RESPONSE_RETRY_MESSAGE


def test_empty_response_exhausted_message_is_stable() -> None:
    # tests/test_agent.py asserts on this substring; keep the wording pinned.
    assert "empty responses repeatedly" in EMPTY_RESPONSE_EXHAUSTED_MESSAGE


# --- is_empty_final_response -----------------------------------------------


def test_text_response_is_not_empty() -> None:
    assert not is_empty_final_response(Message(role="assistant", content="hi"))


def test_blank_text_response_is_empty() -> None:
    assert is_empty_final_response(Message(role="assistant", content="   "))


def test_tool_call_only_response_is_not_empty() -> None:
    block = ToolUseBlock(id="t1", name="read", arguments={"file": "a.py"})
    assert not is_empty_final_response(Message(role="assistant", content=[block]))


# --- maybe_compact_context ------------------------------------------------


async def test_maybe_compact_skips_when_disabled() -> None:
    session = FakeSession()
    session.needed = True
    settings, _ = LoopSettings.from_kwargs({"disable_compaction": True})

    assert await maybe_compact_context(session, FakeProvider(), settings) is None
    assert session.compact_calls == []


async def test_maybe_compact_skips_when_under_threshold() -> None:
    session = FakeSession()
    session.needed = False
    settings, _ = LoopSettings.from_kwargs({})

    assert await maybe_compact_context(session, FakeProvider(), settings) is None
    assert session.compact_calls == []


async def test_maybe_compact_returns_summary() -> None:
    session = FakeSession()
    session.needed = True
    settings, _ = LoopSettings.from_kwargs({})
    provider = FakeProvider()

    summary = await maybe_compact_context(session, provider, settings)

    assert summary == "compacted summary text"
    assert session.compact_calls == [
        {"provider": provider, "keep_recent": 5, "summary_type": "detailed"}
    ]


# --- ids ------------------------------------------------------------------


def test_next_id_uses_prefix_and_is_unique() -> None:
    a, b = next_id("run"), next_id("run")
    assert a.startswith("run_")
    assert a != b


@pytest.mark.parametrize("prefix", ["run", "msg"])
def test_next_id_prefixes(prefix: str) -> None:
    assert next_id(prefix).startswith(f"{prefix}_")


@pytest.mark.parametrize(
    ("window", "max_output", "budget"),
    [
        (32768, 8192, 24576),
        (500000, 32000, 468000),
        # A reply share bigger than a quarter of the window is capped there.
        (32768, 32000, 24576),
        (200000, None, 200000),
        (None, 8192, None),
        (0, 8192, None),
    ],
)
def test_history_budget_leaves_room_for_the_reply(window, max_output, budget):
    from openagent.runtime.loop import history_budget

    assert history_budget(window, max_output) == budget
