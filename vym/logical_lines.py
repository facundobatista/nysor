"""Logical lines."""


class LogicalLines:
    """Hold the lines to show in the grid."""

    def __init__(self):
        self._lines = {}

    def get(self, row):
        """Return the logical line for the indicated row."""
        return self._lines.get(row)

    def add(self, row, col, textinfo):
        """Add text info to the grid."""
        # expand the textinfo so we have one format per character, to keep our logical grid
        expanded = []
        for text, fmt in textinfo:
            expanded.extend((char, fmt) for char in text)

        print("============= write line row", row)
        prvline = self._lines.setdefault(row, [])
        if col > len(prvline):
            raise ValueError("Trying to write outside the line; needs to rethink model!!!")
        prvline[col: col + len(expanded)] = expanded
        print("============= current", repr("".join(text for text, fmt in prvline)))

    def scroll_vertical(self, top, bottom, delta):
        """Scroll vertically some lines in the grid."""
        print("=============== scroll vertical", top, bottom, delta)
        lines = {}
        for idx in range(top, bottom - 1):
            lines[idx - delta] = self._lines.pop(idx)
        self._lines.update(lines)
        print("========== new logical?")
        for i in range(8):
            xxx = self._lines.get(i)
            if xxx is not None:
                xxx = "".join(text for text, fmt in xxx)
            print("========== x", i, repr(xxx))
