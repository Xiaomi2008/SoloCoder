from __future__ import annotations

import asyncio
from typing import Any, Callable

# Changed imports for core modules
from .bash_manager import BashManager, get_bash_manager
from .display import (
    bold, dim, cyan, green, red, white,
    display_tool_call_claude_style, display_tool_result_claude_style,
    format_diff_output, display_code_block, display_diff_claude_style,
    truncate_text
)


def display_write_result(file_path: str, result_content: str) -> None:
    """Display write operation result with file info."""
    print(f"  ● {bold('write')}({cyan(f'\"{file_path}\"')})")

    # Parse the success message to show bytes written
    if 'Successfully wrote' in result_content:
        parts = result_content.split()
        for i, part in enumerate(parts):
            if part == 'wrote':
                bytes_written = parts[i + 1]
                print(f"    ⎿ {green(bytes_written)} to {cyan(file_path)}")
                break

    # Extract and show the actual content that was written (skip the success message)
    lines = result_content.split('\n')
    content_start = False
    for line in lines:
        if 'Successfully wrote' in line:
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
    print(f"  ● {bold('edit')}({cyan(f'\"{file_path}\"')})")

    # Check if this is a unified diff format
    has_diff_format = '@@' in result_content and any(
        line.startswith('+') or line.startswith('-')
        for line in result_content.split('\n')[1:]
    )

    if has_diff_format:
        # Parse the success message first
        if 'Successfully made' in result_content:
            parts = result_content.split()
            for i, part in enumerate(parts):
                if part == 'made':
                    count = parts[i + 1]
                    print(f"    ⎿ {green(count)} replacement(s) made")
                    break

        # Show the unified diff with color coding
        lines = result_content.split('\n')
        for line in lines:
            if not line or 'Successfully' in line:
                continue
            if line.startswith('@@'):
                print(f"  {bold(cyan(line))}")
            elif line.startswith('+') and not line.startswith('+++'):
                print(f"    {green(line[1:])}")
            elif line.startswith('-') and not line.startswith('---'):
                print(f"    {red(line[1:])}")
            else:
                print(f"    {dim(line)}")

        return

    # Fallback for non-diff format results - show full content without truncation
    if 'Successfully made' in result_content:
        parts = result_content.split()
        for i, part in enumerate(parts):
            if part == 'made':
                count = parts[i + 1]
                print(f"    ⎿ {green(count)} replacement(s) made")
                break

    # Show full content without truncation
    lines = result_content.split('\n')
    for line in lines:
        if line.strip():
            print(f"    {dim(line)}")


from .logging import AgentLogger
from .session import Session
from .skill_manager import (
    SkillManager,
    SlashCommandRegistry,
    get_command_registry,
    get_skill_manager,
)
from .task_manager import TaskManager, get_task_manager
from .tool import ToolRegistry, tool
from .types import Message

# BaseProvider is likely in parent package or sibling 'provider' package
# Since we are in core/, provider/ is '../provider/'
# But 'openagent.provider' is absolute import, which is fine and clearer.
from openagent.provider.base import BaseProvider


class Agent:
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
        max_messages: int | None = None,  # Max messages before compression kicks in
        enable_learning: bool = False,  # Enable online learning features
        learning_storage_path: str | None = None,  # Path to store learning data
    ) -> None:
        self.provider = provider
        self.session = Session(system_prompt=system_prompt, max_messages=max_messages)
        self.max_turns = max_turns
        self.tool_registry = ToolRegistry()
        self._logger = AgentLogger(agent_id)

        # Initialize managers (use provided or create new instances)
        self.bash_manager = bash_manager or get_bash_manager()
        self.task_manager = task_manager or get_task_manager()
        self.skill_manager = skill_manager or get_skill_manager()
        self.command_registry = get_command_registry()

        # Initialize learning components if enabled
        self._enable_learning = enable_learning
        self._learning_storage_path = learning_storage_path
        if enable_learning:
            from .learning import (
                ToolUsageTracker, FeedbackManager, SessionAnalyzer, AdaptivePrompt
            )
            self._tool_tracker = ToolUsageTracker(
                storage_path=f"{learning_storage_path}/tool_usage.json" if learning_storage_path else None
            )
            self._feedback_manager = FeedbackManager(
                storage_path=f"{learning_storage_path}/feedback.json" if learning_storage_path else None
            )
            self._session_analyzer = SessionAnalyzer(
                storage_path=f"{learning_storage_path}/outcomes.json" if learning_storage_path else None
            )
            self._adaptive_prompt = AdaptivePrompt(
                base_prompt=system_prompt,
                tool_tracker=self._tool_tracker,
                feedback_manager=self._feedback_manager,
                session_analyzer=self._session_analyzer,
            )
        else:
            self._tool_tracker = None
            self._feedback_manager = None
            self._session_analyzer = None
            self._adaptive_prompt = None

        if tools:
            for fn in tools:
                if not hasattr(fn, "_tool_name"):
                    fn = tool(fn)
                self.tool_registry.register(fn)

        # Integrate MCP client if provided - discover and register MCP tools
        self._mcp_client = mcp_client
        if mcp_client is not None:
            asyncio.run(self._integrate_mcp_tools())

    async def _integrate_mcp_tools(self) -> None:
        """Discover and integrate MCP tools from the client."""
        try:
            # Ensure the client is connected
            if hasattr(self._mcp_client, '__aenter__'):
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
        self.session.add("user", user_input)
        result = await self._loop(**kwargs)
        return result

    async def _loop(self, **kwargs: Any) -> str:
        tool_defs = self.tool_registry.definitions if len(self.tool_registry) > 0 else None

        # Apply adaptive prompt if learning is enabled
        system_prompt = self.session.system_prompt
        if self._enable_learning and self._adaptive_prompt:
            system_prompt = self._adaptive_prompt.adapt()

        response: Message | None = None

        for turn in range(self.max_turns):
            self._logger.turn_start(turn + 1, self.max_turns)

            response = await self.provider.chat(
                messages=self.session.messages,
                tools=tool_defs,
                system_prompt=system_prompt,
                **kwargs,
            )
            self.session.add_message(response)

            has_tools = response.has_tool_calls
            self._logger.turn_end(turn + 1, has_tools)

            if not has_tools:
                # Analyze session outcome if learning is enabled
                if self._enable_learning and self._session_analyzer:
                    await self._analyze_session_outcome()
                self._logger.run_end(turn + 1)
                return response.text

            # Log and execute tool calls in Claude Code style
            for tc in response.tool_calls:
                display_tool_call_claude_style(tc.name, tc.arguments)

            tool_tasks = [
                self.tool_registry.execute(tc) for tc in response.tool_calls
            ]
            results = await asyncio.gather(*tool_tasks)

            # Track tool usage if learning is enabled
            if self._enable_learning and self._tool_tracker:
                for result, tc in zip(results, response.tool_calls):
                    self._tool_tracker.record(
                        tool_name=tc.name,
                        success=not result.is_error,
                        error_message=result.content if result.is_error else None,
                        task_context=self._extract_task_context(),
                    )

            # Log results in Claude Code style with diff highlighting for code changes
            for result, tc in zip(results, response.tool_calls):
                if result.is_error:
                    display_tool_result_claude_style(result.is_error, result.content)
                else:
                    content = result.content

                    # Check if this is a write/edit operation that should show diff formatting
                    is_write_operation = tc.name in ('write', 'edit')

                    if is_write_operation:
                        # Get file path from tool arguments and make it relative
                        full_path = str(tc.arguments.get('file', tc.arguments.get('path', 'unknown')))

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
                        if tc.name == 'write':
                            display_write_result(rel_path, content)
                        # For edit operations, try to extract and show the changed lines
                        elif tc.name == 'edit':
                            display_edit_result(rel_path, content)

                    else:
                        display_tool_result_claude_style(result.is_error, content)

            self.session.add_tool_results(list(results))

        self._logger.max_turns_reached()
        # Analyze session outcome if learning is enabled (even on max turns)
        if self._enable_learning and self._session_analyzer:
            await self._analyze_session_outcome()
        if response is None:
            raise RuntimeError("Agent loop completed without receiving any response")
        return response.text

    def _extract_task_context(self) -> str:
        """Extract brief task context from the most recent user message."""
        for msg in reversed(self.session.messages):
            if msg.role == "user" and isinstance(msg.content, str):
                # Return first 100 chars as context
                return msg.content[:100]
        return ""

    async def _analyze_session_outcome(self) -> None:
        """Analyze the current session outcome for learning."""
        if not self._session_analyzer:
            return

        # Extract task description from first user message
        task_description = ""
        for msg in self.session.messages:
            if msg.role == "user" and isinstance(msg.content, str):
                task_description = msg.content[:200]
                break

        if task_description:
            self._session_analyzer.analyze_session(
                session=self.session,
                task_description=task_description,
            )

    def add_feedback(self, rating: int, comment: str = "") -> None:
        """Add user feedback for the current session.

        Args:
            rating: Rating from 1-5 (1=very poor, 5=excellent)
            comment: Optional user comment
        """
        if not self._enable_learning or not self._feedback_manager:
            return

        # Extract task description
        task_description = ""
        for msg in self.session.messages:
            if msg.role == "user" and isinstance(msg.content, str):
                task_description = msg.content[:200]
                break

        import uuid
        session_id = f"{uuid.uuid4().hex[:8]}"
        self._feedback_manager.add_feedback(
            session_id=session_id,
            rating=rating,
            comment=comment,
            task_description=task_description,
        )

    def get_learning_stats(self) -> dict[str, Any]:
        """Get learning statistics for the agent.

        Returns:
            Dictionary with tool usage stats and feedback summary
        """
        if not self._enable_learning:
            return {"learning_enabled": False}

        stats = {
            "learning_enabled": True,
            "tool_usage": {},
            "feedback_summary": None,
        }

        if self._tool_tracker:
            # Get stats for each tool
            all_tools = set()
            for record in self._tool_tracker._records:
                all_tools.add(record.tool_name)

            for tool_name in all_tools:
                stats["tool_usage"][tool_name] = self._tool_tracker.get_stats(tool_name)

        if self._feedback_manager:
            stats["feedback_summary"] = self._feedback_manager.get_feedback_summary()

        return stats
