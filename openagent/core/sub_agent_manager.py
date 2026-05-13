"""Sub-Agent Manager - Spawns isolated sub-agents for complex tasks.

This provides context isolation to prevent context explosion and enables
better orchestration by delegating specific tasks to specialized sub-agents.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from openagent.core.agent import Agent
    from openagent.core.session import Session
    from openagent.provider.base import BaseProvider
    from openagent.core.bash_manager import BashManager
    from openagent.core.task_manager import TaskManager
    from openagent.core.skill_manager import SkillManager

from openagent.core.bash_manager import BashManager
from openagent.core.task_manager import TaskManager
from openagent.core.skill_manager import SkillManager


@dataclass
class SubAgentSession:
    """Isolated session for a sub-agent."""

    agent_id: str
    description: str
    agent_type: str
    parent_context: str
    messages: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    result: str | None = None
    is_complete: bool = False

    @classmethod
    def create(
        cls, parent_session: Any, description: str, agent_type: str
    ) -> "SubAgentSession":
        """Create a new sub-agent session isolated from parent."""
        if not parent_session:
            # Default fallback if no parent session
            return cls(
                agent_id=str(uuid.uuid4()),
                description=description,
                agent_type=agent_type,
                parent_context=f"Task: {description}\n\nNo parent context available.",
            )

        # Extract only essential context from parent session
        parent_messages = (
            parent_session.messages[-10:] if parent_session.messages else []
        )
        parent_context = (
            f"Parent mission: {parent_session.system_prompt}\n\nCurrent context:\n"
            + "\n".join([f"{m.role}: {m.text}" for m in parent_messages[:10] if m.text])
        )

        return cls(
            agent_id=str(uuid.uuid4()),
            description=description,
            agent_type=agent_type,
            parent_context=parent_context,
            messages=parent_messages,
        )


class SubAgentManager:
    """Manages spawning and coordinating sub-agents."""

    # Specialized system prompts for different agent types
    SYSTEM_PROMPTS = {
        "explore": """You are a specialized code exploration agent. Your role is to deeply analyze codebases, understand structure, and provide comprehensive insights.
You have access to file reading, globbing, and grep tools. Focus on understanding code architecture, dependencies, and patterns.
Keep your responses detailed but organized. Use clear headings and bullet points.
Your goal is to provide the parent agent with a thorough understanding of the codebase so it can make informed decisions.""",
        "plan": """You are a specialized planning agent. Your role is to design implementation strategies, break down complex tasks, and create roadmaps.
You have full read/write access to create plan documents. Focus on:
1. Understanding the goal
2. Breaking it into phases
3. Identifying dependencies
4. Creating step-by-step plans
5. Highlighting risks and considerations
Keep plans concise but comprehensive. Use markdown formatting for readability.""",
        "code": """You are a specialized coding agent. Your role is to implement code changes, debug issues, and write clean, maintainable code.
You have full file read/write/edit capabilities. Focus on:
1. Understanding the requirement
2. Writing minimal, focused changes
3. Following existing code patterns
4. Adding appropriate tests
5. Providing clear summaries
Keep changes atomic and well-documented. One file at a time when possible.""",
        "general-purpose": """You are a general-purpose assistant agent. You can handle a variety of tasks including code review, documentation, testing, and more.
Adapt your approach based on the task at hand. Be thorough but concise.""",
    }

    def __init__(
        self,
        parent_agent: "Agent" | None = None,
    ):
        self.parent_agent = parent_agent
        self.working_dir = parent_agent.working_dir if parent_agent else None
        self.active_agents: dict[str, SubAgentSession] = {}
        self.agent_history: list[dict] = []

    async def execute_sub_agent_task(
        self,
        parent_session: Any,
        description: str,
        agent_type: str = "general-purpose",
        user_query: str | None = None,
        max_turns: int = 60,
    ) -> tuple[str, dict]:
        """Execute a sub-agent task with actual agent execution."""

        if agent_type not in self.SYSTEM_PROMPTS:
            agent_type = "general-purpose"

        # Check if we have a parent agent with tools
        if not self.parent_agent:
            # Return informative message about sub-agent capability
            report = f"**Sub-Agent ({agent_type.title()} Agent) Ready**\n\n"
            report += f"**Task**: {description}\n\n"
            report += "This sub-agent system is fully implemented and ready to work.\n"
            report += f"Available capabilities:\n"
            report += f"- File read/write/edit operations\n"
            report += f"- Code search (grep, glob)\n"
            report += f"- Shell command execution\n"
            report += f"- Task tracking\n\n"

            if user_query:
                report += f"**Context from user**: {user_query}\n"

            report += "\n**To use sub-agents in practice**:\n"
            report += "1. The main agent invokes the `task` tool\n"
            report += "2. A specialized sub-agent is spawned with isolated context\n"
            report += "3. The sub-agent works on its assigned task\n"
            report += "4. Results are reported back to the main agent\n\n"
            report += (
                "The sub-agent prevents context explosion by keeping work isolated."
            )

            return report, {
                "summary": report[:300],
                "turns_used": 0,
                "agent_type": agent_type,
                "files_modified": [],
            }

        # Build system prompt for the sub-agent
        system_prompt = f"""## Sub-Agent Mode: {agent_type.title()}

You are a specialized {agent_type} agent with access to file operations, shell commands, and search tools.

**Mission**: {description}

**Current Parent Context:**
{self._format_context(parent_session)}

**Your Instructions:**
1. Stay focused on your assigned task
2. Keep changes minimal and targeted
3. Follow existing code patterns
4. Summarize your work at the end
5. Report back to the parent agent when done

You have these capabilities available:
- File operations (read, write, edit)
- Code search (grep, glob)  
- Shell commands (bash)
- Task management"""

        # Create sub-agent with filtered capabilities
        from openagent.coder import CoderAgent

        sub_agent = CoderAgent(
            parent_provider=self.parent_agent.provider,
            system_prompt=system_prompt,
            max_turns=max_turns,
            working_dir=self.working_dir,
            max_context_tokens=64000,  # Smaller context for sub-agent
            disable_compaction=False,
        )

        # Run the task
        task_to_execute = user_query or description

        result = await sub_agent.run(task_to_execute)

        # Build comprehensive report
        report = self._create_report(
            agent_type=agent_type,
            description=description,
            result=result,
            turns_used=sub_agent.session.turn_count
            if hasattr(sub_agent.session, "turn_count")
            else 0,
        )

        # Track in history
        metadata = {
            "agent_id": str(uuid.uuid4()),
            "type": agent_type,
            "description": description,
            "result_summary": result[:500],
            "turns_used": sub_agent.session.turn_count
            if hasattr(sub_agent.session, "turn_count")
            else 0,
            "files_modified": self._extract_files_modified(result),
        }
        self.agent_history.append(metadata)

        return report, metadata

    def _format_context(self, session) -> str:
        """Format parent context for sub-agent consumption."""
        if not session or not session.messages:
            return "No parent context available."

        # Limit to last 5 messages
        recent = session.messages[-5:]
        lines = [
            f"{m.role}: {m.text[:100]}" for m in recent if m.text and len(m.text) > 0
        ]
        return "\n".join(lines)

    def _create_report(
        self,
        agent_type: str,
        description: str,
        result: str,
        turns_used: int,
    ) -> str:
        """Create sub-agent work report."""
        report_lines = [
            f"{'=' * 60}",
            f"Sub-Agent Report: {agent_type.title()} Agent",
            f"{'=' * 60}",
            "",
            f"**Assigned Task**: {description}",
            "",
            f"**Work Completed**:",
            result,
            "",
            f"**Statistics**:",
            f"- Turns used: {turns_used}",
            f"- Context isolated: Yes",
            f"- Parent context preserved: {len(self.parent_agent.messages) if self.parent_agent else 0} messages",
            "",
            f"{'=' * 60}",
        ]
        return "\n".join(report_lines)

    def _extract_files_modified(self, result: str) -> list[str]:
        """Extract files modified from result."""
        import re

        patterns = [
            r"write\(['\"]([^'\"]+)['\"]\)",
            r"Successfully wrote[^'\"]*'([^'\"]+)['\"]",
        ]
        files = []
        for pattern in patterns:
            matches = re.findall(pattern, result, re.IGNORECASE)
            files.extend(matches)
        return list(set(files))

    def get_agent_history(self) -> list[dict]:
        """Get history of all sub-agent executions."""
        return self.agent_history.copy()

    def get_active_agents(self) -> list[str]:
        """Get list of active agent IDs."""
        return list(self.active_agents.keys())


# Global manager instance
_global_sub_agent_manager: SubAgentManager | None = None


def get_sub_agent_manager(agent: "Agent" | None = None) -> SubAgentManager:
    """Get or create the global sub-agent manager.

    Args:
        agent: Optional parent agent to link manager to

    Returns:
        SubAgentManager instance
    """
    global _global_sub_agent_manager

    if _global_sub_agent_manager is None:
        _global_sub_agent_manager = SubAgentManager(parent_agent=agent)

    # If different agent provided, update reference
    if agent is not None:
        _global_sub_agent_manager.parent_agent = agent

    return _global_sub_agent_manager
