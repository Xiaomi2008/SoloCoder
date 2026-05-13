"""Online learning components for agent improvement.

This module provides tools for tracking tool usage patterns, collecting feedback,
analyzing session outcomes, and adapting system prompts based on learned behavior.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ToolUsageRecord:
    """Record of a single tool usage event."""
    tool_name: str
    success: bool
    error_message: str | None = None
    task_context: str = ""  # Brief description of what the agent was trying to do
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class FeedbackRecord:
    """User feedback on agent behavior."""
    session_id: str
    rating: int  # 1-5 scale
    comment: str = ""
    task_description: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class SessionOutcome:
    """Structured outcome from a completed session."""
    session_id: str
    task_description: str
    tools_used: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    commands_executed: list[str] = field(default_factory=list)
    success: bool = True  # Whether the task was completed successfully
    turn_count: int = 0
    error_count: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class ToolUsageTracker:
    """Tracks tool usage patterns to identify successful strategies.

    This tracker records which tools succeed or fail in different contexts,
    enabling the agent to learn from past experiences.

    Example:
        >>> tracker = ToolUsageTracker()
        >>> tracker.record("read", success=True, context="Understanding codebase structure")
        >>> stats = tracker.get_stats("read")
        >>> print(f"Success rate: {stats['success_rate']:.2%}")
    """

    def __init__(self, storage_path: str | None = None):
        self._records: list[ToolUsageRecord] = []
        self._storage_path = Path(storage_path) if storage_path else None
        self._load()

    def record(
        self,
        tool_name: str,
        success: bool,
        error_message: str | None = None,
        task_context: str = "",
    ) -> None:
        """Record a tool usage event.

        Args:
            tool_name: Name of the tool that was used
            success: Whether the tool execution succeeded
            error_message: Error message if the tool failed
            task_context: Brief description of what the agent was trying to do
        """
        record = ToolUsageRecord(
            tool_name=tool_name,
            success=success,
            error_message=error_message,
            task_context=task_context,
        )
        self._records.append(record)
        self._save()

    def get_stats(self, tool_name: str | None = None) -> dict[str, Any]:
        """Get usage statistics for tools.

        Args:
            tool_name: Optional specific tool to get stats for

        Returns:
            Dictionary with success_rate, total_uses, successes, failures
        """
        records = self._records if tool_name is None else [
            r for r in self._records if r.tool_name == tool_name
        ]

        if not records:
            return {"success_rate": 0.0, "total_uses": 0, "successes": 0, "failures": 0}

        successes = sum(1 for r in records if r.success)
        failures = len(records) - successes

        return {
            "success_rate": successes / len(records),
            "total_uses": len(records),
            "successes": successes,
            "failures": failures,
        }

    def get_reliable_tools(self, min_uses: int = 3, min_success_rate: float = 0.7) -> list[str]:
        """Get tools that have proven reliable based on usage history.

        Args:
            min_uses: Minimum number of times a tool must be used
            min_success_rate: Minimum success rate threshold

        Returns:
            List of tool names that meet the reliability criteria
        """
        reliable = []
        for record in self._records:
            stats = self.get_stats(record.tool_name)
            if (stats["total_uses"] >= min_uses and
                stats["success_rate"] >= min_success_rate):
                if record.tool_name not in reliable:
                    reliable.append(record.tool_name)
        return reliable

    def get_common_errors(self, tool_name: str, top_n: int = 3) -> list[tuple[str, int]]:
        """Get most common error messages for a tool.

        Args:
            tool_name: Name of the tool
            top_n: Number of top errors to return

        Returns:
            List of (error_message, count) tuples
        """
        errors = defaultdict(int)
        for record in self._records:
            if record.tool_name == tool_name and not record.success and record.error_message:
                # Normalize error messages by taking first line
                error_key = record.error_message.split('\n')[0][:100]
                errors[error_key] += 1

        return sorted(errors.items(), key=lambda x: -x[1])[:top_n]

    def _load(self) -> None:
        """Load records from storage."""
        if self._storage_path and self._storage_path.exists():
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
            self._records = [
                ToolUsageRecord(**r) for r in data.get("records", [])
            ]

    def _save(self) -> None:
        """Save records to storage."""
        if not self._storage_path:
            return
        data = {"records": [self._record_to_dict(r) for r in self._records]}
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _record_to_dict(self, record: ToolUsageRecord) -> dict[str, Any]:
        return {
            "tool_name": record.tool_name,
            "success": record.success,
            "error_message": record.error_message,
            "task_context": record.task_context,
            "timestamp": record.timestamp,
        }


class FeedbackManager:
    """Manages user feedback for agent improvement.

    Collects and stores user ratings and comments on agent performance,
    enabling the system to learn from explicit user feedback.

    Example:
        >>> manager = FeedbackManager()
        >>> manager.add_feedback(session_id="abc123", rating=4, comment="Good but slow")
        >>> avg_rating = manager.get_average_rating()
    """

    def __init__(self, storage_path: str | None = None):
        self._records: list[FeedbackRecord] = []
        self._storage_path = Path(storage_path) if storage_path else None
        self._load()

    def add_feedback(
        self,
        session_id: str,
        rating: int,
        comment: str = "",
        task_description: str = "",
    ) -> None:
        """Add user feedback.

        Args:
            session_id: Unique identifier for the session
            rating: Rating from 1-5 (1=very poor, 5=excellent)
            comment: Optional user comment
            task_description: Description of what was attempted
        """
        if not 1 <= rating <= 5:
            raise ValueError("Rating must be between 1 and 5")

        record = FeedbackRecord(
            session_id=session_id,
            rating=rating,
            comment=comment,
            task_description=task_description,
        )
        self._records.append(record)
        self._save()

    def get_average_rating(self) -> float:
        """Get average rating across all feedback."""
        if not self._records:
            return 0.0
        return sum(r.rating for r in self._records) / len(self._records)

    def get_recent_feedback(self, limit: int = 5) -> list[FeedbackRecord]:
        """Get most recent feedback records."""
        sorted_records = sorted(
            self._records,
            key=lambda r: r.timestamp,
            reverse=True,
        )
        return sorted_records[:limit]

    def get_feedback_summary(self) -> dict[str, Any]:
        """Get summary statistics of all feedback.

        Returns:
            Dictionary with rating distribution and common themes
        """
        if not self._records:
            return {"total": 0, "average": 0.0, "distribution": {}}

        distribution = defaultdict(int)
        for record in self._records:
            distribution[record.rating] += 1

        return {
            "total": len(self._records),
            "average": self.get_average_rating(),
            "distribution": dict(distribution),
        }

    def _load(self) -> None:
        """Load feedback from storage."""
        if self._storage_path and self._storage_path.exists():
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
            self._records = [FeedbackRecord(**r) for r in data.get("records", [])]

    def _save(self) -> None:
        """Save feedback to storage."""
        if not self._storage_path:
            return
        data = {"records": [self._record_to_dict(r) for r in self._records]}
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _record_to_dict(self, record: FeedbackRecord) -> dict[str, Any]:
        return {
            "session_id": record.session_id,
            "rating": record.rating,
            "comment": record.comment,
            "task_description": record.task_description,
            "timestamp": record.timestamp,
        }


class SessionAnalyzer:
    """Analyzes completed sessions to extract structured outcomes.

    Parses session data to identify patterns in successful vs unsuccessful
    task completions, tracking which tool sequences work best.

    Example:
        >>> analyzer = SessionAnalyzer()
        >>> outcome = analyzer.analyze_session(session, task_description="Refactor code")
        >>> print(f"Tools used: {outcome.tools_used}")
    """

    def __init__(self, storage_path: str | None = None):
        from .session import Session
        self.Session = Session
        self._outcomes: list[SessionOutcome] = []
        self._storage_path = Path(storage_path) if storage_path else None
        self._load()

    def analyze_session(
        self,
        session: "Session",
        task_description: str,
        session_id: str | None = None,
    ) -> SessionOutcome:
        """Analyze a completed session and extract structured outcome.

        Args:
            session: The completed session to analyze
            task_description: Description of what the user asked for
            session_id: Optional unique identifier for this session

        Returns:
            SessionOutcome with extracted information
        """
        from .types import ToolResultBlock, ToolUseBlock

        if session_id is None:
            session_id = datetime.now().isoformat()

        tools_used = []
        files_modified = []
        commands_executed = []
        error_count = 0

        for msg in session.messages:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        tools_used.append(block.name)
                        # Extract file paths from write/edit operations
                        if block.name in ("write", "edit"):
                            file_path = (
                                block.arguments.get("file") or
                                block.arguments.get("path")
                            )
                            if file_path:
                                files_modified.append(file_path)
                        # Extract bash commands
                        elif block.name == "bash":
                            cmd = block.arguments.get("command", "")
                            if cmd:
                                commands_executed.append(cmd)

                    elif isinstance(block, ToolResultBlock):
                        if block.is_error:
                            error_count += 1

        # Determine success based on error ratio and turn count
        total_tool_calls = len(tools_used)
        success_rate = (total_tool_calls - error_count) / max(total_tool_calls, 1)
        is_success = success_rate >= 0.8 and error_count <= 2

        outcome = SessionOutcome(
            session_id=session_id,
            task_description=task_description,
            tools_used=tools_used,
            files_modified=list(set(files_modified)),
            commands_executed=list(set(commands_executed))[:10],  # Limit stored commands
            success=is_success,
            turn_count=len(session.messages),
            error_count=error_count,
        )

        self._outcomes.append(outcome)
        self._save()
        return outcome

    def get_successful_patterns(self, min_occurrences: int = 2) -> dict[str, list[str]]:
        """Identify tool sequences that lead to successful outcomes.

        Args:
            min_occurrences: Minimum times a pattern must appear

        Returns:
            Dictionary mapping task types to successful tool sequences
        """
        patterns = defaultdict(list)

        for outcome in self._outcomes:
            if outcome.success and outcome.tools_used:
                # Use first few tools as the "pattern"
                pattern = tuple(outcome.tools_used[:3])
                key = f"{outcome.task_description[:50]}..." if len(outcome.task_description) > 50 else outcome.task_description
                patterns[key].append(list(pattern))

        # Filter to only common patterns
        return {k: v for k, v in patterns.items() if len(v) >= min_occurrences}

    def get_average_turns_for_success(self) -> float:
        """Get average number of turns needed for successful completions."""
        successful = [o for o in self._outcomes if o.success]
        if not successful:
            return 0.0
        return sum(o.turn_count for o in successful) / len(successful)

    def _load(self) -> None:
        """Load outcomes from storage."""
        if self._storage_path and self._storage_path.exists():
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
            self._outcomes = [SessionOutcome(**o) for o in data.get("outcomes", [])]

    def _save(self) -> None:
        """Save outcomes to storage."""
        if not self._storage_path:
            return
        data = {"outcomes": [self._outcome_to_dict(o) for o in self._outcomes]}
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _outcome_to_dict(self, outcome: SessionOutcome) -> dict[str, Any]:
        return {
            "session_id": outcome.session_id,
            "task_description": outcome.task_description,
            "tools_used": outcome.tools_used,
            "files_modified": outcome.files_modified,
            "commands_executed": outcome.commands_executed,
            "success": outcome.success,
            "turn_count": outcome.turn_count,
            "error_count": outcome.error_count,
            "timestamp": outcome.timestamp,
        }


class AdaptivePrompt:
    """Dynamically adjusts system prompts based on learned patterns.

    Uses tool usage statistics and feedback to modify the agent's behavior
    by injecting learned preferences into the system prompt.

    Example:
        >>> tracker = ToolUsageTracker()
        >>> adapter = AdaptivePrompt(tracker)
        >>> adapted_prompt = adapter.adapt("You are a helpful assistant.")
    """

    def __init__(
        self,
        base_prompt: str,
        tool_tracker: ToolUsageTracker | None = None,
        feedback_manager: FeedbackManager | None = None,
        session_analyzer: SessionAnalyzer | None = None,
    ):
        self.base_prompt = base_prompt
        self.tool_tracker = tool_tracker
        self.feedback_manager = feedback_manager
        self.session_analyzer = session_analyzer

    def adapt(self) -> str:
        """Generate an adapted system prompt based on learned patterns.

        Returns:
            Modified system prompt with learned preferences injected
        """
        adaptations = []

        # Add tool reliability hints
        if self.tool_tracker:
            reliable_tools = self.tool_tracker.get_reliable_tools(
                min_uses=3, min_success_rate=0.75
            )
            if reliable_tools:
                adaptations.append(
                    f"Preferred tools (proven reliable): {', '.join(reliable_tools[:5])}"
                )

        # Add feedback-based adjustments
        if self.feedback_manager:
            avg_rating = self.feedback_manager.get_average_rating()
            if avg_rating < 3.0:
                adaptations.append(
                    "Note: Recent user feedback indicates room for improvement. "
                    "Be more thorough and ask clarifying questions when uncertain."
                )

        # Add session pattern insights
        if self.session_analyzer:
            avg_turns = self.session_analyzer.get_average_turns_for_success()
            if avg_turns > 0:
                adaptations.append(
                    f"Typical successful tasks complete in ~{avg_turns:.1f} turns."
                )

        # Combine base prompt with adaptations
        if adaptations:
            return self.base_prompt + "\n\n### Learning-Based Adaptations\n" + "\n".join(f"- {a}" for a in adaptations)
        return self.base_prompt
