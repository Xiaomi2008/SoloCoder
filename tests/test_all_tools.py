"""Comprehensive test for all built-in tools including newly implemented ones."""

import asyncio
import json
import re
import tempfile
from pathlib import Path


def main():
    from openagent.tools.builtin import (
        ask_user_question,
        bash,
        bash_background,
        bash_output,
        edit,
        glob,
        grep,
        kill_shell,
        notebook_edit,
        read,
        slash_command,
        skill,
        task,
        todo_list,
        todo_update,
        todo_write,
        web_fetch,
        web_search,
        write,
    )


def extract_session_id(result: str) -> str | None:
    """Extract session ID from bash_background result message."""
    match = re.search(r"'([a-f0-9]+)'", result)
    if match:
        return match.group(1)
    return None

    print("=== Testing All Built-in Tools ===\n")

    # ========================================================================
    # File Operations (already tested)
    # ========================================================================
    print("1. Testing 'write' tool...")
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test.txt"
        result = write(str(test_file.resolve()), "Hello, World!\nThis is a test.")
        print(f"   Result: {result}")

    print("\n2. Testing 'read' tool...")
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test.txt"
        write(str(test_file.resolve()), "Line 1\nLine 2\nLine 3")
        result = read(str(test_file.resolve()))
        print(f"   Content:\n{result}")

    print("\n3. Testing 'edit' tool...")
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test.txt"
        write(str(test_file.resolve()), "Hello World\nHello Python")
        result = edit(str(test_file.resolve()), "World", "Universe")
        print(f"   Result: {result}")

    # ========================================================================
    # Shell & Process Management (NEW - with bash manager integration)
    # ========================================================================
    print("\n4. Testing 'bash' tool...")
    result = bash("echo 'Hello from bash!'")
    print(f"   Result: {result.strip()}")

    print("\n5. Testing 'bash_background' tool (NEW)...")
    session_id_msg = asyncio.run(bash_background("ls -la"))
    print(f"   Message: {session_id_msg}")

    # Extract actual session ID from message
    session_id = extract_session_id(session_id_msg)
    if session_id:
        print(f"   Extracted Session ID: {session_id}")

        print("\n6. Testing 'bash_output' tool...")
        output = bash_output(session_id)  # No await!
        print(f"   Output:\n{output[:500]}")

        print("\n7. Testing 'kill_shell' tool...")
        kill_result = asyncio.run(kill_shell(session_id))
        print(f"   Result: {kill_result}")

    # ========================================================================
    # Web & Search (already tested)
    # ========================================================================
    print("\n8. Testing 'web_search' tool...")
    result = web_search("Python programming language")
    print(f"   First 200 chars: {result[:200]}...")

    print("\n9. Testing 'web_fetch' tool...")
    result = web_fetch("https://example.com")
    print(f"   First 200 chars: {result[:200]}...")

    # ========================================================================
    # Agent Orchestration (NEW - improved message)
    # ========================================================================
    print("\n10. Testing 'task' tool (NEW)...")
    result = task("explore", "Find all Python files in the project", None)
    print(f"   Result: {result}")

    # ========================================================================
    # Workflow (with task manager integration)
    # ========================================================================
    print("\n13. Testing 'todo_write' tool (NEW)...")
    tasks = [
        {"subject": "Implement feature A", "description": "Add new functionality", "activeForm": "Implementing feature A"},
        {"subject": "Write tests", "description": "Create unit tests", "activeForm": "Writing tests"},
    ]
    result = todo_write(tasks)
    print(f"   Result:\n{result}")

    # Get task IDs from the output to test update/list
    import re
    match = re.search(r'task_[a-f0-9]+', result)
    if match:
        task_id = match.group()
        print(f"\n14. Testing 'todo_update' tool (NEW)...")
        result = todo_update(task_id, status="in_progress", subject=f"[DONE] {task_id}")
        print(f"   Result: {result}")

    print("\n15. Testing 'todo_list' tool...")
    result = todo_list()
    print(f"   Result:\n{result}")

    # ========================================================================
    # User Interaction (NEW - improved message)
    # ========================================================================
    print("\n16. Testing 'ask_user_question' tool (NEW)...")
    result = ask_user_question(
        "What would you like to do next?",
        options=["Option A", "Option B", "Option C"],
        multi_select=False,
    )
    print(f"   Result:\n{result}")

    # ========================================================================
    # Extensibility (NEW - with skill/command manager integration)
    # ========================================================================
    print("\n17. Testing 'skill' tool (NEW)...")
    result = skill("nonexistent_skill", None)
    print(f"   Result: {result}")

    print("\n18. Testing 'slash_command' tool (NEW)...")
    result = slash_command("nonexistent_cmd", ["arg1", "arg2"])
    print(f"   Result: {result}")

    # ========================================================================
    # Notebook Edit (already tested)
    # ========================================================================
    print("\n19. Testing 'notebook_edit' tool...")
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.ipynb') as f:
        nb_content = {
            "cells": [
                {"cell_type": "code", "source": ["print('hello')"]},
                {"cell_type": "markdown", "source": ["# Title"]},
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
        json.dump(nb_content, f)
        path = Path(f.name).resolve()

    result = notebook_edit(str(path), cell_index=0, new_source="print('world')")
    print(f"   Result: {result}")

    # Verify the change
    with open(path) as f:
        nb = json.load(f)
    print(f"   Cell 0 source after edit: {nb['cells'][0]['source']}")

    Path(path).unlink()

    # ========================================================================
    # Glob (already tested)
    # ========================================================================
    print("\n20. Testing 'glob' tool...")
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "test.py").touch()
        (Path(tmpdir) / "main.py").touch()
        result = glob("*.py", path=tmpdir)
        print(f"   Result:\n{result}")

    # ========================================================================
    # Grep (already tested)
    # ========================================================================
    print("\n21. Testing 'grep' tool...")
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "test.txt").write_text("Hello World\nFoo Bar")
        result = grep("World", path=tmpdir)
        print(f"   Result:\n{result}")

    print("\n=== All tests completed! ===")


if __name__ == "__main__":
    main()
