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

    def _update_height(self):
        """Recalculate and apply fixed height based on current content and viewport width."""
        line_count = len(self._current_text_lines) or 1
        visible_lines = min(line_count, 5)

        line_height = self.fontMetrics().lineSpacing()
        doc_margin = int(self.document().documentMargin())
        frame_width = self.frameWidth()

        viewport_width = self.viewport().width()
        needs_h_scroll = viewport_width > 0 and self.document().idealWidth() > viewport_width
        scrollbar_height = self.horizontalScrollBar().sizeHint().height() if needs_h_scroll else 0

        new_height = (
            visible_lines * line_height +
            2 * doc_margin +
            2 * frame_width +
            scrollbar_height
        )
        if self.maximumHeight() != new_height:
            self.setFixedHeight(new_height)

        if line_count > 5:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        """Recalculate height on resize, in case horizontal scrollbar need changes."""
        super().resizeEvent(event)
        if self._current_text_lines:
            self._update_height()

    def _set_text(self, text):  # FIXME: recibir lineas, no el bloque entero
        """Set the text content, adjusting height based on line count."""
        self.setPlainText(text)
        self.show()
        self._update_height()

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
