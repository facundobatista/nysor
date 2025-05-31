
import pytest

from vym.logical_lines import LogicalLines, LogicalChar

MARK = object()


def create_trivial_grid(*lines_content):
    """Create a trivial grid with some indicated lines content without specific formats."""
    q_rows = len(lines_content)
    q_cols = len(lines_content[0])
    ll = LogicalLines(q_rows, q_cols, "fmt_default")
    for idx, line in enumerate(lines_content):
        ll.add(idx, 0, [(line, None)])
    return ll


class TestBasic:

    def test_empty(self):
        """Basic empty structure."""
        ll = LogicalLines(0, 0, "fmt_default")
        line = ll.get(0)
        assert line is None
        line = ll.get(123)
        assert line is None

    def test_single_line_simple_textinfo(self):
        """Add a line, we can only get that one, basic structure."""
        ll = LogicalLines(2, 5, "fmt_default")
        ll.add(1, 0, [("foo", "fmt1")])

        line = ll.get(0)
        assert line == [LogicalChar(' ', 'fmt_default')] * 5
        line = ll.get(2)
        assert line is None

        line = ll.get(1)
        p1, p2, p3, p4, p5 = line
        assert p1 == LogicalChar("f", "fmt1")
        assert p2 == LogicalChar("o", "fmt1")
        assert p3 == LogicalChar("o", "fmt1")
        assert p4 == LogicalChar(" ", "fmt_default")
        assert p5 == LogicalChar(" ", "fmt_default")

    def test_single_line_complex_textinfo(self):
        """Add content with complex textinfo."""
        ll = LogicalLines(2, 10, "fmt_default")
        ll.add(1, 0, [("foo", "fmt1"), ("X", "fmt2"), ("extra", "fmt1")])

        line = ll.get(1)
        assert [lc.char for lc in line] == ["f", "o", "o", "X", "e", "x", "t", "r", "a", " "]
        assert [lc.format for lc in line] == (
            ["fmt1"] * 3 + ["fmt2"] + ["fmt1"] * 5 + ["fmt_default"]
        )


class TestAddingRows:

    def test_offlimit_empty(self):
        """Add in the middle of the line for an empty one."""
        ll = LogicalLines(3, 7, "fmt_default")
        ll.add(1, 2, [("foo", "fmt1")])

        line = ll.get(1)
        assert [lc.char for lc in line] == [" ", " ", "f", "o", "o", " ", " "]
        assert [lc.format for lc in line] == (
            ["fmt_default"] * 2 + ["fmt1"] * 3 + ["fmt_default"] * 2
        )

    def test_offlimit_content(self):
        """Add beyond last column."""
        ll = LogicalLines(3, 3, "fmt_default")
        ll.add(0, 0, [("foo", "fmt1")])

        # can't add from column 5 when line 0 had only 3 columns
        with pytest.raises(ValueError):
            ll.add(0, 5, [("foo", "fmt1")])

    def test_ok_limit(self):
        """Add ok to the same line, just in the limit."""
        ll = LogicalLines(3, 3, "fmt_default")
        ll.add(1, 0, [("foo", "fmt1")])

        ll.add(1, 3, [("bar", "fmt2")])
        line = ll.get(1)
        assert [lc.char for lc in line] == ["f", "o", "o", "b", "a", "r"]
        assert [lc.format for lc in line] == ["fmt1"] * 3 + ["fmt2"] * 3

    def test_ok_overlapped_extended(self):
        """Add ok to the same line, overlapping and extending the limit."""
        ll = LogicalLines(3, 3, "fmt_default")
        ll.add(1, 0, [("foo", "fmt1")])

        ll.add(1, 2, [("bar", "fmt2")])
        line = ll.get(1)
        assert [lc.char for lc in line] == ["f", "o", "b", "a", "r"]
        assert [lc.format for lc in line] == ["fmt1"] * 2 + ["fmt2"] * 3

    def test_ok_overlapped_inside(self):
        """Add ok to the same line, overlapping."""
        ll = LogicalLines(3, 3, "fmt_default")
        ll.add(1, 0, [("foobar", "fmt1")])

        ll.add(1, 2, [("XX", "fmt2")])
        line = ll.get(1)
        assert [lc.char for lc in line] == ["f", "o", "X", "X", "a", "r"]
        assert [lc.format for lc in line] == ["fmt1"] * 2 + ["fmt2"] * 2 + ["fmt1"] * 2


class TestScrollingVertically:

    def test_negative_single(self):
        """Should move down from 'c', creating a gap; from 'f' is untouched."""
        ll = create_trivial_grid(*"abcdefg")
        ll.scroll_vertical(top=2, bottom=5, delta=-1)

        extracted = [ll.get(idx, MARK) for idx in range(7)]
        assert extracted == [
            [LogicalChar("a", None)],
            [LogicalChar("b", None)],
            MARK,
            [LogicalChar("c", None)],
            [LogicalChar("d", None)],
            [LogicalChar("f", None)],
            [LogicalChar("g", None)],
        ]

    def test_negative_multiple(self):
        """Moving down two lines."""
        ll = create_trivial_grid(*"abcdefg")
        ll.scroll_vertical(top=2, bottom=5, delta=-2)

        extracted = [ll.get(idx, MARK) for idx in range(7)]
        assert extracted == [
            [LogicalChar("a", None)],
            [LogicalChar("b", None)],
            MARK,
            MARK,
            [LogicalChar("c", None)],
            [LogicalChar("f", None)],
            [LogicalChar("g", None)],
        ]

    def test_positive_single(self):
        """Should move up from 'c', creating a gap at the end; from 'f' is untouched."""
        ll = create_trivial_grid(*"abcdefg")
        ll.scroll_vertical(top=2, bottom=5, delta=1)

        extracted = [ll.get(idx, MARK) for idx in range(7)]
        assert extracted == [
            [LogicalChar("a", None)],
            [LogicalChar("b", None)],
            [LogicalChar("d", None)],
            [LogicalChar("e", None)],
            MARK,
            [LogicalChar("f", None)],
            [LogicalChar("g", None)],
        ]

    def test_positive_multiple(self):
        """Moving up two lines."""
        ll = create_trivial_grid(*"abcdefg")
        ll.scroll_vertical(top=2, bottom=5, delta=2)

        extracted = [ll.get(idx, MARK) for idx in range(7)]
        assert extracted == [
            [LogicalChar("a", None)],
            [LogicalChar("b", None)],
            [LogicalChar("e", None)],
            MARK,
            MARK,
            [LogicalChar("f", None)],
            [LogicalChar("g", None)],
        ]
