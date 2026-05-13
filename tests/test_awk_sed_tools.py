"""Tests for awk and sed built-in tools."""

from __future__ import annotations

from openagent.tools.builtin import awk, sed


class TestAwkTool:
    """Test the awk text processing tool."""

    def test_simple_field_extraction(self) -> None:
        result = awk("{print}", "Alice 95 Bob 87", field=1)
        assert result == "Alice"

    def test_multi_line_field_extraction(self) -> None:
        result = awk("{print}", "Alice 95\nBob 87", field=2)
        assert result == "95\n87"

    def test_print_all_lines(self) -> None:
        result = awk("{print $0}", "line1\nline2\nline3")
        assert result == "line1\nline2\nline3"

    def test_print_selected_fields(self) -> None:
        result = awk("{print $1,$2}", "Alice 95 100\nBob 87 92")
        assert result == "Alice 95\nBob 87"

    def test_condition_greater_than(self) -> None:
        result = awk("$1 > 10 {print $0}", "5 10\n20 30\n3 4")
        assert result == "20 30"

    def test_condition_equals(self) -> None:
        result = awk('$2 == 95 {print $0}', "Alice 95\nBob 87\nCharlie 92")
        assert result == "Alice 95"

    def test_condition_with_print_fields(self) -> None:
        result = awk("$1 > 50 {print $0}", "95 Alice\n87 Bob\n30 Charlie")
        assert result == "95 Alice\n87 Bob"

    def test_no_matches(self) -> None:
        result = awk("$1 > 100 {print $0}", "5 10\n3 4")
        assert result == "(no output)"

    def test_empty_input(self) -> None:
        result = awk("{print $0}", "")
        assert result == "(no output)"


class TestSedTool:
    """Test the sed text processing tool."""

    def test_basic_replacement(self) -> None:
        result = sed("s/Hello/Hi/", "Hello World")
        assert result == "Hi World"

    def test_global_replacement(self) -> None:
        result = sed("s/Hello/Hi/g", "Hello Hello Hello")
        assert result == "Hi Hi Hi"

    def test_case_insensitive(self) -> None:
        result = sed("s/hello/hi/i", "Hello HELLO hello")
        assert result == "hi HELLO hello"

    def test_global_case_insensitive(self) -> None:
        result = sed("s/hello/hi/gi", "Hello HELLO hello")
        assert result == "hi hi hi"

    def test_backreferences(self) -> None:
        result = sed(r"s/(\w+) (\w+)/\2, \1/", "Jane Doe")
        assert result == "Doe, Jane"

    def test_multiline(self) -> None:
        result = sed("s/line/LINE/g", "line one\nline two\nline three")
        assert result == "LINE one\nLINE two\nLINE three"

    def test_no_match(self) -> None:
        result = sed("s/notfound/REPLACED/", "Hello World")
        assert result == "Hello World"

    def test_invalid_expression(self) -> None:
        result = sed("invalid", "Hello")
        assert "Error" in result

    def test_only_first_replacement(self) -> None:
        result = sed("s/Hello/Hi/", "Hello Hello")
        assert result == "Hi Hello"
