"""Contract tests for the public `openagent` API surface.

Guards against the two ways this package used to rot: names listed in `__all__`
that were never imported, and the same class exported under two names.
"""

from __future__ import annotations

import openagent


def test_all_names_are_importable() -> None:
    missing = [name for name in openagent.__all__ if not hasattr(openagent, name)]
    assert missing == []


def test_all_has_no_duplicates() -> None:
    assert len(openagent.__all__) == len(set(openagent.__all__))


def test_agent_names_are_distinct() -> None:
    """`Agent` is the full tool-capable agent; `RuntimeAgent` is the streaming one."""
    from openagent.core.agent import Agent as CoreAgent
    from openagent.runtime.agent import RuntimeAgent

    assert openagent.Agent is CoreAgent
    assert openagent.RuntimeAgent is RuntimeAgent
    assert openagent.Agent is not openagent.RuntimeAgent


def test_runtime_agent_alias_is_the_same_class() -> None:
    from openagent.runtime import Agent as RuntimeAlias
    from openagent.runtime import RuntimeAgent

    assert RuntimeAlias is RuntimeAgent


def test_display_helpers_are_exported() -> None:
    # `user_input` was long listed in __all__ without being imported.
    assert "x" in openagent.user_input("x")
    assert callable(openagent.format_file_list)
    assert callable(openagent.display_claude_code_block)
