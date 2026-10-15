from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

# Changed imports for core modules
from .bash_manager import BashManager, get_bash_manager
from .display import (
    bold,
    cyan,
    dim,
    diff_addition,
    diff_deletion,
    display_tool_call_claude_style,
    display_tool_result_claude_style,
    green,
    red,
    yellow,
)


def display_write_result(file_path: str, result_content: str) -> None:
    """Display write operation result with file info."""
    q = '"'
    print(f"  ➜ {bold('write')}({cyan(q + file_path + q)})")

    # Parse the success message to show bytes written
    if "Successfully wrote" in result_content:
        parts = result_content.split()
        for i, part in enumerate(parts):
            if part == "wrote":
                bytes_written = parts[i + 1]
                print(f"    ⎿ {green(bytes_written)} to {cyan(file_path)}")
                break

    # Extract and show the actual content that was written (skip the success message)
    lines = result_content.split("\n")
    content_start = False
    for line in lines:
        if "Successfully wrote" in line:
            continue
        if line.strip():  # Non-empty line
            if not content_start:
                print()
                content_start = True
            print(f"    {dim(line)}")

    if content_start:
        print()


def display_edit_result(file_path: str, result_content: str) -> None:
    """Display edit operation result with changed lines highlighted."""
    q = '"'
    print(f"  ➜ {bold('edit')}({cyan(q + file_path + q)})")

    # Check if this is a unified diff format
    has_diff_format = "@@" in result_content and any(
        line.startswith("+") or line.startswith("-")
        for line in result_content.split("\n")[1:]
    )

    if has_diff_format:
        # Parse the success message first
        if "Successfully made" in result_content:
            parts = result_content.split()
            for i, part in enumerate(parts):
                if part == "made":
                    count = parts[i + 1]
                    print(f"    ⎿ {green(count)} replacement(s) made")
                    break

        # Show the unified diff with color coding
        lines = result_content.split("\n")
        for line in lines:
            if not line or "Successfully" in line:
                continue
            if line.startswith("@@"):
                print(f"  {bold(cyan(line))}")
            elif line.startswith("+") and not line.startswith("+++"):
                print(f"    {green(line[1:])}")
            elif line.startswith("-") and not line.startswith("---"):
                print(f"    {red(line[1:])}")
            else:
                print(f"    {dim(line)}")

        return

    # Fallback for non-diff format results - show full content without truncation
    if "Successfully made" in result_content:
        parts = result_content.split()
        for i, part in enumerate(parts):
            if part == "made":
                count = parts[i + 1]
                print(f"    ⎿ {green(count)} replacement(s) made")
                break

    # Show full content without truncation
    lines = result_content.split("\n")
    for line in lines:
        if line.strip():
            print(f"    {dim(line)}")


def display_edit_result_with_lines(file_path: str, result_content: str) -> None:
    """Display edit operation result with line numbers like Claude Code."""
    q = '"'
    print(f"  ➜ {bold('edit')}({cyan(q + file_path + q)})")

    lines = result_content.split("\n")
    additions = 0
    deletions = 0
    current_line_num: int | None = None

    # Count additions and deletions
    for line in lines:
        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1

    # Show summary
    if additions > 0 or deletions > 0:
        parts = []
        if additions > 0:
            parts.append(green(f"Added {additions} line(s)"))
        if deletions > 0:
            parts.append(red(f"Removed {deletions} line(s)"))
        print(f"    ⎿ {', '.join(parts)}")

    print()

    # Display diff with line numbers
    for line in lines:
        if not line or "Successfully" in line:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("@@"):
            print(f"{bold(cyan(line))}")
            # Extract starting line number from hunk header
            parts = line.split()
            for part in parts:
                if part.startswith("-") and "," in part:
                    current_line_num = int(part[1:].split(",")[0])
                    break

        elif line.startswith("+") and not line.startswith("+++"):
            num_str = str(current_line_num) if current_line_num else ""
            if current_line_num is not None:
                current_line_num += 1
            print(f"    {green(num_str.ljust(4))} {diff_addition(line[1:])}")

        elif line.startswith("-") and not line.startswith("---"):
            num_str = str(current_line_num) if current_line_num else ""
            if current_line_num is not None:
                current_line_num += 1
            print(f"    {red(num_str.ljust(4))} {diff_deletion(line[1:])}")

        else:
            num_str = str(current_line_num) if current_line_num else ""
            if current_line_num is not None:
                current_line_num += 1
            print(f"    {dim(num_str.ljust(4))} {dim(line)}")

    if additions > 0 or deletions > 0:
        print()


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

        # Integrate MCP client if provided - discover and register MCP tools
        self._mcp_client = mcp_client
        if mcp_client is not None:
            asyncio.run(self._integrate_mcp_tools())

    @property
    def tool_context(self) -> ToolContext:
        """What tools get for this session (the model never sees it)."""
        return ToolContext(working_dir=self.working_dir)

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

            # Display thinking indicator
            print("\x1b[2K\x1b[G\x1b[2m⠋ Thinking...\x1b[0m", end="", flush=True)

            turn_kwargs = self._provider_kwargs_for_turn(
                provider_kwargs, screenshot_followup_added
            )

            response = await self.provider.chat(
                messages=self.session.messages,
                tools=tool_defs,
                system_prompt=self.session.system_prompt,
                **turn_kwargs,
            )

            # Clear thinking indicator
            print("\x1b[2K\x1b[G", end="", flush=True)

            # Show compact context usage indicator
            try:
                current_tokens = self.session.token_count
                max_tokens = settings.max_context_tokens
                threshold = settings.compact_threshold
                percentage = (
                    (current_tokens / max_tokens) * 100 if max_tokens > 0 else 0
                )

                if percentage >= 90:
                    bar_color = red
                elif percentage >= 80:
                    bar_color = yellow
                elif percentage >= threshold * 100:
                    bar_color = cyan
                else:
                    bar_color = green

                bar_width = 40
                filled_chars = int(bar_width * percentage / 100)
                bar = "█" * min(filled_chars, bar_width) + "░" * max(
                    bar_width - filled_chars, 0
                )

                usage_text = f"{current_tokens:,}"
                if percentage > 70:
                    percent_text = cyan(f"{percentage:.0f}% ")
                    print(
                        f"  {dim('Context: ')}{bar_color(usage_text)} / {bar_color(str(max_tokens)[:5] + 'k')}  {percent_text}"
                    )
                else:
                    print(
                        f"  {dim('Context: ')}{bar_color(usage_text)} / {bar_color(str(max_tokens)[:5] + 'k')}"
                    )
            except Exception:
                pass

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

            # Log and execute tool calls in Claude Code style
            for tc in response.tool_calls:
                display_tool_call_claude_style(tc.name, tc.arguments)

            tool_tasks = [
                self.tool_registry.execute(tc, context=self.tool_context)
                for tc in response.tool_calls
            ]
            results = await asyncio.gather(*tool_tasks)

            # Log results in Claude Code style with diff highlighting for code changes
            for result, tc in zip(results, response.tool_calls):
                if result.is_error:
                    display_tool_result_claude_style(result.is_error, result.content)
                else:
                    content = result.content

                    # Check if this is a write/edit operation that should show diff formatting
                    is_write_operation = tc.name in ("write", "edit")

                    if is_write_operation:
                        # Get file path from tool arguments and make it relative
                        full_path = str(
                            tc.arguments.get(
                                "file", tc.arguments.get("path", "unknown")
                            )
                        )

                        try:
                            import os

                            cwd = os.getcwd()
                            if full_path.startswith(cwd):
                                rel_path = os.path.relpath(full_path, cwd)
                            else:
                                rel_path = full_path
                        except Exception:
                            rel_path = full_path

                        # For write operations, show success message with file info
                        if tc.name == "write":
                            display_write_result(rel_path, content)
                        # For edit operations, extract line counts and show diff with line numbers
                        elif tc.name == "edit":
                            display_edit_result_with_lines(rel_path, content)

                    else:
                        display_tool_result_claude_style(result.is_error, content)

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
