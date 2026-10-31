"""Anthropic only caches up to a cache_control marker; the provider sets them."""

from __future__ import annotations

from openagent.provider.anthropic import mark_cache_breakpoints


def test_tools_system_and_the_last_message_are_marked():
    kwargs = {
        "system": "You are a coding agent.",
        "tools": [{"name": "a"}, {"name": "b"}],
        "messages": [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "out"}]},
        ],
    }
    mark_cache_breakpoints(kwargs)

    assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in kwargs["tools"][0]
    assert kwargs["system"] == [
        {"type": "text", "text": "You are a coding agent.", "cache_control": {"type": "ephemeral"}}
    ]
    assert kwargs["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["messages"][0]["content"] == "first"


def test_a_plain_string_message_becomes_a_marked_block():
    kwargs = {"messages": [{"role": "user", "content": "hello"}]}
    mark_cache_breakpoints(kwargs)
    assert kwargs["messages"][0]["content"] == [
        {"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}
    ]


def test_nothing_to_mark_is_left_alone():
    kwargs = {"messages": [{"role": "user", "content": [{"type": "text", "text": ""}]}]}
    mark_cache_breakpoints(kwargs)
    assert "cache_control" not in kwargs["messages"][0]["content"][0]
