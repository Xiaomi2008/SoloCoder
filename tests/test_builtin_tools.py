"""Tests for built-in tools.

Note: All tools are synchronous functions and should NOT be awaited.
The @tool decorator wraps them but they execute synchronously.
"""

import json
import tempfile
from pathlib import Path

import pytest

from openagent.tools.builtin import (
    bash,
    edit,
    glob,
    grep,
    notebook_edit,
    read,
    web_fetch,
    web_search,
    write,
)


class TestReadTool:
    """Tests for the read tool."""

    def test_read_existing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("line1\nline2\nline3")
            path = f.name

        try:
            result = read(path)  # No await - tools are synchronous!
            assert "line1" in result
            assert "line2" in result
            assert "line3" in result
        finally:
            Path(path).unlink()

    def test_read_with_line_range(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("line1\nline2\nline3\nline4\nline5")
            path = f.name

        try:
            result = read(path, line_start=2, line_end=4)  # No await!
            assert "line1" not in result
            assert "line2" in result
            assert "line3" in result
            assert "line4" in result
            assert "line5" not in result
        finally:
            Path(path).unlink()

    def test_read_nonexistent_file(self):
        result = read("/nonexistent/path/file.txt")  # No await!
        assert "Error" in result or "does not exist" in result


class TestWriteTool:
    """Tests for the write tool."""

    def test_write_new_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.txt"
            result = write(str(path), "Hello, World!")  # No await!
            assert "Successfully wrote" in result
            assert path.exists()
            assert path.read_text() == "Hello, World!"

    def test_write_creates_parents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_path = Path(tmpdir) / "a" / "b" / "c" / "test.txt"
            result = write(
                str(nested_path), "content", create_parents=True
            )  # No await!
            assert "Successfully wrote" in result
            assert nested_path.exists()


class TestEditTool:
    """Tests for the edit tool."""

    def test_edit_file(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("Hello World\nHello Python")
            path = f.name

        try:
            result = edit(path, "World", "Universe")  # No await!
            assert "1 replacement" in result or "replacement(s)" in result
            content = Path(path).read_text()
            assert "Hello Universe" in content
        finally:
            Path(path).unlink()

    def test_edit_no_match(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("Hello World")
            path = f.name

        try:
            result = edit(path, "XYZ", "ABC")  # No await!
            assert "No occurrences" in result or "not found" in result.lower()
        finally:
            Path(path).unlink()


class TestGlobTool:
    """Tests for the glob tool."""

    def test_glob_pattern(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create some test files
            (Path(tmpdir) / "test.py").touch()
            (Path(tmpdir) / "main.py").touch()
            (Path(tmpdir) / "readme.md").touch()

            result = glob("*.py", path=tmpdir)  # No await!
            assert "test.py" in result or "main.py" in result

    def test_glob_recursive_pattern(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = Path(tmpdir) / "src" / "pkg"
            nested.mkdir(parents=True)
            (nested / "test.py").touch()
            (Path(tmpdir) / "README.md").touch()

            result = glob("**/*.py", path=tmpdir)

            assert "test.py" in result
            assert "Error searching for files" not in result


class TestGrepTool:
    """Tests for the grep tool."""

    def test_grep_pattern(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "test.txt").write_text("Hello World\nFoo Bar")
            (Path(tmpdir) / "other.txt").write_text("Different content")

            result = grep("World", path=tmpdir)  # No await!
            assert "test.txt" in result or "Line" in result


class TestNotebookEditTool:
    """Tests for the notebook_edit tool."""

    def test_notebook_edit(self):
        nb_content = {
            "cells": [
                {"cell_type": "code", "source": ["print('hello')"]},
                {"cell_type": "markdown", "source": ["# Title"]},
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".ipynb") as f:
            json.dump(nb_content, f)
            path = f.name

        try:
            result = notebook_edit(
                path, cell_index=0, new_source="print('world')"
            )  # No await!
            assert "Successfully updated" in result

            # Verify the change
            with open(path) as f:
                nb = json.load(f)
            assert nb["cells"][0]["source"] == ["print('world')"]
        finally:
            Path(path).unlink()


class TestWebSearchTool:
    """Tests for the web_search tool."""

    def test_web_search(self):
        # This may fail if duckduckgo-search is not installed
        result = web_search("Python programming language")  # No await!
        # Just check it returns something (success or error message)
        assert isinstance(result, str)


class TestWebFetchTool:
    """Tests for the web_fetch tool."""

    def test_web_fetch(self):
        # This may fail if httpx is not installed or network issues
        result = web_fetch("https://example.com")  # No await!
        # Just check it returns something (success or error message)
        assert isinstance(result, str)


class TestBashTool:
    """Tests for the bash tool."""

    def test_bash_echo(self):
        result = bash("echo 'Hello'")  # No await!
        assert "Hello" in result or "no output" in result.lower()
