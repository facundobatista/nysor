# Copyright 2025-2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""The widget that display all the text from Neovim."""

import logging
import math
from dataclasses import dataclass
from typing import Any
from functools import partial

from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPen,
    QResizeEvent,
    QWheelEvent,
)
from PyQt6.QtCore import QPointF, Qt, QRectF, QSize

from nysor.logical_lines import LogicalLines, CharFormat, CharUnderline

logger = logging.getLogger(__name__)


@dataclass
class FontSize:
    """Hold information about cell and char inside it."""
    width: float
    height: float
    ascent: float


# conversion between the names used by Neovim to the styles defined in Qt6
UNDERLINE_STYLES = [
    "underline",
    "undercurl",
    "underdouble",
    "underdotted",
    "underdashed",
]

# never ask to Neovim a grid smaller than these
MIN_COLS_ROWS = 5

# conversion between Qt key codes and Neovim names for some special keys
QT_NVIM_KEYS_MAP = {
    Qt.Key.Key_Left: "Left",
    Qt.Key.Key_Right: "Right",
    Qt.Key.Key_Up: "Up",
    Qt.Key.Key_Down: "Down",
    Qt.Key.Key_Home: "Home",
    Qt.Key.Key_End: "End",
    Qt.Key.Key_PageUp: "PageUp",
    Qt.Key.Key_PageDown: "PageDown",
    Qt.Key.Key_Insert: "Insert",
    Qt.Key.Key_Delete: "Del",
    Qt.Key.Key_Backspace: "BS",
    Qt.Key.Key_Return: "CR",
    Qt.Key.Key_Enter: "Enter",
    Qt.Key.Key_Tab: "Tab",
    Qt.Key.Key_Escape: "Esc",
    Qt.Key.Key_F1: "F1",
    Qt.Key.Key_F2: "F2",
    Qt.Key.Key_F3: "F3",
    Qt.Key.Key_F4: "F4",
    Qt.Key.Key_F5: "F5",
    Qt.Key.Key_F6: "F6",
    Qt.Key.Key_F7: "F7",
    Qt.Key.Key_F8: "F8",
    Qt.Key.Key_F9: "F9",
    Qt.Key.Key_F10: "F10",
    Qt.Key.Key_F11: "F11",
    Qt.Key.Key_F12: "F12",
}

# Handier
MouseButton = Qt.MouseButton


class BaseDisplay(QWidget):
    """Base widget to isolate as much as possible Qt itself from the Text handling."""

    def __init__(self):
        super().__init__()
        self.widget_size = QSize(100, 100)  # default valid pseudo-useful value
        self.setMouseTracking(True)

        # get *all* keyboard events in this widget
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def focusNextPrevChild(self, _):
        """Do not allow to "navigate" widgets out of here."""
        return False

    def sizeHint(self):
        """Provide the desired size for the widget."""
        return self.widget_size

    def resizeEvent(self, event: QResizeEvent):
        """Hook up in the event to trigger internal resizing."""
        super().resizeEvent(event)
        self.window_resize()

    def keyPressEvent(self, event: QKeyEvent):
        """Get all keyboard events."""
        print("\n========== Key", repr(event.text()), hex(event.key()), repr(event.modifiers()))
        key_text = event.text()
        key = event.key()
        modifiers = event.modifiers()
        self.handle_keyboard(key_text, key, modifiers)

    def paintEvent(self, event: QPaintEvent):
        """Paint the widget."""
        print("======= PAINT!")
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        self.paint(painter)
        painter.end()

    # ----- set of mouse related callbacks
    #
    # Nnothing is really done by the TextDisplay, but by Neovim directly or the main window:
    #
    # left button:
    #    - on press: sent to Neovim, for cursor positioning
    #    - on release: same, but also some chars are sent to handle clipboard properly; note that
    #        double and triple clicking, and related selections, is automatically handled by Neovim
    #
    #  right button:
    #    - on release: called a function in main window to present a context window
    #
    #  middle button:
    #  - on press: sent to Neovim, for clipboard pasting
    #
    #  wheel: sent to Neovim, for displacement
    #
    #  mouse movement: only sent to Neovim if left button is pressed, for "dragging", implies
    #    selection of text
    #
    #  notes:
    #    - in every event sent to Neovim, whatever modifier is used is also sent
    #    - if button is not left/middle/right, is considered left
    #    - if an event is not described above, it's ignored
    #

    def mousePressEvent(self, event: QMouseEvent):
        """A button mouse was pressed."""
        button = event.button()

        if button is MouseButton.RightButton:
            # ignored
            return

        if button is MouseButton.MiddleButton:
            button_name = "middle"
        else:
            # really left, or default to left as mouses can have a ton of buttons
            button_name = "left"

        action = "press"
        modifier = self._get_button_modifiers(event)
        grid = 0  # FIXME.90: may change when multi-edit?

        pos = event.position()
        row, col = self._get_grid_cell(pos.x(), pos.y())
        self.main_window.nvi.future_request(
            "nvim_input_mouse", button_name, action, modifier, grid, row, col
        )

    def mouseReleaseEvent(self, event: QMouseEvent):
        """A button mouse was released."""
        button = event.button()
        if button is MouseButton.RightButton:
            self.main_window.present_context_window()
            return

        if button is MouseButton.MiddleButton:
            # ignored
            return

        # really left, or default to left as mouses can have a ton of buttons
        button_name = "left"

        action = "release"
        modifier = self._get_button_modifiers(event)
        grid = 0  # FIXME.90: may change when multi-edit?

        pos = event.position()
        row, col = self._get_grid_cell(pos.x(), pos.y())
        self.main_window.nvi.future_request(
            "nvim_input_mouse", button_name, action, modifier, grid, row, col
        )

        # this will make Neovim to yank selection to the "X11 main selection"
        self.main_window.nvi.future_request("nvim_command", 'normal! "*ygv')

    def mouseMoveEvent(self, event: QMouseEvent):
        """Mouse is moving; we only care about this for left button dragging."""
        button = event.buttons()
        if button in (MouseButton.NoButton, MouseButton.RightButton, MouseButton.MiddleButton):
            # ignore the event if not dragging with left button
            return

        # really left, or default to left as mouses can have a ton of buttons
        button_name = "left"
        action = "drag"
        modifier = self._get_button_modifiers(event)
        grid = 0  # FIXME.90: may change when multi-edit?

        pos = event.position()
        row, col = self._get_grid_cell(pos.x(), pos.y())
        self.main_window.nvi.future_request(
            "nvim_input_mouse", button_name, action, modifier, grid, row, col
        )

    def wheelEvent(self, event: QWheelEvent):
        """The wheel is used (or a wheel like interface).

        The event information is an angle. As Neovim only supports to be informed in one
        direction, in case of having both displacements in X and Y, we inform twice.

        Also, we inform only when the displacement is noticeable, for interface
        not to be "too nervous". Note that typical wheel of standard mouses will
        inform a delta of 120.
        """
        button_name = "wheel"
        qpoint = event.angleDelta()
        dx, dy = qpoint.x(), qpoint.y()
        trigger_limit = 10
        row, col = 0, 0  # seems to be ignored
        grid = 0  # FIXME.90: may change when multi-edit?

        if abs(dx) > trigger_limit:
            action = "right" if dx > 0 else "left"
            modifier = self._get_button_modifiers(event)
            self.main_window.nvi.future_request(
                "nvim_input_mouse", button_name, action, modifier, grid, row, col
            )

        if abs(dy) > trigger_limit:
            action = "up" if dy > 0 else "down"
            modifier = self._get_button_modifiers(event)
            self.main_window.nvi.future_request(
                "nvim_input_mouse", button_name, action, modifier, grid, row, col
            )

    def _get_grid_cell(self, x: int, y: int):
        """Return grid's row and column from pixels x and y."""
        col = int(x / self.font_size.width)
        row = int(y / self.font_size.height)
        return row, col

    def _get_button_modifiers(self, event: QMouseEvent | QWheelEvent):
        """Return a string indicating the used modifiers, to inform Neovim."""
        indicators = []
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ShiftModifier:
            indicators.append("S")
        if mods & Qt.KeyboardModifier.ControlModifier:
            indicators.append("C")
        if mods & Qt.KeyboardModifier.AltModifier:
            indicators.append("A")
        return "-".join(indicators)

    # ----- end of mouse related event handling methods


class TextDisplay(BaseDisplay):
    """A text display widget."""

    # cache to hold chars drawing widths; cleaned when font changes
    _char_drawing_widths_cache = {}

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.initial_resizing_done = False

        # some defaults
        self.font_size = None
        self.display_size = (80, 20)
        self.set_font("Courier", 12)

        self.lines = LogicalLines.empty()
        self.cursor_pos = (0, 0)
        self.cursor_painter = lambda *a: None
        self.need_grid_clearing = True

        # cache to hold conversions between Neovim's highlight info and Qt formats
        self.nvimhl_to_qtfmt = {}

    def window_resize(self):
        """Inform Neovim of new window size."""
        cols = max(MIN_COLS_ROWS, int(self.width() / self.font_size.width))
        rows = max(MIN_COLS_ROWS, int(self.height() / self.font_size.height))
        self.main_window.nvi.future_request("nvim_ui_try_resize", cols, rows)

    def handle_keyboard(self, key_text, key, modifiers):
        """Handle keyboard events."""
        # simple case when it's just unicode text
        if key_text:
            key_text = key_text.replace("<", "<LT>")
            print("=====++=++======= key simple:", repr(key_text))
            self.main_window.nvi.future_request("nvim_input", key_text)
            return

        # need to compose special keys
        keyname = QT_NVIM_KEYS_MAP.get(key)
        if keyname is None:
            return

        parts = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("C")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("S")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("A")
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            parts.append("D")  # 'D' often represents 'Command' in Mac

        parts.append(keyname)
        composed = f"<{"-".join(parts)}>"
        print("=====++=++======= key composed:", repr(composed))
        self.main_window.nvi.future_request("nvim_input", composed)

    def _build_empty_logical_lines(self):
        """Build an empty logical lines."""
        default_fmt = self._build_text_format(None)
        cols, rows = self.display_size
        print("============= BUILD!")
        return LogicalLines(rows, cols, default_fmt)

    def clear(self):
        """Clear the display."""
        self.need_grid_clearing = True
        print("====++++++======= clear scheduled")
        self.lines = self._build_empty_logical_lines()

    def set_font(self, name, size):
        """Set the font."""
        # when requesting the font itself, round up the size, as it may not work
        # properly with non-ints
        print("========= set font!", name, size)
        self.font = QFont(name, math.ceil(size))
        self.font.setFixedPitch(True)

        # try to set the real size, however it may not work in all systems
        self.font.setPointSizeF(size)

        # clear the cache for the drawing widths
        self._char_drawing_widths_cache.clear()

        # store font sizes
        fm = QFontMetricsF(self.font)
        char_width = fm.horizontalAdvance("M")
        line_height = fm.height()
        self.font_size = FontSize(width=char_width, height=line_height, ascent=fm.ascent())
        self.resize_view(force=True)

    def resize_view(self, size=None, force=False):
        """Resize the display.

        If size is given (W x H) it is used; else use current size (if not set, default to 80x20.
        """
        print("=++++++== resize", size)
        if size is None:
            size = self.display_size
        else:
            self.display_size = size
        cols, rows = size

        # adjust display size for font, if we have it
        if self.font_size is None:
            return

        print("==++++++== resize font size?", self.font, self.font_size)
        # calculate widget desired size, update internal record, and call Qt magic to resize/redraw
        view_width = math.ceil(self.font_size.width * cols)
        view_height = math.ceil(self.font_size.height * rows)
        self.widget_size = QSize(view_width, view_height)
        print("==++++++== resize update a", self.widget_size)
        if force:
            self.updateGeometry()
            self.main_window.adjustSize()

    def scroll(self, vertical, horizontal):
        """Scroll the grid."""
        top, bottom, delta = vertical
        if delta:
            self.lines.scroll_vertical(top, bottom, delta)

        left, right, delta = horizontal
        assert delta == 0  # need to implement if the situation really arises

    def flush(self):
        """Update the window."""
        self.update()

    def set_cursor(self, row, col):
        """Set the cursor position in the display."""
        print("==---======= new cursor pos", row, col)
        self.cursor_pos = (row, col)

    def _paint_cursor_block(self, attr_id, painter, start_x, start_y, width):
        """Draw a cursor as a block."""
        rect = QRectF(start_x, start_y, width, self.font_size.height)
        assert attr_id == 0  # means inverting color, which is what we're only doing here
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.RasterOp_SourceXorDestination)
        painter.fillRect(rect, Qt.GlobalColor.white)
        painter.restore()

    def _paint_cursor_vertical(self, attr_id, percentage, painter, start_x, start_y, width):
        """Draw a cursor as a block."""
        rect = QRectF(start_x, start_y, width * percentage / 100, self.font_size.height)
        assert attr_id == 0  # means inverting color, which is what we're only doing here
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.RasterOp_SourceXorDestination)
        painter.fillRect(rect, Qt.GlobalColor.white)
        painter.restore()

    def _get_drawing_widths(self, logical_char):
        """Define the values for placing chars in the line.

        This is cached per char; note this cache is cleaned when font changes.

        Returns the slot and char widths, and the horizontal shift to start drawing.
        """
        try:
            return self._char_drawing_widths_cache[logical_char]
        except KeyError:
            # not in the cache: calculate, store, and return values
            pass

        fm = QFontMetricsF(self.font)
        char_width = fm.horizontalAdvance(logical_char.char)

        slot_width = self.font_size.width
        shift = 0
        if logical_char.is_wide:
            slot_width *= 2
            shift = (slot_width - char_width) / 2

        values = slot_width, shift, char_width
        self._char_drawing_widths_cache[logical_char] = values
        return values

    def paint(self, painter):
        """Paint (draw) the grid."""
        cell_height = self.font_size.height

        # paint all backgrounds first!
        for row in range(self.display_size[1]):
            base_y = row * cell_height
            base_x = 0

            logical_line = self.lines.get(row)
            if logical_line is None:
                # no logical line, fill with background default color; note that this value is not
                # ready at the very start, but it's there soon enough
                rect = QRectF(0, base_y, self.width(), cell_height)
                default_colors = self.main_window.nvim_notifs.structs.get("default_colors")
                if default_colors is not None:
                    painter.fillRect(rect, QColor(default_colors["background"]))
                continue

            for logical_char in logical_line:
                if logical_char is None:
                    continue

                slot_width, _, _ = self._get_drawing_widths(logical_char)
                x = base_x
                base_x += slot_width

                rect = QRectF(x, base_y, slot_width + 1, cell_height)
                painter.fillRect(rect, logical_char.format.background)

        # the foregrounds
        cursor_row, cursor_col = self.cursor_pos
        for row in range(self.display_size[1]):
            base_y = row * cell_height
            base_x = 0

            logical_line = self.lines.get(row)
            if logical_line is None:
                continue

            for col, logical_char in enumerate(logical_line):
                if logical_char is None:
                    continue

                # get the value for current x, and shift the base for next round
                slot_width, horizontal_shift, char_width = self._get_drawing_widths(logical_char)

                # base font
                self.font.setItalic(logical_char.format.italic)
                self.font.setBold(logical_char.format.bold)
                painter.setFont(self.font)

                # foreground
                painter.setPen(logical_char.format.foreground)

                # draw the text
                text_x = base_x + horizontal_shift
                text_y = base_y + (cell_height + self.font_size.ascent) / 2 - 2
                painter.drawText(QPointF(text_x, text_y), logical_char.char)

                # and effects over the test
                if logical_char.format.strikethrough:
                    self._draw_strikethrough(painter, logical_char, text_x, char_width, text_y)
                if logical_char.format.underline:
                    self._draw_underline(
                        painter, logical_char, base_x, slot_width, base_y, cell_height,
                    )

                # the cursor, if that is the position
                if col == cursor_col and row == cursor_row:
                    self.cursor_painter(painter, base_x, base_y, slot_width - 1)

                base_x += slot_width

    def _draw_strikethrough(self, painter, logical_char, text_x, char_width, text_y):
        """Draw strikethrough effect over the text."""
        painter.setPen(logical_char.format.foreground)
        strike_y = text_y - self.font_size.ascent / 3
        rect = QRectF(text_x, strike_y, text_x + char_width, strike_y)
        painter.drawLine(rect)

    def _draw_underline(self, painter, logical_char, base_x, slot_width, base_y, cell_height):
        """Draw underline effect over the text."""
        underline_y = int(base_y + cell_height - 1)

        match logical_char.format.underline.style:
            case "underline":
                pen = QPen(logical_char.format.underline.color)
                pen.setWidth(2)
                painter.setPen(pen)
                painter.drawLine(
                    QPointF(base_x, underline_y),
                    QPointF(base_x + slot_width, underline_y)
                )

            case "underdotted":
                pen = QPen(logical_char.format.underline.color)
                pen.setStyle(Qt.PenStyle.DotLine)
                painter.setPen(pen)
                painter.drawLine(
                    QPointF(base_x, underline_y),
                    QPointF(base_x + slot_width, underline_y)
                )

            case "underdashed":
                pen = QPen(logical_char.format.underline.color)
                pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawLine(
                    QPointF(base_x, underline_y),
                    QPointF(base_x + slot_width, underline_y)
                )

            case "underdouble":
                pen = QPen(logical_char.format.underline.color)
                painter.setPen(pen)
                painter.drawLine(
                    QPointF(base_x, underline_y),
                    QPointF(base_x + slot_width, underline_y)
                )
                painter.drawLine(
                    QPointF(base_x, underline_y + 3),
                    QPointF(base_x + slot_width, underline_y + 3)
                )

            case "undercurl":
                path = QPainterPath()
                amplitude = 1
                period = 4
                underline_y += 1
                path.moveTo(base_x, underline_y)
                i = 0
                while i < slot_width:
                    cx1 = base_x + i + period / 2
                    cy1 = underline_y + (amplitude if (i // period) % 2 == 0 else -amplitude)
                    path.lineTo(cx1, cy1)
                    i += period
                painter.setPen(QPen(logical_char.format.underline.color, 1))
                painter.drawPath(path)

            case _:
                raise ValueError(
                    f"Invalid underline style: {logical_char.format.underline.style!r}"
                )

    def change_mode(self, mode_info):
        """Change mode."""
        # FIXME.05: consider a model where we assert what attributes we implement (instead of
        # copying and changing the dict) -- even we can declare attributes here and nvimmanager
        # will assert (and maybe alert) when stuff is informed AND NOT EVERY TIME HERE

        mode_info = mode_info.copy()  # copy because will consume
        print("======== cmode!", mode_info)

        # this is just discarded (if present), as neovim documentation says "to be implemented"
        mode_info.pop("mouse_shape", None)

        # these two are also discarded as they are deprecated
        mode_info.pop("hl_id", None)
        mode_info.pop("id_lm", None)

        if "attr_id_lm" in mode_info:
            attr_id_lm = mode_info.pop("attr_id_lm")
            assert attr_id_lm == 0  # need to implement if the situation really arises

        # FIXME.05: consider a model where all this processing is done when configuration is
        # received originally, so the gap between "neovim config" and our "internal config" is
        # done once, and mismatches are detected earlier
        if "blinkon" in mode_info:
            # consider that if one is present, the three will be
            blinkon = mode_info.pop("blinkon")
            blinkoff = mode_info.pop("blinkoff")
            blinkwait = mode_info.pop("blinkwait")
            if blinkon or blinkoff or blinkwait:
                logger.warning(
                    "Cursor blinking not supported yet - on=%d off=%d wait=%d",
                    blinkon,
                    blinkoff,
                    blinkwait,
                )

        # we need a cursor, be gentle with input and default to a full block
        cursor_shape = mode_info.pop("cursor_shape", "block")
        cursor_perc = mode_info.pop("cell_percentage", 20)
        cursor_attr_id = mode_info.pop("attr_id", 0)
        print("========= CURSOR color", cursor_attr_id)
        if cursor_shape == "block":
            self.cursor_painter = partial(self._paint_cursor_block, cursor_attr_id)
        elif cursor_shape == "vertical":
            self.cursor_painter = partial(self._paint_cursor_vertical, cursor_attr_id, cursor_perc)
        else:
            # need to implement if the situation really arises
            raise NotImplementedError(
                "Cursor shape not currently supported: {cursor_shape!r} ({cursor_perc!r})"
            )

        if mode_info:
            logger.warning("Some mode change info remained unprocessed: %s", mode_info)

    def _build_text_format(self, hl_id: int | None) -> CharFormat:
        """Get the format for the text. If None, return default colors."""
        # the base is always the default color
        default_colors = self.main_window.nvim_notifs.structs["default_colors"]
        print("================= default back", default_colors["background"])
        fmt = CharFormat(
            background=QColor(default_colors["background"]),
            foreground=QColor(default_colors["foreground"]),
        )

        # 'special' is color for underline, this is the default, may be modified later
        special_color = QColor(default_colors["special"])

        if hl_id:  # cover also the case of it being 0, which *may* indicate default colors
            hl_attrs = self.main_window.nvim_notifs.structs["hl-attrs"]
            hl = hl_attrs[hl_id].copy()  # copy because will consume

            # basic set of attributes
            attr_names = ("foreground", "background", "strikethrough", "italic", "bold")
            for name in attr_names:
                if name in hl:
                    setattr(fmt, name, hl.pop(name))

            # colors may be reversed
            reverse = hl.pop("reverse", False)
            if reverse:
                fmt.foreground, fmt.background = fmt.background, fmt.foreground

            # XXX: we need to support 'url', but not sure the info that comes and how it spans

            # XXX: we need to support 'blend', but still not sure how
            #   blend: blend level (0-100). Could be used by UIs to support blending floating
            #   windows to the background or to signal a transparent cursor.

            # all variations of underlining; note the 'for' continues to the end (instead of
            # breaking on first find) because we want to "consume" all possible flags
            underline_style = None
            for style in UNDERLINE_STYLES:
                if hl.pop(style, False):
                    underline_style = style
            if underline_style is not None:
                if "special" in hl:
                    special_color = QColor(hl.pop("special"))
                fmt.underline = CharUnderline(color=special_color, style=underline_style)

            if hl:
                logger.warning("Some text format remained unprocessed: %s", hl)

        return fmt

    def write_grid(self, row: int, col: int, sequence: list[Any]):
        """Write a sequence starting in the given row/column.

        The sequence is a list of text, or text and highlight id, or text, highlight id and
        repetitions.
        """
        textinfo = []
        print("====++=++===== WRITE", row, col, sequence)

        fmt = self._build_text_format(None)
        for item in sequence:
            match item:
                case ['']:
                    # special case to indicate that the previous char is width
                    text = None
                case [text]:
                    hl_id = None
                case [text, hl_id]:
                    pass
                case [' ', 0, 0]:
                    # looks like used at the end of each sequence; looks not useful
                    continue
                case [text, hl_id, repeat]:
                    text = text * repeat
                case _:
                    raise ValueError(f"Wrong sequence format when writing to display: {item!r}")

            if hl_id is not None:
                # transform Neovim highlight into Qt format, caching it
                fmt = self.nvimhl_to_qtfmt.get(hl_id)
                if fmt is None:
                    fmt = self._build_text_format(hl_id)
                    self.nvimhl_to_qtfmt[hl_id] = fmt

            textinfo.append((text, fmt))

        self.lines.add(row, col, textinfo)
