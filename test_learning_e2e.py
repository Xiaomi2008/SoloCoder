"""End-to-end test of autonomous learning system."""

import asyncio
import tempfile
from pathlib import Path


async def main():
    from openagent import get_memory_store, PatternMemory, PreferenceMemory, FactMemory
    from openagent.tools import recall

    # Use a temp directory for memory storage
    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"Using temp memory directory: {tmpdir}\n")

        # Reset the global singleton to use our temp dir
        from openagent.core import memory as memory_module
        memory_module._memory_store = None

        store = get_memory_store(path=tmpdir)

        # Simulate what LearningExtractor would do after a session
        print("=" * 60)
        print("STEP 1: Save learnings (simulating post-session extraction)")
        print("=" * 60)

        # These are the learnings that would be extracted by LLM analysis
        store.save_pattern(PatternMemory(
            id="learn-001",
            task_description="Refactor code to use context manager",
            solution_summary="Used context manager pattern for automatic resource cleanup",
            code_snippet="with open('file.txt') as f:\n    data = f.read()",
            tags=["refactoring", "context-manager", "resources"],
            outcome="success"
        ))

        store.save_preference(PreferenceMemory(
            id="pref-001",
            category="coding_style",
            preference="Prefer context managers over manual resource management"
        ))

        store.save_project_fact("test-project", FactMemory(
            id="fact-001",
            fact="Project prefers Pythonic patterns like context managers",
            category="conventions",
            source_session_id="session-abc123"
        ))

        print("Saved 1 pattern, 1 preference, 1 fact\n")

        # Verify files were created
        print("Memory store contents:")
        print(f"  patterns.json exists: {store.patterns_file.exists()}")
        print(f"  preferences.json exists: {store.preferences_file.exists()}")
        facts_path = store._get_project_facts_path("test-project")
        print(f"  projects/test-project/facts.json exists: {facts_path.exists()}\n")

        # Show actual JSON content
        print("patterns.json content:")
        print(store.patterns_file.read_text())
        print()

        # Now test recall
        print("=" * 60)
        print("STEP 2: Recall learnings (simulating agent querying memory)")
        print("=" * 60)

        recalled = recall(context="refactoring resources", project_id="test-project")
        print(f"\nRecall result:\n{recalled}\n")

        # Verify
        assert "Relevant Past Solutions" in recalled, "Should find patterns"
        assert "context manager" in recalled.lower(), "Should mention context manager"
        assert "User Preferences" in recalled, "Should find preferences"
        assert "Project Knowledge" in recalled, "Should find facts"

        print("=" * 60)
        print("SUCCESS: Autonomous learning system works end-to-end!")
        print("=" * 60)
        print("\nWhat happened:")
        print("1. Post-session extraction saved patterns/preferences/facts to JSON")
        print("2. Recall tool queried memory and returned relevant learnings")
        print("3. Agent can use these learnings for future tasks")


if __name__ == "__main__":
    asyncio.run(main())
