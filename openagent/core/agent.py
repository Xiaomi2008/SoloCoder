from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

# Changed imports for core modules
from .bash_manager import BashManager, get_bash_manager
from .logging import AgentLogger
from .session import Session
from .skill_manager import (
    SkillManager,
    SlashCommandRegistry,
    get_command_registry,
    get_skill_manager,
)
from .task_manager import TaskManager, get_task_manager
from .tool import ToolContext, ToolRegistry, tool
from .types import Message, ToolResultBlock
from openagent.runtime.agent import RuntimeAgent
from openagent.runtime.loop import (
    EMPTY_RESPONSE_EXHAUSTED_MESSAGE,
    EMPTY_RESPONSE_RETRY_MESSAGE as _RETRY_PROMPT,
    EmptyResponseGuard,
    LoopSettings,
    add_screenshot_followup,
    is_empty_final_response,
    maybe_compact_context,
)

# BaseProvider is likely in parent package or sibling 'provider' package
# Since we are in core/, provider/ is '../provider/'
# But 'openagent.provider' is absolute import, which is fine and clearer.
from openagent.provider.base import BaseProvider


class Agent:
    EMPTY_RESPONSE_RETRY_MESSAGE = _RETRY_PROMPT
    SCREENSHOT_TOOL_IMAGE_MIME_TYPE = "image/jpeg"
    SCREENSHOT_FOLLOWUP_MAX_TOKENS = 2048

    def __init__(
        self,
        provider: BaseProvider,
        system_prompt: str = "",
        tools: list[Callable[..., Any]] | None = None,
        max_turns: int = 10,
        agent_id: str | None = None,
        bash_manager: BashManager | None = None,
        task_manager: TaskManager | None = None,
        skill_manager: SkillManager | None = None,
        mcp_client: Any | None = None,  # MCP client for tool discovery
        working_dir: str | None = None,
    ) -> None:
        self.provider = provider
        self.session = Session(system_prompt=system_prompt)
        self.max_turns = max_turns
        self.tool_registry = ToolRegistry()
        self._logger = AgentLogger(agent_id)
        # The directory relative tool paths mean. Sub-agents inherit it.
        self.working_dir = str(Path(working_dir).expanduser().resolve()) if working_dir else str(Path.cwd())

        # Initialize managers (use provided or create new instances)
        self.bash_manager = bash_manager or get_bash_manager()
        self.task_manager = task_manager or get_task_manager()
        self.skill_manager = skill_manager or get_skill_manager()
        self.command_registry = get_command_registry()

        if tools:
            for fn in tools:
                if not hasattr(fn, "_tool_name"):
                    fn = tool(fn)
                self.tool_registry.register(fn)

        # MCP tools are discovered on the first run, not here: asyncio.run()
        # fails when the agent is built inside a running loop, and outside one
        # it connects the client on a loop that closes as soon as it returns.
        self._mcp_client = mcp_client
        self._mcp_pending = mcp_client is not None

    @property
    def tool_context(self) -> ToolContext:
        """What tools get for this session (the model never sees it)."""
        return ToolContext(working_dir=self.working_dir)

    async def _ensure_mcp_tools(self) -> None:
        """Connect the MCP client and register its tools, once."""
        if self._mcp_pending:
            self._mcp_pending = False
            await self._integrate_mcp_tools()

    async def _integrate_mcp_tools(self) -> None:
        """Discover and integrate MCP tools from the client."""
        try:
            # Ensure the client is connected
            if hasattr(self._mcp_client, "__aenter__"):
                await self._mcp_client.__aenter__()

            # Get MCP tools
            mcp_tools = await self._mcp_client.get_tools()

            # Register each MCP tool
            for tool_fn in mcp_tools:
                if hasattr(tool_fn, "_tool_name"):
                    self.tool_registry.register(tool_fn)
                    self._logger.info(f"Registered MCP tool: {tool_fn._tool_name}")

        except Exception as e:
            self._logger.error(f"Failed to integrate MCP tools: {e}")

    @property
    def messages(self) -> list[Message]:
        return self.session.messages

    async def run(self, user_input: str, **kwargs: Any) -> str:
        self._logger.run_start(user_input)
        await self._ensure_mcp_tools()
        if len(self.tool_registry) == 0:
            runtime_agent = RuntimeAgent(
                provider=self.provider,
                system_prompt=self.session.system_prompt,
            )
            runtime_agent.session = self.session
            try:
                result = await runtime_agent.run(user_input, **kwargs)
            except RuntimeError as exc:
                self.session = runtime_agent.session
                if "empty responses repeatedly" in str(exc):
                    return str(exc)
                raise

            self.session = runtime_agent.session
            return result.output_text

        self.session.add("user", user_input)
        result = await self._loop(**kwargs)
        return result

    async def run_multimodal(
        self,
        text: str | None = None,
        image_data: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Run the agent with multimodal input (text and/or images).

        For agents with tools, this adds the image to the session and runs the normal loop.

        Args:
            text: Optional text prompt
            image_data: Optional base64-encoded image data

        Returns:
            The agent's response text
        """
        self._logger.run_start(text or "Image analysis")
        await self._ensure_mcp_tools()

        # Add multimodal input to session
        if image_data:
            self.session.add_user_multimodal(text=text, image_data=image_data)
        elif text:
            self.session.add("user", text)
        else:
            self.session.add("user", "Analyze this image.")

        result = await self._loop(**kwargs)
        return result

    @staticmethod
    def _is_empty_final_response(message: Message) -> bool:
        """Deprecated alias for :func:`openagent.runtime.loop.is_empty_final_response`."""
        return is_empty_final_response(message)

    def _add_tool_result_followup_messages(
        self, results: list[ToolResultBlock]
    ) -> bool:
        """Shared with the streaming runner — see :func:`add_screenshot_followup`."""
        return add_screenshot_followup(
            self.session, results, mime_type=self.SCREENSHOT_TOOL_IMAGE_MIME_TYPE
        )

    def _provider_kwargs_for_turn(
        self, base_kwargs: dict[str, Any], screenshot_followup_added: bool
    ) -> dict[str, Any]:
        provider_kwargs = dict(base_kwargs)
        if screenshot_followup_added:
            provider_kwargs["max_tokens"] = min(
                provider_kwargs.get("max_tokens", 8192),
                self.SCREENSHOT_FOLLOWUP_MAX_TOKENS,
            )
        return provider_kwargs

    async def _loop(self, **kwargs: Any) -> str:
        tool_defs = (
            self.tool_registry.definitions if len(self.tool_registry) > 0 else None
        )
        response: Message | None = None
        completed_turns = 0
        awaiting_final_response = False
        #: One turn past the limit, for the final text after the last tool
        #: batch — and only once. Granting it every time the flag is set made a
        #: model that keeps calling tools loop forever: each iteration ends by
        #: re-arming the flag, so the exit condition was never reached.
        grace_used = False

        # Loop settings are split out of kwargs so they never reach the provider.
        settings, provider_kwargs = LoopSettings.from_kwargs(
            {**getattr(self, "loop_defaults", {}), **kwargs}
        )
        settings.apply_instance_override(self)
        guard = EmptyResponseGuard(settings.max_empty_response_retries)

        screenshot_followup_added = False

        while completed_turns < self.max_turns or (
            awaiting_final_response and not grace_used
        ):
            turn_number = min(completed_turns + 1, self.max_turns)
            self._logger.turn_start(turn_number, self.max_turns)

            # Check if context compaction is needed before sending request (unless disabled)
            summary = await maybe_compact_context(self.session, self.provider, settings)
            if summary:
                self._logger.info("Context approaching limit, compacting...")
                self._logger.info(f"Compacted context: {summary[:100]}...")

            turn_kwargs = self._provider_kwargs_for_turn(
                provider_kwargs, screenshot_followup_added
            )

            response = await self.provider.chat(
                messages=self.session.messages,
                tools=tool_defs,
                system_prompt=self.session.system_prompt,
                **turn_kwargs,
            )

            if is_empty_final_response(response):
                self._logger._logger.warning(
                    "Provider returned an empty assistant response; requesting a retry."
                )
                guard.record_empty()
                if guard.exhausted:
                    return EMPTY_RESPONSE_EXHAUSTED_MESSAGE
                guard.ensure_retry_prompt(self.session)
                continue

            guard.record_success()
            self.session.add_message(response)
            if awaiting_final_response and completed_turns >= self.max_turns:
                grace_used = True
            completed_turns += 1
            awaiting_final_response = False

            has_tools = response.has_tool_calls
            self._logger.turn_end(turn_number, has_tools)

            if not has_tools:
                self._logger.run_end(completed_turns)
                return response.text

            tool_tasks = [
                self.tool_registry.execute(tc, context=self.tool_context)
                for tc in response.tool_calls
            ]
            results = await asyncio.gather(*tool_tasks)

            self.session.add_tool_results(list(results))
            screenshot_followup_added = self._add_tool_result_followup_messages(
                list(results)
            )
            awaiting_final_response = True

        self._logger.max_turns_reached()
        if response is None:
            raise RuntimeError("Agent loop completed without receiving any response")
        if awaiting_final_response:
            return (
                "The model kept requesting more tool work after the turn limit was reached. "
                "Try again, increase `--max-turns`, or switch to a more reliable model."
            )
        if is_empty_final_response(response):
            return (
                "The model stopped with an empty response before finishing the task. "
                "Try again or switch to a more reliable model."
            )
        return response.text
