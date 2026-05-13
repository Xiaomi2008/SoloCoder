"""Memory store for autonomous learning across agent sessions."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class PatternMemory:
    """Stores successful problem-solving patterns and solutions."""

    id: str
    task_description: str  # "Refactor async function to use context manager"
    solution_summary: str  # Key approach taken
    code_snippet: str | None = None  # Reusable code pattern (optional)
    outcome: str = "success"  # "success", "partial", "failed but learned"
    tags: list[str] = field(default_factory=list)  # ["async", "context-manager"]
    created_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
    usage_count: int = 0  # Track how often this pattern helps

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PatternMemory:
        return cls(**data)


@dataclass
class PreferenceMemory:
    """Stores user preferences revealed through interactions."""

    id: str
    category: str  # "coding_style", "tools", "formatting", "architecture"
    preference: str  # "User prefers dataclasses over namedtuples"
    context: str | None = None  # When this applies
    confidence: float = 1.0  # How certain we are (can decay)
    created_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PreferenceMemory:
        return cls(**data)


@dataclass
class FactMemory:
    """Stores project-specific facts and decisions."""

    id: str
    fact: str  # "Project uses pytest-asyncio with asyncio_mode=auto"
    category: str  # "testing", "dependencies", "architecture"
    source_session_id: str  # Which session this came from
    created_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FactMemory:
        return cls(**data)


class MemoryStore:
    """Persistent memory store using JSON files for autonomous learning."""

    def __init__(self, base_path: str = "~/.openagent/memory"):
        self.base_path = Path(base_path).expanduser()
        self.patterns_file = self.base_path / "patterns.json"
        self.preferences_file = self.base_path / "preferences.json"
        self.projects_dir = self.base_path / "projects"
        self.metadata_file = self.base_path / "metadata.json"

        self._ensure_dirs()
        self._load_or_init_metadata()

    def _ensure_dirs(self) -> None:
        """Create directory structure if it doesn't exist."""
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(exist_ok=True)

    def _load_or_init_metadata(self) -> None:
        """Initialize metadata file if it doesn't exist."""
        if not self.metadata_file.exists():
            self._save_metadata({
                "created_at": datetime.utcnow().isoformat(),
                "total_patterns": 0,
                "total_preferences": 0,
                "total_facts": 0,
            })

    def _load_json(self, path: Path) -> dict[str, Any]:
        """Load JSON file or return empty dict if not exists."""
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_json(self, path: Path, data: dict[str, Any]) -> None:
        """Save data to JSON file."""
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load_or_init_metadata(self) -> None:
        """Initialize metadata file if it doesn't exist."""
        if not self.metadata_file.exists():
            self._save_metadata({
                "created_at": datetime.utcnow().isoformat(),
                "total_patterns": 0,
                "total_preferences": 0,
                "total_facts": 0,
            })

    def _load_metadata(self) -> dict[str, Any]:
        return self._load_json(self.metadata_file)

    def _save_metadata(self, data: dict[str, Any]) -> None:
        self._save_json(self.metadata_file, data)

    # Pattern Memory Operations

    def save_pattern(self, pattern: PatternMemory) -> None:
        """Save a pattern memory."""
        data = self._load_json(self.patterns_file)
        data[pattern.id] = pattern.to_dict()
        self._save_json(self.patterns_file, data)

        # Update metadata
        meta = self._load_metadata()
        meta["total_patterns"] = len(data)
        self._save_metadata(meta)

    def get_pattern(self, pattern_id: str) -> PatternMemory | None:
        """Get a specific pattern by ID."""
        data = self._load_json(self.patterns_file)
        if pattern_id not in data:
            return None
        return PatternMemory.from_dict(data[pattern_id])

    def query_patterns(
        self, context: str, limit: int = 5, min_score: float = 0.0
    ) -> list[PatternMemory]:
        """
        Query patterns relevant to the given context using keyword matching.

        Uses TF-IDF-like scoring based on word overlap between query and stored patterns.
        """
        data = self._load_json(self.patterns_file)
        if not data:
            return []

        # Tokenize context
        context_tokens = set(
            re.findall(r"\b\w+\b", context.lower())
        ) - {"the", "a", "an", "is", "are", "was", "were", "be", "been"}

        scored_patterns: list[tuple[PatternMemory, float]] = []

        for pattern_id, pattern_data in data.items():
            pattern = PatternMemory.from_dict(pattern_data)

            # Combine searchable fields
            searchable = (
                pattern.task_description +
                " " + pattern.solution_summary +
                " " + " ".join(pattern.tags) +
                " " + (pattern.code_snippet or "")
            ).lower()

            searchable_tokens = set(re.findall(r"\b\w+\b", searchable))

            # Calculate overlap score (Jaccard-like)
            if context_tokens and searchable_tokens:
                intersection = context_tokens & searchable_tokens
                union_set = context_tokens | searchable_tokens
                score = len(intersection) / len(union_set) if union_set else 0.0
            else:
                score = 0.0

            # Boost by usage count (more used patterns are more valuable)
            score *= (1 + pattern.usage_count * 0.1)

            if score >= min_score:
                scored_patterns.append((pattern, score))

        # Sort by score descending and return top results
        scored_patterns.sort(key=lambda x: x[1], reverse=True)
        return [p for p, _ in scored_patterns[:limit]]

    def increment_pattern_usage(self, pattern_id: str) -> bool:
        """Increment usage count for a pattern (when it helps)."""
        data = self._load_json(self.patterns_file)
        if pattern_id not in data:
            return False

        pattern = PatternMemory.from_dict(data[pattern_id])
        pattern.usage_count += 1
        data[pattern_id] = pattern.to_dict()
        self._save_json(self.patterns_file, data)
        return True

    # Preference Memory Operations

    def save_preference(self, preference: PreferenceMemory) -> None:
        """Save a preference memory."""
        data = self._load_json(self.preferences_file)
        data[preference.id] = preference.to_dict()
        self._save_json(self.preferences_file, data)

        # Update metadata
        meta = self._load_metadata()
        meta["total_preferences"] = len(data)
        self._save_metadata(meta)

    def get_preferences(
        self, category: str | None = None
    ) -> list[PreferenceMemory]:
        """Get all preferences, optionally filtered by category."""
        data = self._load_json(self.preferences_file)
        prefs = [PreferenceMemory.from_dict(d) for d in data.values()]

        if category:
            prefs = [p for p in prefs if p.category == category]

        # Sort by confidence descending
        return sorted(prefs, key=lambda x: x.confidence, reverse=True)

    def update_preference_confidence(
        self, preference_id: str, delta: float
    ) -> bool:
        """Update confidence of a preference (positive or negative)."""
        data = self._load_json(self.preferences_file)
        if preference_id not in data:
            return False

        pref = PreferenceMemory.from_dict(data[preference_id])
        pref.confidence = max(0.0, min(1.0, pref.confidence + delta))
        data[preference_id] = pref.to_dict()
        self._save_json(self.preferences_file, data)
        return True

    # Project Fact Operations

    def _get_project_facts_path(self, project_id: str) -> Path:
        """Get path to project facts file."""
        project_dir = self.projects_dir / project_id
        project_dir.mkdir(exist_ok=True)
        return project_dir / "facts.json"

    def save_project_fact(
        self, project_id: str, fact: FactMemory
    ) -> None:
        """Save a fact for a specific project."""
        facts_path = self._get_project_facts_path(project_id)
        data = self._load_json(facts_path)
        data[fact.id] = fact.to_dict()
        self._save_json(facts_path, data)

        # Update metadata
        meta = self._load_metadata()
        if "projects" not in meta:
            meta["projects"] = {}
        meta["projects"][project_id] = len(data)
        self._save_metadata(meta)

    def query_project(
        self, project_id: str, keywords: list[str] | None = None
    ) -> list[FactMemory]:
        """Query facts for a specific project, optionally filtered by keywords."""
        facts_path = self._get_project_facts_path(project_id)
        data = self._load_json(facts_path)

        if not data:
            return []

        facts = [FactMemory.from_dict(d) for d in data.values()]

        if keywords:
            keyword_set = set(k.lower() for k in keywords)
            filtered = []
            for fact in facts:
                searchable = (fact.fact + " " + fact.category).lower()
                if any(kw in searchable for kw in keyword_set):
                    filtered.append(fact)
            return filtered

        return facts

    def get_all_projects(self) -> list[str]:
        """Get list of all project IDs with stored facts."""
        if not self.projects_dir.exists():
            return []

        projects = []
        for item in self.projects_dir.iterdir():
            if item.is_dir() and (item / "facts.json").exists():
                projects.append(item.name)
        return sorted(projects)

    # Utility Methods

    def clear_all(self) -> None:
        """Clear all memories (use with caution!)."""
        self.patterns_file.unlink(missing_ok=True)
        self.preferences_file.unlink(missing_ok=True)
        for project_dir in self.projects_dir.iterdir():
            if project_dir.is_dir():
                for f in project_dir.iterdir():
                    f.unlink()
                project_dir.rmdir()

        self._save_metadata({
            "created_at": datetime.utcnow().isoformat(),
            "total_patterns": 0,
            "total_preferences": 0,
            "total_facts": 0,
        })


# Global memory store singleton
_memory_store: MemoryStore | None = None


def get_memory_store(path: str | None = None) -> MemoryStore:
    """Get or create the global memory store instance."""
    global _memory_store
    if _memory_store is None:
        _memory_store = MemoryStore(base_path=path or "~/.openagent/memory")
    return _memory_store
