# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""The editor's command line."""

from PyQt6.QtWidgets import QLineEdit, QTextEdit
from PyQt6.QtCore import Qt


class MessagesView(QTextEdit):
    """A read-only multi-line text widget for displaying Neovim messages."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.hide()
        self._current_text_lines = []

    def add_line(self, line, replace_last):
        """Add a new line to the text."""
        if self._current_text_lines:
            if replace_last:
                self._current_text_lines[-1] = line
            else:
                self._current_text_lines.append(line)
        else:
            self._current_text_lines.append(line)
        self._set_text("\n".join(self._current_text_lines))

    def _set_text(self, text):  # FIXME: recibir lineas, no el bloque entero
        """Set the text content, adjusting height based on line count."""
        self.setPlainText(text)
        self.show()

        line_count = text.count('\n') + 1
        visible_lines = min(line_count, 5)

        line_height = self.fontMetrics().lineSpacing()
        doc_margin = int(self.document().documentMargin())
        frame_width = self.frameWidth()
        height = visible_lines * line_height + 2 * doc_margin + 2 * frame_width
        self.setFixedHeight(height)

        if line_count > 5:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def clear(self):
        """Clear content and hide the widget."""
        super().clear()
        self._current_text_lines.clear()
        self.hide()


class LineView(QLineEdit):
    """A read-only single-line text widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)

    def set_text(self, text):
        """Set the text content."""
        self.setText(text)

    def clear(self):
        """Clear the text content."""
        super().clear()

    def set_cursor_position(self, pos):
        """Set the cursor to the given position."""
        self.setCursorPosition(pos)
