"""Tests for the awk tool (structured text processing)."""

from openagent.tools.builtin import (
    awk,
    _filter_rows,
    _sort_rows,
    _unique_rows,
    _aggregate_all,
    _aggregate_grouped,
    _build_output,
)

text_process = awk


# --- Helpers ---

TAB = "\t"


def make_tsv(header: str, *rows: str) -> str:
    """Build a TSV string from a header and data rows."""
    return "\n".join([header] + list(rows))


# --- text_process integration tests ---


class TestTextProcessBasic:
    def test_pass_through(self):
        inp = make_tsv("Name\tAge", "Alice\t30", "Bob\t25")
        result = text_process(inp)
        lines = result.strip().split("\n")
        assert len(lines) == 3
        assert lines[0] == "Name\tAge"

    def test_empty_input(self):
        result = text_process("")
        assert result == "(empty input)"

    def test_select_columns(self):
        inp = make_tsv("Name\tAge\tCity", "Alice\t30\tNYC", "Bob\t25\tLA")
        result = text_process(inp, select_columns=[1, 3])
        lines = result.strip().split("\n")
        assert lines[0] == "Name\tCity"
        assert "Alice" in lines[1] and "NYC" in lines[1]

    def test_no_header(self):
        inp = "a\t1\nb\t2\nc\t3"
        result = text_process(inp, header=False)
        lines = result.strip().split("\n")
        assert lines[0] == "a\t1"

    def test_csv_delimiter(self):
        inp = "Name,Age\nAlice,30\nBob,25"
        result = text_process(inp, delimiter=",")
        assert "Alice" in result and "Bob" in result


class TestTextProcessFilter:
    def test_filter_eq(self):
        inp = make_tsv("Name\tStatus", "Alice\ta", "Bob\tb", "Carol\ta")
        result = text_process(inp, filter_column=2, filter_operator="eq", filter_value="a")
        lines = result.strip().split("\n")
        # header + 2 matches
        assert len(lines) == 3
        assert "Bob" not in result

    def test_filter_gt(self):
        inp = make_tsv("Name\tAge", "Alice\t30", "Bob\t25", "Carol\t35")
        result = text_process(inp, filter_column=2, filter_operator="gt", filter_value="28")
        assert "Alice" in result and "Carol" in result
        assert "Bob" not in result

    def test_filter_contains(self):
        inp = make_tsv("Name\tCity", "Alice\tNew York", "Bob\tLA", "Carol\tNew Orleans")
        result = text_process(inp, filter_column=2, filter_operator="contains", filter_value="New")
        assert "Alice" in result and "Carol" in result
        assert "Bob" not in result


class TestTextProcessSort:
    def test_sort_ascending(self):
        inp = make_tsv("Name\tAge", "Alice\t30", "Bob\t25", "Carol\t35")
        result = text_process(inp, sort_column=2)
        lines = result.strip().split("\n")[1:]  # skip header
        assert "Bob" in lines[0]
        assert "Carol" in lines[2]

    def test_sort_descending(self):
        inp = make_tsv("Name\tAge", "Alice\t30", "Bob\t25", "Carol\t35")
        result = text_process(inp, sort_column=2, sort_reverse=True)
        lines = result.strip().split("\n")[1:]
        assert "Carol" in lines[0]
        assert "Bob" in lines[2]


class TestTextProcessUnique:
    def test_unique_rows(self):
        inp = make_tsv("Name\tAge", "Alice\t30", "Alice\t30", "Bob\t25")
        result = text_process(inp, unique=True)
        assert result.count("Alice") == 1

    def test_unique_by_column(self):
        inp = make_tsv("Name\tAge", "Alice\t30", "Alice\t25", "Bob\t25")
        result = text_process(inp, unique=True, unique_column=1)
        assert result.count("Alice") == 1


class TestTextProcessAggregate:
    def test_sum(self):
        inp = make_tsv("Name\tVal", "A\t10", "B\t20", "C\t30")
        result = text_process(inp, aggregate_column=2, aggregate_function="sum")
        assert "60" in result

    def test_mean(self):
        inp = make_tsv("Name\tVal", "A\t10", "B\t20")
        result = text_process(inp, aggregate_column=2, aggregate_function="mean")
        assert "15" in result

    def test_min_max(self):
        inp = make_tsv("Name\tVal", "A\t10", "B\t5", "C\t30")
        r_min = text_process(inp, aggregate_column=2, aggregate_function="min")
        r_max = text_process(inp, aggregate_column=2, aggregate_function="max")
        assert "5" in r_min
        assert "30" in r_max

    def test_count(self):
        inp = make_tsv("Name\tVal", "A\t10", "B\txyz", "C\t30")
        result = text_process(inp, aggregate_column=2, aggregate_function="count")
        assert "3" in result

    def test_grouped_aggregate(self):
        inp = make_tsv("Dept\tVal", "Eng\t10", "Eng\t20", "Sales\t5")
        result = text_process(
            inp,
            aggregate_column=2,
            aggregate_function="sum",
            group_by_column=1,
        )
        assert "30" in result  # Eng sum
        assert "5" in result   # Sales sum


# --- Helper unit tests ---


class TestFilterRows:
    def test_eq_string(self):
        rows = [["Alice", "a"], ["Bob", "b"]]
        result = _filter_rows(rows, 1, "eq", "a")
        assert len(result) == 1

    def test_neq(self):
        rows = [["Alice", "a"], ["Bob", "b"]]
        result = _filter_rows(rows, 1, "neq", "a")
        assert len(result) == 1
        assert result[0][0] == "Bob"

    def test_lte_numeric(self):
        rows = [["Alice", "25"], ["Bob", "30"]]
        result = _filter_rows(rows, 1, "lte", "28")
        assert len(result) == 1
        assert result[0][0] == "Alice"

    def test_short_row_skipped(self):
        rows = [["Alice"], ["Bob", "b"]]
        result = _filter_rows(rows, 1, "eq", "a")
        assert len(result) == 0


class TestSortRows:
    def test_numeric_sort(self):
        rows = [["Alice", "30"], ["Bob", "5"], ["Carol", "20"]]
        result = _sort_rows(rows, 1, False)
        assert result[0][0] == "Bob"
        assert result[2][0] == "Alice"


class TestUniqueRows:
    def test_by_full_row(self):
        rows = [["a", "1"], ["a", "1"], ["b", "2"]]
        result = _unique_rows(rows, None)
        assert len(result) == 2

    def test_by_column(self):
        rows = [["a", "1"], ["a", "2"], ["b", "3"]]
        result = _unique_rows(rows, 0)
        assert len(result) == 2


class TestAggregateAll:
    def test_sum(self):
        rows = [["a", "10"], ["b", "20"]]
        assert _aggregate_all(rows, 1, "sum") == "30.0"

    def test_skips_non_numeric(self):
        rows = [["a", "10"], ["b", "xyz"], ["c", "20"]]
        assert _aggregate_all(rows, 1, "sum") == "30.0"


class TestAggregateGrouped:
    def test_grouped_mean(self):
        rows = [["Eng", "10"], ["Eng", "20"], ["Sales", "5"]]
        result = _aggregate_grouped(rows, 0, 1, "mean", None)
        assert result[0] == ["Eng", "15.0"]
        assert result[1] == ["Sales", "5.0"]


class TestBuildOutput:
    def test_with_header(self):
        rows = [["Alice", "30"]]
        out = _build_output(rows, include_header=True, header_fields=["Name", "Age"])
        assert out == "Name\tAge\nAlice\t30"

    def test_without_header(self):
        rows = [["Alice", "30"]]
        out = _build_output(rows, include_header=False)
        assert out == "Alice\t30"
