"""Logical lines."""

import logging
from dataclasses import dataclass

from PyQt6.QtGui import QColor

logger = logging.getLogger(__name__)


@dataclass
class CharUnderline:
    color: QColor
    style: str  # "underline", "undercurl", "underdouble", "underdotted", "underdashed"


@dataclass
class CharFormat:
    foreground: QColor
    background: QColor
    strikethrough: bool = False
    italic: bool = False
    bold: bool = False
    underline: CharUnderline | None = None


@dataclass
class LogicalChar:
    char: str
    format: CharFormat
    is_wide: bool = False


class LogicalLines:
    """Hold the lines to show in the grid."""

    def __init__(self, q_rows, q_cols, fmt):
        self._lines = {
            idx: [LogicalChar(" ", fmt) for _ in range(q_cols)] for idx in range(q_rows)
        }

    @classmethod
    def empty(cls):
        return cls(0, 0, None)

    def get(self, row, default=None):
        """Return the logical line for the indicated row."""
        return self._lines.get(row, default)

    def add(self, row, col, textinfo):
        """Add text info to the grid."""
        # expand the textinfo so we have one format per character, to keep our logical grid
        expanded = []
        for text, fmt in textinfo:
            if text == "":
                # special "char" that comes after others to indicate those are width
                expanded.append(LogicalChar(None, fmt))
            else:
                expanded.extend(LogicalChar(char, fmt) for char in text)

        print("============= write line row", row)
        # it's fine to create new lines in the map, because gaps are created when scrolling
        prvline = self._lines.setdefault(row, [])

        if col > len(prvline):
            raise ValueError("Trying to write outside the line; needs to rethink model!!!")
        prvline[col: col + len(expanded)] = expanded
        print("============= current", [lc.char for lc in prvline])

    def scroll_vertical(self, top, bottom, delta):
        """Scroll vertically some lines in the grid."""
        print("=============== scroll vertical", top, bottom, delta)
        if abs(delta) >= bottom - top:
            raise ValueError("Overflow scrolling; not supported yet")

        if delta > 0:
            # goes up; move N lines up and then remove "hole"
            for idx in range(top + delta, bottom):
                print("======= + moving", idx)
                self._lines[idx - delta] = self._lines[idx]
            for idx in range(bottom - delta, bottom):
                print("======= + killing", idx)
                del self._lines[idx]

        elif delta < 0:
            # goes down; move N lines down and then remove "hole"
            for idx in reversed(range(top, bottom + delta)):
                print("======= - moving", idx)
                self._lines[idx - delta] = self._lines[idx]
            for idx in range(top, top - delta):
                print("======= - killing", idx)
                del self._lines[idx]

        else:
            logger.warning("Called scroll vertical with delta=0, shouldn't happen")
            return

        print("========== new logical?")
        for i in range(25):
            xxx = self._lines.get(i)
            if xxx is not None:
                xxx = "".join(lc.char for lc in xxx)
            print("========== x", i, repr(xxx))
