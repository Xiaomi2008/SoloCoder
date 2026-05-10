"""Prompt templates for OpenAgent agents."""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    """Load a prompt template by name (without extension).

    Args:
        name: The prompt file name (e.g. "solocoder" loads "solocoder.md")

    Returns:
        The prompt text content.
    """
    path = _PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8").strip()
