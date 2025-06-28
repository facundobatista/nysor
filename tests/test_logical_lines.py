
import pytest

from nysor.logical_lines import LogicalLines, LogicalChar


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
        assert line == [
            LogicalChar("f", "fmt1"),
            LogicalChar("o", "fmt1"),
            LogicalChar("o", "fmt1"),
            LogicalChar(" ", "fmt_default"),
            LogicalChar(" ", "fmt_default"),
        ]

    def test_single_line_complex_textinfo(self):
        """Add content with complex textinfo."""
        ll = LogicalLines(2, 10, "fmt_default")
        ll.add(1, 0, [("foo", "fmt1"), ("X", "fmt2"), ("extra", "fmt1")])

        line = ll.get(1)
        assert line == [
            LogicalChar("f", "fmt1"),
            LogicalChar("o", "fmt1"),
            LogicalChar("o", "fmt1"),
            LogicalChar("X", "fmt2"),
            LogicalChar("e", "fmt1"),
            LogicalChar("x", "fmt1"),
            LogicalChar("t", "fmt1"),
            LogicalChar("r", "fmt1"),
            LogicalChar("a", "fmt1"),
            LogicalChar(" ", "fmt_default"),
        ]

    def test_wide_character(self):
        """The special char is acknowledged to mark a wide character."""
        ll = LogicalLines(2, 6, "fmt_default")
        ll.add(1, 0, [("xy", "fmt1"), ("W", "fmt2"), (None, "fmt2"), ("z", "fmt3")])

        line = ll.get(1)
        assert line == [
            LogicalChar("x", "fmt1"),
            LogicalChar("y", "fmt1"),
            LogicalChar("W", "fmt2", is_wide=True),
            None,
            LogicalChar("z", "fmt3"),
            LogicalChar(" ", "fmt_default"),
        ]


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

        extracted = [ll.get(idx) for idx in range(7)]
        assert extracted == [
            [LogicalChar("a", None)],
            [LogicalChar("b", None)],
            None,
            [LogicalChar("c", None)],
            [LogicalChar("d", None)],
            [LogicalChar("f", None)],
            [LogicalChar("g", None)],
        ]

    def test_negative_multiple(self):
        """Moving down two lines."""
        ll = create_trivial_grid(*"abcdefg")
        ll.scroll_vertical(top=2, bottom=5, delta=-2)

        extracted = [ll.get(idx) for idx in range(7)]
        assert extracted == [
            [LogicalChar("a", None)],
            [LogicalChar("b", None)],
            None,
            None,
            [LogicalChar("c", None)],
            [LogicalChar("f", None)],
            [LogicalChar("g", None)],
        ]

    def test_positive_single(self):
        """Should move up from 'c', creating a gap at the end; from 'f' is untouched."""
        ll = create_trivial_grid(*"abcdefg")
        ll.scroll_vertical(top=2, bottom=5, delta=1)

        extracted = [ll.get(idx) for idx in range(7)]
        assert extracted == [
            [LogicalChar("a", None)],
            [LogicalChar("b", None)],
            [LogicalChar("d", None)],
            [LogicalChar("e", None)],
            None,
            [LogicalChar("f", None)],
            [LogicalChar("g", None)],
        ]

    def test_positive_multiple(self):
        """Moving up two lines."""
        ll = create_trivial_grid(*"abcdefg")
        ll.scroll_vertical(top=2, bottom=5, delta=2)

        extracted = [ll.get(idx) for idx in range(7)]
        assert extracted == [
            [LogicalChar("a", None)],
            [LogicalChar("b", None)],
            [LogicalChar("e", None)],
            None,
            None,
            [LogicalChar("f", None)],
            [LogicalChar("g", None)],
        ]

    def test_side_effect_add_missing(self):
        """Content may be added to the created-gap-line."""
        ll = create_trivial_grid(*"abcdef")
        ll.scroll_vertical(top=1, bottom=3, delta=1)

        extracted = [ll.get(idx) for idx in range(4)]
        assert extracted == [
            [LogicalChar("a", None)],
            [LogicalChar("c", None)],
            None,
            [LogicalChar("d", None)],
        ]

        # this should be ok!
        ll.add(2, 0, [("foo", "fmt1")])

        extracted = [ll.get(idx) for idx in range(4)]
        assert extracted == [
            [LogicalChar("a", None)],
            [LogicalChar("c", None)],
            [LogicalChar("f", "fmt1"), LogicalChar("o", "fmt1"), LogicalChar("o", "fmt1")],
            [LogicalChar("d", None)],
        ]


class TestLogicalChar:

    def test_simple(self):
        """Basic usage.."""
        lc = LogicalChar("x", "fmt")
        assert lc.char == "x"
        assert lc.format == "fmt"
        assert lc.is_wide is False

        lc.is_wide = True
        assert lc.is_wide is True

        lc = LogicalChar("x", "fmt", is_wide=True)
        assert lc.is_wide is True

    def test_dictkey_ok(self):
        """Can be using in a dict using char and width."""
        d = {}
        lc = LogicalChar("x", "fmt")
        d[lc] = 3
        assert d[lc] == 3

        lc = LogicalChar("x", "fmt", is_wide=True)
        assert lc not in d

    def test_dictkey_not_format(self):
        """The format is not involved when used in a dict key."""
        d = {}
        lc = LogicalChar("x", "fmt1")
        d[lc] = 3
        assert d[lc] == 3

        lc = LogicalChar("x", "fmt2")
        assert d[lc] == 3
