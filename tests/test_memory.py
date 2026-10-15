"""Tests for the autonomous learning memory system."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from openagent.core.memory import (
    FactMemory,
    MemoryStore,
    PatternMemory,
    PreferenceMemory,
    get_memory_store,
)


class TestPatternMemory:
    """Tests for PatternMemory dataclass."""

    def test_pattern_creation(self):
        """Test creating a pattern memory."""
        pattern = PatternMemory(
            id="test-1",
            task_description="Refactor async function",
            solution_summary="Used context manager pattern",
            code_snippet="async with session(): pass",
            tags=["async", "context-manager"],
            outcome="success",
        )

        assert pattern.task_description == "Refactor async function"
        assert len(pattern.tags) == 2
        assert pattern.usage_count == 0

    def test_pattern_serialization(self):
        """Test pattern to_dict and from_dict."""
        original = PatternMemory(
            id="test-1",
            task_description="Test task",
            solution_summary="Test solution",
            tags=["tag1"],
        )

        data = original.to_dict()
        restored = PatternMemory.from_dict(data)

        assert restored.id == original.id
        assert restored.task_description == original.task_description
        assert restored.tags == original.tags


class TestPreferenceMemory:
    """Tests for PreferenceMemory dataclass."""

    def test_preference_creation(self):
        """Test creating a preference memory."""
        pref = PreferenceMemory(
            id="pref-1",
            category="coding_style",
            preference="User prefers dataclasses over namedtuples",
            context="For data structures",
            confidence=0.9,
        )

        assert pref.category == "coding_style"
        assert pref.confidence == 0.9

    def test_preference_serialization(self):
        """Test preference to_dict and from_dict."""
        original = PreferenceMemory(
            id="pref-1",
            category="tools",
            preference="Prefers pytest over unittest",
        )

        data = original.to_dict()
        restored = PreferenceMemory.from_dict(data)

        assert restored.id == original.id
        assert restored.preference == original.preference


class TestFactMemory:
    """Tests for FactMemory dataclass."""

    def test_fact_creation(self):
        """Test creating a fact memory."""
        fact = FactMemory(
            id="fact-1",
            fact="Project uses pytest-asyncio with asyncio_mode=auto",
            category="testing",
            source_session_id="session-abc",
        )

        assert fact.category == "testing"
        assert fact.source_session_id == "session-abc"


class TestMemoryStore:
    """Tests for MemoryStore class."""

    @pytest.fixture
    def temp_memory_store(self):
        """Create a temporary memory store for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(base_path=tmpdir)
            yield store

    def test_save_and_load_pattern(self, temp_memory_store: MemoryStore):
        """Test saving and retrieving a pattern."""
        pattern = PatternMemory(
            id="pattern-1",
            task_description="Fix async context manager issue",
            solution_summary="Used asyncio.ensure_future",
            tags=["async", "bugfix"],
        )

        temp_memory_store.save_pattern(pattern)

        retrieved = temp_memory_store.get_pattern("pattern-1")
        assert retrieved is not None
        assert retrieved.task_description == pattern.task_description

    def test_query_patterns_keyword_match(self, temp_memory_store: MemoryStore):
        """Test querying patterns with keyword matching."""
        # Save multiple patterns
        patterns = [
            PatternMemory(
                id="pattern-1",
                task_description="Fix async context manager issue",
                solution_summary="Used asyncio.ensure_future for proper handling",
                tags=["async", "context-manager"],
            ),
            PatternMemory(
                id="pattern-2",
                task_description="Implement REST API endpoint",
                solution_summary="Used FastAPI with Pydantic models",
                tags=["api", "fastapi"],
            ),
        ]

        for p in patterns:
            temp_memory_store.save_pattern(p)

        # Query for async-related patterns
        results = temp_memory_store.query_patterns("async context manager")

        assert len(results) > 0
        # First result should be the async pattern
        assert "async" in results[0].task_description.lower()

    def test_query_patterns_no_match(self, temp_memory_store: MemoryStore):
        """Test querying patterns with no matches returns low-scoring results.

        With min_score=0.0, we return all patterns sorted by score and let the LLM filter.
        This is more agentic than over-engineering keyword matching thresholds.
        """
        pattern = PatternMemory(
            id="pattern-1",
            task_description="Fix database connection",
            solution_summary="Added retry logic",
            tags=["database"],
        )

        temp_memory_store.save_pattern(pattern)

        # Query for unrelated topic - returns the pattern but with low score
        results = temp_memory_store.query_patterns("quantum physics teleportation")

        # Results are returned (sorted by score, lowest first for no match)
        assert len(results) == 1
        assert results[0].id == "pattern-1"

    def test_save_and_load_preference(self, temp_memory_store: MemoryStore):
        """Test saving and retrieving preferences."""
        pref = PreferenceMemory(
            id="pref-1",
            category="coding_style",
            preference="Use type hints everywhere",
            confidence=1.0,
        )

        temp_memory_store.save_preference(pref)

        prefs = temp_memory_store.get_preferences()
        assert len(prefs) > 0

        # Filter by category
        style_prefs = temp_memory_store.get_preferences(category="coding_style")
        assert len(style_prefs) == 1
        assert style_prefs[0].preference == pref.preference

    def test_update_preference_confidence(self, temp_memory_store: MemoryStore):
        """Test updating preference confidence."""
        pref = PreferenceMemory(
            id="pref-1",
            category="tools",
            preference="Use black for formatting",
            confidence=0.8,
        )

        temp_memory_store.save_preference(pref)

        # Increase confidence
        result = temp_memory_store.update_preference_confidence("pref-1", 0.1)
        assert result is True

        updated = temp_memory_store.get_preferences(category="tools")[0]
        assert updated.confidence == 0.9

        # Test clamping at 1.0
        temp_memory_store.update_preference_confidence("pref-1", 0.5)
        updated = temp_memory_store.get_preferences(category="tools")[0]
        assert updated.confidence == 1.0

    def test_save_and_query_project_fact(self, temp_memory_store: MemoryStore):
        """Test saving and querying project-specific facts."""
        fact = FactMemory(
            id="fact-1",
            fact="Uses PostgreSQL for database",
            category="dependencies",
            source_session_id="session-123",
        )

        temp_memory_store.save_project_fact("my-project", fact)

        # Query all facts for project
        facts = temp_memory_store.query_project("my-project")
        assert len(facts) == 1
        assert facts[0].fact == fact.fact

    def test_query_project_with_keywords(self, temp_memory_store: MemoryStore):
        """Test querying project facts with keyword filtering."""
        facts = [
            FactMemory(
                id="fact-1",
                fact="Uses PostgreSQL for database",
                category="dependencies",
                source_session_id="session-1",
            ),
            FactMemory(
                id="fact-2",
                fact="Testing with pytest and coverage",
                category="testing",
                source_session_id="session-2",
            ),
        ]

        for f in facts:
            temp_memory_store.save_project_fact("my-project", f)

        # Query with keyword
        results = temp_memory_store.query_project("my-project", keywords=["postgres"])

        assert len(results) == 1
        assert "PostgreSQL" in results[0].fact

    def test_project_isolation(self, temp_memory_store: MemoryStore):
        """Test that facts are isolated per project."""
        fact1 = FactMemory(
            id="fact-1",
            fact="Project A uses Django",
            category="dependencies",
            source_session_id="session-1",
        )

        fact2 = FactMemory(
            id="fact-2",
            fact="Project B uses FastAPI",
            category="dependencies",
            source_session_id="session-2",
        )

        temp_memory_store.save_project_fact("project-a", fact1)
        temp_memory_store.save_project_fact("project-b", fact2)

        # Query project A should not return project B's facts
        project_a_facts = temp_memory_store.query_project("project-a")
        assert len(project_a_facts) == 1
        assert "Django" in project_a_facts[0].fact

    def test_increment_pattern_usage(self, temp_memory_store: MemoryStore):
        """Test incrementing pattern usage count."""
        pattern = PatternMemory(
            id="pattern-1",
            task_description="Common bug fix",
            solution_summary="Added null check",
            tags=["bugfix"],
            usage_count=0,
        )

        temp_memory_store.save_pattern(pattern)

        # Increment usage
        result = temp_memory_store.increment_pattern_usage("pattern-1")
        assert result is True

        updated = temp_memory_store.get_pattern("pattern-1")
        assert updated.usage_count == 1

    def test_get_all_projects(self, temp_memory_store: MemoryStore):
        """Test getting list of all projects."""
        # Add facts to multiple projects
        for project_id in ["project-a", "project-b", "project-c"]:
            fact = FactMemory(
                id=f"fact-{project_id}",
                fact=f"Fact for {project_id}",
                category="general",
                source_session_id="session-1",
            )
            temp_memory_store.save_project_fact(project_id, fact)

        projects = temp_memory_store.get_all_projects()

        assert len(projects) == 3
        assert "project-a" in projects
        assert "project-b" in projects


class TestLearningExtractor:
    """Tests for LearningExtractor class."""

    @pytest.mark.asyncio
    async def test_extract_with_llm_parses_json(self):
        """Test that _extract_with_llm correctly parses JSON response."""
        from openagent.core.learning_extractor import LearningExtractor
        from openagent.provider.base import BaseProvider

        # Mock provider
        mock_provider = MagicMock(spec=BaseProvider)

        # Simulate LLM returning valid JSON
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "patterns": [
                {
                    "task_description": "Test task",
                    "solution_summary": "Test solution",
                    "tags": ["test"],
                    "outcome": "success"
                }
            ],
            "preferences": [],
            "facts": []
        })

        mock_provider.chat = AsyncMock(return_value=mock_response)

        extractor = LearningExtractor(provider=mock_provider)

        # Call the method
        result = await extractor._extract_with_llm("test session", "project-1")

        assert "patterns" in result
        assert len(result["patterns"]) == 1
        assert result["patterns"][0]["task_description"] == "Test task"

    @pytest.mark.asyncio
    async def test_extract_with_llm_handles_markdown_json(self):
        """Test that _extract_with_llm handles JSON wrapped in markdown."""
        from openagent.core.learning_extractor import LearningExtractor
        from openagent.provider.base import BaseProvider

        mock_provider = MagicMock(spec=BaseProvider)

        # Simulate LLM returning JSON in markdown code block
        mock_response = MagicMock()
        mock_response.content = """```json
{
  "patterns": [],
  "preferences": [
    {
      "category": "coding_style",
      "preference": "Use type hints"
    }
  ],
  "facts": []
}
```"""

        mock_provider.chat = AsyncMock(return_value=mock_response)

        extractor = LearningExtractor(provider=mock_provider)
        result = await extractor._extract_with_llm("test session", "project-1")

        assert len(result["preferences"]) == 1
        assert result["preferences"][0]["preference"] == "Use type hints"

    @pytest.mark.asyncio
    async def test_extract_with_llm_handles_invalid_json(self):
        """Test that _extract_with_llm returns empty arrays on invalid JSON."""
        from openagent.core.learning_extractor import LearningExtractor
        from openagent.provider.base import BaseProvider

        mock_provider = MagicMock(spec=BaseProvider)

        # Simulate LLM returning invalid JSON
        mock_response = MagicMock()
        mock_response.content = "This is not valid JSON at all {{{"

        mock_provider.chat = AsyncMock(return_value=mock_response)

        extractor = LearningExtractor(provider=mock_provider)
        result = await extractor._extract_with_llm("test session", "project-1")

        # Should return empty arrays instead of crashing
        assert result == {"patterns": [], "preferences": [], "facts": []}

    def test_parse_patterns(self):
        """Test _parse_patterns converts data to PatternMemory objects."""
        from openagent.core.learning_extractor import LearningExtractor
        from openagent.provider.base import BaseProvider

        mock_provider = MagicMock(spec=BaseProvider)
        extractor = LearningExtractor(provider=mock_provider)

        data = [
            {
                "task_description": "Fix bug",
                "solution_summary": "Added null check",
                "code_snippet": "if x is not None: pass",
                "tags": ["bugfix"],
                "outcome": "success"
            }
        ]

        patterns = extractor._parse_patterns(data)

        assert len(patterns) == 1
        assert patterns[0].task_description == "Fix bug"
        assert patterns[0].code_snippet == "if x is not None: pass"

    def test_parse_preferences(self):
        """Test _parse_preferences converts data to PreferenceMemory objects."""
        from openagent.core.learning_extractor import LearningExtractor
        from openagent.provider.base import BaseProvider

        mock_provider = MagicMock(spec=BaseProvider)
        extractor = LearningExtractor(provider=mock_provider)

        data = [
            {
                "category": "coding_style",
                "preference": "Use dataclasses",
                "context": "For simple data structures"
            }
        ]

        preferences = extractor._parse_preferences(data)

        assert len(preferences) == 1
        assert preferences[0].category == "coding_style"
        assert preferences[0].context == "For simple data structures"

    def test_parse_facts(self):
        """Test _parse_facts converts data to FactMemory objects."""
        from openagent.core.learning_extractor import LearningExtractor
        from openagent.provider.base import BaseProvider

        mock_provider = MagicMock(spec=BaseProvider)
        extractor = LearningExtractor(provider=mock_provider)

        data = [
            {
                "fact": "Uses pytest-asyncio",
                "category": "testing"
            }
        ]

        facts = extractor._parse_facts(data, "session-123", "project-abc")

        assert len(facts) == 1
        assert facts[0].fact == "Uses pytest-asyncio"
        # Note: source_session_id is set in FactMemory creation


class TestMemoryStoreSingleton:
    """Tests for the global memory store singleton."""

    def test_get_memory_store_returns_same_instance(self):
        """Test that get_memory_store returns a singleton."""
        # Reset singleton
        from openagent.core import memory as memory_module

        memory_module._memory_store = None

        store1 = get_memory_store()
        store2 = get_memory_store()

        assert store1 is store2

    def test_get_memory_store_with_custom_path(self):
        """Test creating a memory store with custom path."""
        from openagent.core import memory as memory_module

        memory_module._memory_store = None

        with tempfile.TemporaryDirectory() as tmpdir:
            store = get_memory_store(path=tmpdir)
            assert str(store.base_path) == tmpdir


class TestPatternScoring:
    """Tests for pattern query scoring algorithm."""

    @pytest.fixture
    def populated_store(self):
        """Create a memory store with test patterns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(base_path=tmpdir)

            # Add patterns with varying relevance
            patterns = [
                PatternMemory(
                    id="p1",
                    task_description="Implement async HTTP client with retry logic",
                    solution_summary="Used aiohttp with exponential backoff",
                    tags=["async", "http", "retry"],
                    usage_count=5,  # High usage count
                ),
                PatternMemory(
                    id="p2",
                    task_description="Build REST API endpoint",
                    solution_summary="Created FastAPI router with dependency injection",
                    tags=["api", "fastapi"],
                    usage_count=0,
                ),
                PatternMemory(
                    id="p3",
                    task_description="Async database connection pooling",
                    solution_summary="Configured asyncpg pool with connection limits",
                    tags=["async", "database", "pooling"],
                    usage_count=2,
                ),
            ]

            for p in patterns:
                store.save_pattern(p)

            yield store

    def test_usage_count_boosts_score(self, populated_store: MemoryStore):
        """Test that higher usage count boosts pattern score."""
        # Query for "async" - both p1 and p3 match
        results = populated_store.query_patterns("async client", limit=5)

        # p1 should rank higher due to usage_count boost
        assert len(results) > 0
        assert results[0].id == "p1"  # Higher usage count wins


class TestMemoryStoreFileStructure:
    """Tests for memory store file structure."""

    def test_creates_directory_structure(self):
        """Test that memory store creates proper directory structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(base_path=tmpdir)

            # Trigger file creation by saving something
            pattern = PatternMemory(
                id="test",
                task_description="Test",
                solution_summary="Test",
            )
            store.save_pattern(pattern)

            # Check files exist
            assert (store.base_path / "patterns.json").exists()
            assert (store.projects_dir).exists()
            assert (store.base_path / "metadata.json").exists()

    def test_json_files_are_valid(self):
        """Test that JSON files are properly formatted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(base_path=tmpdir)

            pattern = PatternMemory(
                id="test-1",
                task_description="Test task",
                solution_summary="Test solution",
            )
            store.save_pattern(pattern)

            # Read and parse JSON to verify it's valid
            patterns_file = store.base_path / "patterns.json"
            data = json.loads(patterns_file.read_text())

            assert "test-1" in data
            assert data["test-1"]["task_description"] == "Test task"
