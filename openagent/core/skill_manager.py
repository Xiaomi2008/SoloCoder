"""Skill and slash command manager for agent extensibility."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class Skill:
    """Represents a loaded skill."""

    name: str
    instructions: str = ""
    resources: list[str] = None
    scripts: dict[str, str] = None
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.resources is None:
            self.resources = []
        if self.scripts is None:
            self.scripts = {}
        if self.metadata is None:
            self.metadata = {}


class SkillManager:
    """Manages agent skills (organized folders of instructions/scripts/resources).

    Skills are loaded from a directory structure and can be executed by the agent.
    """

    def __init__(self, skill_dir: str | None = None):
        """Initialize the skill manager.

        Args:
            skill_dir: Directory containing skills. Defaults to 'skills/' in project root.
        """
        if skill_dir is None:
            # Default to skills/ directory relative to this file's parent
            base = Path(__file__).parent.parent
            skill_dir = str(base / "skills")

        self.skill_dir = Path(skill_dir)
        self.skills: dict[str, Skill] = {}
        self._loaded = False

    def load_all(self) -> list[str]:
        """Load all skills from the skill directory.

        Returns:
            List of loaded skill names
        """
        if not self.skill_dir.exists():
            return []

        for item in self.skill_dir.iterdir():
            if item.is_dir() and not item.name.startswith("_"):
                try:
                    skill = self._load_skill(item.name, item)
                    self.skills[skill.name] = skill
                except Exception as e:
                    print(f"Error loading skill '{item.name}': {e}")

        self._loaded = True
        return list(self.skills.keys())

    def _load_skill(self, name: str, path: Path) -> Skill:
        """Load a single skill from a directory.

        Args:
            name: Skill name
            path: Path to skill directory

        Returns:
            Loaded Skill object
        """
        skill = Skill(name=name)

        # Load instructions.md if exists
        instructions_file = path / "instructions.md"
        if instructions_file.exists():
            skill.instructions = instructions_file.read_text(encoding="utf-8")

        # Load metadata.json if exists
        metadata_file = path / "metadata.json"
        if metadata_file.exists():
            try:
                skill.metadata = json.loads(metadata_file.read_text())
            except json.JSONDecodeError:
                pass

        # Find resource files (*.md, *.txt)
        for ext in [".md", ".txt"]:
            for file_path in path.glob(f"*{ext}"):
                if file_path.name not in ["instructions.md"]:
                    skill.resources.append(str(file_path))

        # Find script files (.py, .sh)
        for ext in [".py", ".sh"]:
            for file_path in path.glob(f"*{ext}"):
                try:
                    content = file_path.read_text(encoding="utf-8")
                    skill.scripts[file_path.name] = content
                except Exception:
                    pass

        return skill

    def get_skill(self, name: str) -> Skill | None:
        """Get a loaded skill by name.

        Args:
            name: Skill name

        Returns:
            Skill if found and loaded, None otherwise
        """
        # Auto-load skills on first access
        if not self._loaded:
            self.load_all()

        return self.skills.get(name)

    def execute_skill(
        self,
        skill_name: str,
        args: str | None = None,
    ) -> str:
        """Execute a skill with optional arguments.

        Args:
            skill_name: Name of the skill to execute
            args: Optional arguments to pass to the skill

        Returns:
            Result from executing the skill
        """
        # Auto-load skills on first access
        if not self._loaded:
            self.load_all()

        skill = self.skills.get(skill_name)
        if not skill:
            return f"Error: Skill '{skill_name}' not found. Available skills: {', '.join(self.skills.keys())}"

        # Build result from skill components
        output = []
        output.append(f"Executing skill: {skill_name}")

        if skill.instructions:
            output.append("\n--- Instructions ---")
            output.append(skill.instructions)

        if skill.resources:
            output.append(f"\n--- Resources ({len(skill.resources)}) ---")
            for resource in skill.resources[:10]:  # Limit to first 10
                output.append(f"  • {resource}")
            if len(skill.resources) > 10:
                output.append(f"  ... and {len(skill.resources) - 10} more")

        if skill.scripts:
            output.append(f"\n--- Scripts ({len(skill.scripts)}) ---")
            for script_name in list(skill.scripts.keys())[:5]:
                output.append(f"  • {script_name}")
            if len(skill.scripts) > 5:
                output.append(f"  ... and {len(skill.scripts) - 5} more")

        return "\n".join(output)


class SlashCommandRegistry:
    """Registry for custom slash commands.

    Slash commands are shortcuts for common operations like /commit, /review-pr, etc.
    """

    def __init__(self):
        self.commands: dict[str, Callable] = {}

    def register(
        self,
        name: str,
        handler: Callable[[list[str]], str],
        description: str = "",
    ):
        """Register a slash command.

        Args:
            name: Command name (without leading /)
            handler: Function that takes args list and returns result string
            description: Description of what the command does
        """
        self.commands[name] = {
            "handler": handler,
            "description": description,
        }

    def execute(self, command: str, args: list[str] | None = None) -> str:
        """Execute a slash command.

        Args:
            command: Command name (with or without leading /)
            args: Optional arguments for the command

        Returns:
            Result from executing the command
        """
        # Normalize command name
        if command.startswith("/"):
            command = command[1:]

        cmd_info = self.commands.get(command)
        if not cmd_info:
            available = ", ".join(self.commands.keys())
            return f"Error: Unknown slash command '{command}'. Available commands: {available}"

        handler = cmd_info["handler"]
        try:
            return handler(args or [])
        except Exception as e:
            return f"Error executing command: {e}"


# Global instances
_global_skill_manager: SkillManager | None = None
_global_command_registry: SlashCommandRegistry | None = None


def get_skill_manager() -> SkillManager:
    """Get or create the global skill manager."""
    global _global_skill_manager
    if _global_skill_manager is None:
        _global_skill_manager = SkillManager()
    return _global_skill_manager


def get_command_registry() -> SlashCommandRegistry:
    """Get or create the global command registry."""
    global _global_command_registry
    if _global_command_registry is None:
        _global_command_registry = SlashCommandRegistry()
    
    # Register built-in slash commands if not already registered
    registry = _global_command_registry
    
    # Register /compact command if not present
    if "compact" not in registry.commands:
        async def compact_handler(args: list[str]) -> str:
            """Handler for the /compact command."""
            from openagent.core.session import Session
            from openagent.provider.base import BaseProvider
            
            # This will be called with access to coder instance
            return "Context compaction triggered manually."
        
        registry.register(
            name="compact",
            handler=lambda args: "Use /compact in the CLI to trigger context compaction.",
            description="Manually compact conversation history"
        )
    
    return _global_command_registry


async def reset_managers():
    """Reset all managers (useful for testing)."""
    global _global_skill_manager, _global_command_registry
    _global_skill_manager = None
    _global_command_registry = None


async def reset_managers():
    """Reset all managers (useful for testing)."""
    global _global_skill_manager, _global_command_registry
    _global_skill_manager = None
    _global_command_registry = None
