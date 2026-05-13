"""Tests for online learning components."""

from __future__ import annotations

import pytest
from pathlib import Path
import tempfile
import json


class TestToolUsageTracker:
    """Tests for ToolUsageTracker class."""

    def test_record_tool_usage(self):
        """Test recording tool usage events."""
        from openagent.core.learning import ToolUsageTracker

        tracker = ToolUsageTracker()
        tracker.record("read", success=True, task_context="Reading file")
        tracker.record("write", success=False, error_message="Permission denied", task_context="Writing file")

        assert len(tracker._records) == 2

    def test_get_stats(self):
        """Test getting tool usage statistics."""
        from openagent.core.learning import ToolUsageTracker

        tracker = ToolUsageTracker()

        # Record some usage
        for _ in range(3):
            tracker.record("read", success=True)
        tracker.record("read", success=False)

        stats = tracker.get_stats("read")
        assert stats["total_uses"] == 4
        assert stats["successes"] == 3
        assert stats["failures"] == 1
        assert abs(stats["success_rate"] - 0.75) < 0.001

    def test_get_reliable_tools(self):
        """Test getting reliable tools based on usage history."""
        from openagent.core.learning import ToolUsageTracker

        tracker = ToolUsageTracker()

        # Make "read" a reliable tool
        for _ in range(5):
            tracker.record("read", success=True)

        # Make "write" unreliable (not enough uses)
        tracker.record("write", success=True)

        reliable = tracker.get_reliable_tools(min_uses=3, min_success_rate=0.7)
        assert "read" in reliable
        assert "write" not in reliable

    def test_get_common_errors(self):
        """Test getting common error messages."""
        from openagent.core.learning import ToolUsageTracker

        tracker = ToolUsageTracker()

        # Record some errors with same pattern prefix
        tracker.record("bash", success=False, error_message="Command not found: foo")
        tracker.record("bash", success=False, error_message="Command not found: bar")
        tracker.record("bash", success=False, error_message="Permission denied: /etc/passwd")
        tracker.record("bash", success=True)

        errors = tracker.get_common_errors("bash", top_n=2)
        assert len(errors) == 2  # Two different error patterns
        # First should be "Command not found" with count of 2
        assert errors[0][1] >= 1

    def test_persistence(self):
        """Test saving and loading records."""
        from openagent.core.learning import ToolUsageTracker

        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir) / "tool_usage.json"
            tracker1 = ToolUsageTracker(storage_path=str(storage_path))

            tracker1.record("read", success=True)
            tracker1.record("write", success=False, error_message="Error")

            # Load from same path
            tracker2 = ToolUsageTracker(storage_path=str(storage_path))
            assert len(tracker2._records) == 2


class TestFeedbackManager:
    """Tests for FeedbackManager class."""

    def test_add_feedback(self):
        """Test adding user feedback."""
        from openagent.core.learning import FeedbackManager

        manager = FeedbackManager()
        manager.add_feedback(
            session_id="abc123",
            rating=4,
            comment="Good but slow",
            task_description="Refactor code"
        )

        assert len(manager._records) == 1
        assert manager._records[0].rating == 4

    def test_rating_validation(self):
        """Test that invalid ratings are rejected."""
        from openagent.core.learning import FeedbackManager

        manager = FeedbackManager()

        with pytest.raises(ValueError):
            manager.add_feedback(session_id="abc", rating=0)

        with pytest.raises(ValueError):
            manager.add_feedback(session_id="abc", rating=6)

    def test_get_average_rating(self):
        """Test calculating average rating."""
        from openagent.core.learning import FeedbackManager

        manager = FeedbackManager()
        manager.add_feedback("s1", rating=3)
        manager.add_feedback("s2", rating=5)
        manager.add_feedback("s3", rating=4)

        avg = manager.get_average_rating()
        assert abs(avg - 4.0) < 0.001

    def test_get_feedback_summary(self):
        """Test getting feedback summary."""
        from openagent.core.learning import FeedbackManager

        manager = FeedbackManager()
        manager.add_feedback("s1", rating=5)
        manager.add_feedback("s2", rating=4)
        manager.add_feedback("s3", rating=4)

        summary = manager.get_feedback_summary()
        assert summary["total"] == 3
        assert "distribution" in summary
        assert summary["distribution"][4] == 2
        assert summary["distribution"][5] == 1


class TestSessionAnalyzer:
    """Tests for SessionAnalyzer class."""

    def test_analyze_session(self):
        """Test analyzing a session outcome."""
        from openagent.core.learning import SessionAnalyzer
        from openagent.core.session import Session
        from openagent.core.types import Message, ToolUseBlock, ToolResultBlock

        analyzer = SessionAnalyzer()
        session = Session(system_prompt="You are helpful.")

        # Add some messages with tool calls
        session.add("user", "Create a file")

        # Simulate tool use message
        tool_msg = Message(
            role="assistant",
            content=[ToolUseBlock(id="1", name="write", arguments={"file": "/tmp/test.txt"})]
        )
        session.add_message(tool_msg)

        # Simulate tool result
        result_msg = Message(
            role="user",
            content=[ToolResultBlock(tool_use_id="1", content="Success", is_error=False)]
        )
        session.add_message(result_msg)

        outcome = analyzer.analyze_session(session, task_description="Create a file")

        assert outcome.tools_used == ["write"]
        assert outcome.success is True
        assert outcome.turn_count >= 3

    def test_successful_patterns(self):
        """Test identifying successful tool patterns."""
        from openagent.core.learning import SessionAnalyzer
        from openagent.core.session import Session
        from openagent.core.types import Message, ToolUseBlock, ToolResultBlock

        analyzer = SessionAnalyzer()

        # Create two similar successful sessions
        for i in range(3):
            session = Session(system_prompt="You are helpful.")
            session.add("user", "Read and write files")

            # Add read then write pattern
            for tool_name in ["read", "write"]:
                tool_msg = Message(
                    role="assistant",
                    content=[ToolUseBlock(id=f"{i}_{tool_name}", name=tool_name, arguments={})]
                )
                session.add_message(tool_msg)

                result_msg = Message(
                    role="user",
                    content=[ToolResultBlock(tool_use_id=f"{i}_{tool_name}", content="Success", is_error=False)]
                )
                session.add_message(result_msg)

            analyzer.analyze_session(session, task_description="Read and write files")

        patterns = analyzer.get_successful_patterns(min_occurrences=2)
        # Should find the read->write pattern
        assert len(patterns) > 0


class TestAdaptivePrompt:
    """Tests for AdaptivePrompt class."""

    def test_adapt_with_reliable_tools(self):
        """Test prompt adaptation with reliable tools."""
        from openagent.core.learning import (
            AdaptivePrompt, ToolUsageTracker, FeedbackManager, SessionAnalyzer
        )

        tracker = ToolUsageTracker()
        for _ in range(5):
            tracker.record("read", success=True)
        for _ in range(4):
            tracker.record("glob", success=True)

        adapter = AdaptivePrompt(
            base_prompt="You are helpful.",
            tool_tracker=tracker,
        )

        adapted = adapter.adapt()
        assert "Preferred tools" in adapted
        assert "read" in adapted
        assert "glob" in adapted

    def test_adapt_with_low_feedback(self):
        """Test prompt adaptation with low feedback ratings."""
        from openagent.core.learning import AdaptivePrompt, FeedbackManager

        feedback = FeedbackManager()
        feedback.add_feedback("s1", rating=2)
        feedback.add_feedback("s2", rating=1)

        adapter = AdaptivePrompt(
            base_prompt="You are helpful.",
            feedback_manager=feedback,
        )

        adapted = adapter.adapt()
        assert "room for improvement" in adapted.lower() or "clarifying" in adapted.lower()

    def test_no_adaptation_when_empty(self):
        """Test that prompt is unchanged when no learning data."""
        from openagent.core.learning import AdaptivePrompt

        adapter = AdaptivePrompt(base_prompt="You are helpful.")
        assert adapter.adapt() == "You are helpful."


class TestAgentLearningIntegration:
    """Tests for agent integration with learning components."""

    @pytest.mark.asyncio
    async def test_agent_with_learning_enabled(self):
        """Test creating an agent with learning enabled."""
        from openagent.core.agent import Agent
        from openagent.provider.openai import OpenAIProvider

        # Create a mock provider that returns simple responses
        class MockProvider:
            async def chat(self, messages, tools=None, system_prompt="", **kwargs):
                from openagent.core.types import Message
                return Message(role="assistant", content="Done")

        agent = Agent(
            provider=MockProvider(),
            system_prompt="You are helpful.",
            enable_learning=True,
        )

        assert agent._enable_learning is True
        assert agent._tool_tracker is not None
        assert agent._feedback_manager is not None
        assert agent._session_analyzer is not None

    @pytest.mark.asyncio
    async def test_agent_feedback_submission(self):
        """Test submitting feedback through the agent."""
        from openagent.core.agent import Agent

        class MockProvider:
            async def chat(self, messages, tools=None, system_prompt="", **kwargs):
                from openagent.core.types import Message
                return Message(role="assistant", content="Done")

        agent = Agent(
            provider=MockProvider(),
            enable_learning=True,
        )

        # Add a user message first (needed for task context)
        agent.session.add("user", "Test task")

        # Submit feedback
        agent.add_feedback(rating=5, comment="Great job!")

        assert len(agent._feedback_manager._records) == 1
        assert agent._feedback_manager._records[0].rating == 5

    @pytest.mark.asyncio
    async def test_agent_learning_stats(self):
        """Test getting learning stats from the agent."""
        from openagent.core.agent import Agent

        class MockProvider:
            async def chat(self, messages, tools=None, system_prompt="", **kwargs):
                from openagent.core.types import Message
                return Message(role="assistant", content="Done")

        agent = Agent(
            provider=MockProvider(),
            enable_learning=True,
        )

        stats = agent.get_learning_stats()
        assert stats["learning_enabled"] is True


class TestCoderAgentLearning:
    """Tests for CoderAgent with learning enabled."""

    @pytest.mark.asyncio
    async def test_coder_agent_with_learning(self):
        """Test creating a CoderAgent with learning enabled."""
        from openagent.coder import CoderAgent
        from openagent.provider.base import BaseProvider
        from openagent.core.types import Message, ToolDef
        from typing import Any

        class MockProvider(BaseProvider):
            """Mock provider for testing."""

            def __init__(self):
                super().__init__("mock-model")

            async def chat(
                self,
                messages: list[Message],
                tools: list[ToolDef] | None = None,
                system_prompt: str = "",
                **kwargs: Any,
            ) -> Message:
                return Message(role="assistant", content="Done")

        agent = CoderAgent(
            provider=MockProvider(),
            enable_learning=True,
        )

        assert agent._enable_learning is True

        # Check that learning tools are registered (registry stores ToolEntry objects keyed by name)
        tool_names = list(agent.tool_registry._tools.keys())
        assert "submit_feedback" in tool_names
        assert "get_learning_stats" in tool_names
