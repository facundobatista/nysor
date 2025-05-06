
import pytest

from vym.logical_lines import LogicalLines

MARK = object()


def create_trivial_grid(*lines_content):
    """Create a trivial grid with some indicated lines content without specific formats."""
    ll = LogicalLines()
    for idx, line in enumerate(lines_content):
        ll.add(idx, 0, [(line, None)])
    return ll


class TestBasic:

    def test_empty(self):
        """Basic empty structure."""
        ll = LogicalLines()
        line = ll.get(0)
        assert line is None
        line = ll.get(123)
        assert line is None

    def test_single_line_simple_textinfo(self):
        """Add a line, we can only get that one, basic structure."""
        ll = LogicalLines()
        ll.add(1, 0, [("foo", "fmt1")])

        line = ll.get(0)
        assert line is None
        line = ll.get(2)
        assert line is None

        line = ll.get(1)
        p1, p2, p3 = line
        assert p1 == ("f", "fmt1")
        assert p2 == ("o", "fmt1")
        assert p3 == ("o", "fmt1")

    def test_single_line_complex_textinfo(self):
        """Add content with complex textinfo."""
        ll = LogicalLines()
        ll.add(1, 0, [("foo", "fmt1"), ("X", "fmt2"), ("extra", "fmt1")])

        line = ll.get(1)
        assert [c for c, f in line] == ["f", "o", "o", "X", "e", "x", "t", "r", "a"]
        assert [f for c, f in line] == ["fmt1"] * 3 + ["fmt2"] + ["fmt1"] * 5


class TestAddingRows:

    def test_offlimit_empty(self):
        """Add in the middle of the line for an empty one."""
        ll = LogicalLines()
        ll.add(0, 0, [("foo", "fmt1")])

        # can't add from column 2 when line 1 was empty!
        with pytest.raises(ValueError):
            ll.add(1, 2, [("foo", "fmt1")])

    def test_offlimit_content(self):
        """Add beyond last column."""
        ll = LogicalLines()
        ll.add(0, 0, [("foo", "fmt1")])

        # can't add from column 5 when line 0 had only 3 columns
        with pytest.raises(ValueError):
            ll.add(0, 5, [("foo", "fmt1")])

    def test_ok_limit(self):
        """Add ok to the same line, just in the limit."""
        ll = LogicalLines()
        ll.add(1, 0, [("foo", "fmt1")])

        ll.add(1, 3, [("bar", "fmt2")])
        line = ll.get(1)
        assert [c for c, f in line] == ["f", "o", "o", "b", "a", "r"]
        assert [f for c, f in line] == ["fmt1"] * 3 + ["fmt2"] * 3

    def test_ok_overlapped_extended(self):
        """Add ok to the same line, overlapping and extending the limit."""
        ll = LogicalLines()
        ll.add(1, 0, [("foo", "fmt1")])

        ll.add(1, 2, [("bar", "fmt2")])
        line = ll.get(1)
        assert [c for c, f in line] == ["f", "o", "b", "a", "r"]
        assert [f for c, f in line] == ["fmt1"] * 2 + ["fmt2"] * 3

    def test_ok_overlapped_inside(self):
        """Add ok to the same line, overlapping."""
        ll = LogicalLines()
        ll.add(1, 0, [("foobar", "fmt1")])

        ll.add(1, 2, [("XX", "fmt2")])
        line = ll.get(1)
        assert [c for c, f in line] == ["f", "o", "X", "X", "a", "r"]
        assert [f for c, f in line] == ["fmt1"] * 2 + ["fmt2"] * 2 + ["fmt1"] * 2


class TestScrollingVertically:

    def test_negative_single(self):
        """Should move down from 'c', creating a gap; from 'f' is untouched."""
        ll = create_trivial_grid(*"abcdefg")
        ll.scroll_vertical(top=2, bottom=5, delta=-1)

        extracted = [ll.get(idx, MARK) for idx in range(7)]
        assert extracted == [
            [("a", None)],
            [("b", None)],
            MARK,
            [("c", None)],
            [("d", None)],
            [("f", None)],
            [("g", None)],
        ]

    def test_negative_multiple(self):
        """Moving down two lines."""
        ll = create_trivial_grid(*"abcdefg")
        ll.scroll_vertical(top=2, bottom=5, delta=-2)

        extracted = [ll.get(idx, MARK) for idx in range(7)]
        assert extracted == [
            [("a", None)],
            [("b", None)],
            MARK,
            MARK,
            [("c", None)],
            [("f", None)],
            [("g", None)],
        ]

    def test_positive_single(self):
        """Should move up from 'c', creating a gap at the end; from 'f' is untouched."""
        ll = create_trivial_grid(*"abcdefg")
        ll.scroll_vertical(top=2, bottom=5, delta=1)

        extracted = [ll.get(idx, MARK) for idx in range(7)]
        assert extracted == [
            [("a", None)],
            [("b", None)],
            [("d", None)],
            [("e", None)],
            MARK,
            [("f", None)],
            [("g", None)],
        ]

    def test_positive_multiple(self):
        """Moving up two lines."""
        ll = create_trivial_grid(*"abcdefg")
        ll.scroll_vertical(top=2, bottom=5, delta=2)

        extracted = [ll.get(idx, MARK) for idx in range(7)]
        assert extracted == [
            [("a", None)],
            [("b", None)],
            [("e", None)],
            MARK,
            MARK,
            [("f", None)],
            [("g", None)],
        ]
