"""SoloCoder agent composition."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Callable

from openagent.core.agent import Agent as BaseAgent
from openagent.core.bash_manager import BashManager, get_bash_manager
from openagent.core.skill_manager import SkillManager, get_skill_manager
from openagent.core.task_manager import TaskManager, get_task_manager
from openagent.provider.base import BaseProvider
from openagent.provider.openai import OpenAIProvider
from openagent.prompts import load_prompt
from openagent.tools import (
    ask_user_question,
    bash,
    bash_background,
    bash_output,
    edit,
    enter_plan_mode,
    exit_plan_mode,
    glob,
    grep,
    kill_shell,
    notebook_edit,
    read,
    screenshot,
    skill,
    todo_list,
    todo_update,
    todo_write,
    web_fetch,
    web_search,
    write,
)


def build_solocoder_tools(computer_use: bool = True) -> list[Callable[..., Any]]:
    """Build the default SoloCoder tool set.

    Args:
        computer_use: Include computer use tools (screenshot, click, etc.) for GUI automation.
    """
    tools: list[Callable[..., Any]] = [
        read,
        write,
        edit,
        glob,
        grep,
        notebook_edit,
        bash,
        bash_background,
        bash_output,
        kill_shell,
        todo_write,
        todo_update,
        todo_list,
        enter_plan_mode,
        exit_plan_mode,
        ask_user_question,
        skill,
        web_search,
        web_fetch,
    ]

    if computer_use:
        from openagent.tools.computer_use import (
            click,
            double_click,
            get_screen_resolution,
            key_combination,
            move_mouse,
            scroll,
            wait,
        )

        tools.extend([
            screenshot,
            click,
            double_click,
            key_combination,
            move_mouse,
            scroll,
            get_screen_resolution,
            wait,
        ])

    return tools


class CoderAgent(BaseAgent):
    """A coding-focused agent with SoloCoder's default tools."""

    DEFAULT_SYSTEM_PROMPT = ""

    def __init__(
        self,
        provider: BaseProvider | None = None,
        system_prompt: str | None = None,
        max_turns: int = 100,
        working_dir: str | None = None,
        bash_manager: BashManager | None = None,
        task_manager: TaskManager | None = None,
        skill_manager: SkillManager | None = None,
        max_context_tokens: int = 128000,
        compact_threshold: float = 0.8,
        disable_compaction: bool = False,
    ) -> None:
        if provider is None:
            provider = OpenAIProvider(model="gpt-4o")

        if system_prompt is None:
            system_prompt = load_prompt("solocoder")

        super().__init__(
            provider=provider,
            system_prompt=system_prompt,
            tools=build_solocoder_tools(),
            max_turns=max_turns,
            bash_manager=bash_manager or get_bash_manager(),
            task_manager=task_manager or get_task_manager(),
            skill_manager=skill_manager or get_skill_manager(),
        )

        self._working_dir = working_dir or str(Path.cwd())
        self.max_context_tokens = max_context_tokens
        self.compact_threshold = compact_threshold
        self.disable_compaction = disable_compaction

    @property
    def working_dir(self) -> str:
        return self._working_dir

    async def run(
        self,
        user_input: str,
        working_dir: str | None = None,
        **kwargs: Any,
    ) -> str:
        if working_dir:
            self._working_dir = working_dir
        kwargs.setdefault("max_context_tokens", self.max_context_tokens)
        kwargs.setdefault("compact_threshold", self.compact_threshold)
        kwargs.setdefault("disable_compaction", self.disable_compaction)
        return await super().run(user_input, **kwargs)

    async def run_multimodal(
        self,
        text: str | None = None,
        image_path: str | None = None,
        image_data: str | None = None,
        working_dir: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Run the agent with text and/or image input (vision-capable).

        Args:
            text: Optional text prompt
            image_path: Path to an image file to analyze
            image_data: Optional base64-encoded image data
            working_dir: Optional working directory override

        Returns:
            Agent response as string
        """
        if working_dir:
            self._working_dir = working_dir
        kwargs.setdefault("max_context_tokens", self.max_context_tokens)
        kwargs.setdefault("compact_threshold", self.compact_threshold)
        kwargs.setdefault("disable_compaction", self.disable_compaction)

        # Load image if provided
        if image_path:
            try:
                image_bytes = Path(image_path).read_bytes()
                image_data = base64.b64encode(image_bytes).decode("utf-8")
            except Exception as e:
                raise ValueError(f"Failed to read image {image_path}: {e}")

        return await super().run_multimodal(text=text, image_data=image_data, **kwargs)


async def create_coder(
    model: str = "gpt-4o",
    api_key: str | None = None,
    max_turns: int = 100,
    working_dir: str | None = None,
) -> CoderAgent:
    """Create a configured SoloCoder agent."""
    provider = OpenAIProvider(model=model, api_key=api_key)
    return CoderAgent(
        provider=provider,
        max_turns=max_turns,
        working_dir=working_dir,
    )


__all__ = ["CoderAgent", "build_solocoder_tools", "create_coder"]
