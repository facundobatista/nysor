# FIXME: copyright notices in all files
"""Main program."""

import asyncio
import logging
import math
import sys
from dataclasses import dataclass
from typing import Any
from functools import partial

import qasync
from PyQt6.QtWidgets import QMainWindow, QPushButton, QVBoxLayout, QWidget, QHBoxLayout, QScrollBar
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtCore import QPointF, Qt, QRectF, QSize

from vym.nvim_interface import NvimInterface
from vym.logical_lines import LogicalLines, CharFormat, CharUnderline

# FIXME: isolate some of this, includeing setup in nviminterace and below because of cmd line to a separate module
logging.basicConfig(
    format='%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s', datefmt='%H:%M:%S', stream=sys.stdout)
logger = logging.getLogger(__name__)
print("=======++ ++====== MAIN", __name__)
logger.setLevel(logging.INFO)
# FIXME: replace prints
# FIXME: foffing?


@dataclass
class FontSize:
    """Hold information about cell and char inside it."""
    width: float
    height: float
    ascent: float


## conversion between the names used by Neovim to the styles defined in Qt6
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


def futurize(async_function):
    """Decorator to convert a blocking function in a async task to run in the future."""

    def blocking_function(*args, **kwargs):
        coro = async_function(*args, **kwargs)
        asyncio.create_task(coro)

    return blocking_function


class NvimManager:
    """Dance at the rythm of Neovim.

    Hold the relevant structures and handle all notifications.
    """
    def __init__(self, main_window):
        self.main_window = main_window
        self.structs = {}
        self.options = {}

    def notification_handler(self, method, parameters):
        """Handle a notification from Neovim."""
        assert method == "redraw"
        for submethod, *args in parameters:
            h_name = "_n__" + submethod
            h_meth = getattr(self, h_name, None)
            if h_meth is None:
                logger.warning(
                    "[NvimManager] Submethod %r not implemented, params: %s", submethod, args)
            else:
                try:
                    logger.debug("[NvimManager] Notification: %s - %s", submethod, args)
                    h_meth(*args)
                except Exception:
                    logger.exception("Crash when calling %r with %r", h_name, args)

    # -- notification handlers

    def _n__default_colors_set(self, colors):
        """Set the default colors."""
        rgb_fg, rgb_bg, rgb_sp, _, _ = colors  # last two are ignored because are for terminals
        self.structs.setdefault("default_colors", {}).update({
            "foreground": rgb_fg,
            "background": rgb_bg,
            "special": rgb_sp,
        })

    def _n__flush(self, _):
        """Clear the grid."""
        self.main_window.flush()

    def _n__grid_clear(self, args):
        """Clear the grid."""
        (grid_id,) = args
        assert grid_id == 1  # is it always 1? when do we have more than one?
        self.main_window.clear_display()

    def _n__grid_cursor_goto(self, args):
        """Resize a grid."""
        grid_id, row, col = args
        assert grid_id == 1  # is it always 1? when do we have more than one?
        self.main_window.set_cursor(row, col)

    def _n__grid_line(self, *args):
        """Expose a line in the grid."""
        for item in args:
            grid, row, col_start, cells, wrap = item
            assert grid == 1  # same question we do in grid_resize

            # note we ignore "wrap", couldn't find proper utility for it
            self.main_window.write_display(row, col_start, cells)

    def _n__grid_resize(self, args):
        """Resize a grid."""
        grid_id, width, height = args
        assert grid_id == 1  # is it always 1? when do we have more than one?
        self.main_window.resize_display((width, height))

    def _n__grid_scroll(self, args):
        """Scroll a grid."""
        grid_id, top, bottom, left, right, rows, cols = args
        assert grid_id == 1  # is it always 1? when do we have more than one?
        self.main_window.display.scroll((top, bottom, rows), (left, right, cols))

    def _n__hl_attr_define(self, *args):
        """Add highlights with their attributes.

        E.g.: (
            2,
            {'foreground': 13882323, 'background': 11119017},
            {'foreground': 7, 'background': 242},
            [],
        )
        """
        hl_attrs = self.structs.setdefault("hl-attrs", {})
        for hl_id, rgb_attr, _, info in args:  # third value is ignored as it's for terminals
            assert not info
            hl_attrs[hl_id] = rgb_attr

    def _n__hl_group_set(self, *args):
        """Set highlight groups.

        E.g.: [['SpecialKey', 161], ['EndOfBuffer', 161], ...]
        """
        hl_groups = self.structs.setdefault("hl-groups", {})
        for group_name, hl_id in args:
            hl_groups[group_name] = hl_id

    def _n__mode_change(self, args):
        """Information about cursor mode."""
        mode, mode_idx = args
        # we ignore the mode idx as we stored in the modes in a dict using the name
        mode_info = self.structs["mode-info"][mode]
        self.main_window.change_mode(mode_info)

    def _n__mode_info_set(self, args):
        """Information about cursor mode."""
        cursor_style_enabled, mode_info = args
        assert cursor_style_enabled  # may it come in False? what do we do? delete previous modes?

        info = {}
        for mi in mode_info:
            # store by name (and remove it from the real data, together with short name)
            name = mi.pop("name")
            del mi["short_name"]
            info[name] = mi

        self.structs.setdefault("mode-info", {}).update(info)

    def _n__mouse_on(self, args):
        """Properly ignored."""

    def _n__mouse_off(self, args):
        """Properly ignored."""

    def _n__option_set(self, *options):
        options = dict(options)
        logger.debug("[NvimManager] options set: %s", options)
        self.options.update(options)

        # react to some of those options
        if "guifont" in options:
            name, size = options["guifont"].split(":")
            assert size[0] == "h"
            size = float(size[1:])
            self.main_window.set_font(name, size)

    def _n__set_icon(self, param):
        """Set the icon, if any."""
        (icon,) = param
        if icon:
            logger.warning("[NvimManager] FIXME! need to implement set icon with %r", icon)

    def _n__set_title(self, param):
        """Set title."""
        (title,) = param
        self.main_window.setWindowTitle(title)

    def _n__win_viewport(self, args):
        """Information for the GUI viewport."""
        grid, objinfo, topline, botline, curline, curcol, line_count, scroll_delta = args
        # FIXME: ignore grid and objinfo so far, need to revisit this when multiwindow

        # Note: can't find use to scroll_delta (maybe for smooth scroolbar?)
        self.main_window.adjust_viewport(topline, botline, line_count, curcol)


class BaseDisplay(QWidget):
    """Base widget to isolate as much as possible Qt itself from the Text handling."""

    def __init__(self):
        super().__init__()
        self.widget_size = QSize(100, 100)  # default valid pseudo-useful value
        self.setMouseTracking(True)

        # get *all* keyboard events in this widget
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def focusNextPrevChild(self, next):
        """Do not allow to "navigate" widgets out of here."""
        return False

    def sizeHint(self):
        """Provide the desired size for the widget."""
        print("=++++++===== hint!", self.widget_size)
        return self.widget_size

    def resizeEvent(self, event):
        """Hook up in the event to trigger internal resizing."""
        super().resizeEvent(event)
        print("====++++++======= RESIZE -- TD", (self.width(), self.height()), event.oldSize(), event.size())
        self.window_resize()

    def keyPressEvent(self, event: QKeyEvent):
        """Get all keyboard events."""
        print("\n========== Key", repr(event.text()), hex(event.key()), repr(event.modifiers()))
        key_text = event.text()
        key = event.key()
        modifiers = event.modifiers()
        self.handle_keyboard(key_text, key, modifiers)

    def paintEvent(self, event):
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

    def mousePressEvent(self, event):
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
        grid = 0  # FIXME: may change when multi-edit?

        pos = event.position()
        row, col = self._get_grid_cell(pos.x(), pos.y())
        self.main_window.nvi.future_call(
            "nvim_input_mouse", button_name, action, modifier, grid, row, col
        )

    def mouseReleaseEvent(self, event):
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
        grid = 0  # FIXME: may change when multi-edit?

        pos = event.position()
        row, col = self._get_grid_cell(pos.x(), pos.y())
        self.main_window.nvi.future_call(
            "nvim_input_mouse", button_name, action, modifier, grid, row, col
        )

        # this will make Neovim to yank selection to the "X11 main selection"
        self.main_window.nvi.future_call("nvim_input", '"*ygv')

    def mouseMoveEvent(self, event):
        """Mouse is moving; we only care about this for left button dragging."""
        button = event.buttons()
        if button in (MouseButton.NoButton, MouseButton.RightButton, MouseButton.MiddleButton):
            # ignore the event if not dragging with left button
            return

        # really left, or default to left as mouses can have a ton of buttons
        button_name = "left"
        action = "drag"
        modifier = self._get_button_modifiers(event)
        grid = 0  # FIXME: may change when multi-edit?

        pos = event.position()
        row, col = self._get_grid_cell(pos.x(), pos.y())
        self.main_window.nvi.future_call(
            "nvim_input_mouse", button_name, action, modifier, grid, row, col
        )

    def wheelEvent(self, event):
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
        grid = 0  # FIXME: may change when multi-edit?

        if abs(dx) > trigger_limit:
            action = "right" if dx > 0 else "left"
            modifier = self._get_button_modifiers(event)
            self.main_window.nvi.future_call(
                "nvim_input_mouse", button_name, action, modifier, grid, row, col
            )

        if abs(dy) > trigger_limit:
            action = "up" if dy > 0 else "down"
            modifier = self._get_button_modifiers(event)
            self.main_window.nvi.future_call(
                "nvim_input_mouse", button_name, action, modifier, grid, row, col
            )

    def _get_grid_cell(self, x, y):
        """Return grid's row and column from pixels x and y."""
        col = int(x / self.font_size.width)
        row = int(y / self.font_size.height)
        return row, col

    def _get_button_modifiers(self, event):
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

    def __init__(self, main_window, nvim_manager, loop):
        super().__init__()
        self.main_window = main_window
        self.nvim_manager = nvim_manager
        self.loop = loop  # FIXME: this will go away eventually when "request" can be done easily
        self.initial_resizing_done = False

        # some defaults
        self.font_size = None
        self.display_size = (80, 20)
        self.set_font("Courier", 12)

        self.lines = LogicalLines.empty()
        self.cursor_pos = (0, 0)
        self.cursor_painter = lambda *a: None
        self.need_grid_clearing = True

    def window_resize(self):
        """Inform Neovim of new window size."""
        cols = max(MIN_COLS_ROWS, int(self.width() / self.font_size.width))
        rows = max(MIN_COLS_ROWS, int(self.height() / self.font_size.height))
        self.main_window.nvi.future_call("nvim_ui_try_resize", cols, rows)

    def handle_keyboard(self, key_text, key, modifiers):
        """Handle keyboard events."""
        # simple case when it's just unicode text
        if key_text:
            key_text = key_text.replace("<", "<LT>")
            print("=====++=++======= key simple:", repr(key_text))
            self.main_window.nvi.future_call("nvim_input", key_text)
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
        self.main_window.nvi.future_call("nvim_input", composed)

    def _build_empty_logical_lines(self):
        """Build an empty logical lines."""
        default_fmt = self.main_window._build_text_format(None)
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
        print("========== FM! horiz advance", char_width)
        print("========== FM! ascent", fm.ascent())
        print("========== FM! height", line_height)
        print("========== FM! bound rect", fm.boundingRect("█"))
        print("========== FM! size", fm.size(0, "█"))
        #br = fm.boundingRect("M")
        #self.font_size = (math.ceil(br.width()), math.ceil(br.height()))

        self.resize_view(force=True)

    def resize_view(self, size=None, force=False):
        """Resize the display.

        If size is given (W x H) it is used; else use current size (if not set, default to 80x20.
        """
        print("=++++++== resize", size)
        if size is None:
            size = self.display_size
        #elif size == self.display_size and not force:
        #    # nothing to do, really
        #    return
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

    def write_line(self, row, col, textinfo):
        """Write a line in the display."""
        self.lines.add(row, col, textinfo)

    def scroll(self, vertical, horizontal):
        """Scroll the grid."""
        top, bottom, delta = vertical
        if delta:
            self.lines.scroll_vertical(top, bottom, delta)

        left, right, delta = horizontal
        assert delta == 0  # FIXME: need to implement!!
        if delta:
            self.lines.scroll_horizontal(left, right, delta)

    def flush(self):
        """FIXME."""
        print("========= Flushhhhhhhhh!")
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

    def _draw_undercurl(self, painter, x, y, width, color):
        """Draws a curled underline."""
        # FIXME: improve drawing
        path = QPainterPath()
        amplitude = 1
        period = 6
        path.moveTo(x, y)
        i = 0
        while x + i < x + width:
            cx1 = x + i + period / 2
            cy1 = y + (amplitude if (i // period) % 2 == 0 else -amplitude)
            path.lineTo(cx1, cy1)
            i += period
        painter.setPen(QPen(color, 1))
        painter.drawPath(path)

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
                rect = QRectF(0, base_y, self.width(), cell_height)
                # FIXME: this should NOT be white, what if user has different background?
                painter.fillRect(rect, Qt.GlobalColor.white)
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

            # FIXME: some of all this should be cached in LogicalLine, no need to recreate
            # everything on each paint
            # complete_text = [lc and (lc.char * 2 if lc.is_wide else lc.char) for lc in logical_line]
            # print("================ paint", row, len(complete_text), repr(complete_text))

            for col, logical_char in enumerate(logical_line):
                if logical_char is None:
                    continue

                # get the value for current x, and shift the base for next round
                slot_width, horizontal_shift, char_width = self._get_drawing_widths(logical_char)

                # fuente
                font = self.font  # FIXME??? QFont(self.font_family)
                font.setItalic(logical_char.format.italic)
                font.setBold(logical_char.format.bold)
                painter.setFont(font)

                # foreground
                painter.setPen(logical_char.format.foreground)

                text_x = base_x + horizontal_shift
                text_y = base_y + (cell_height + self.font_size.ascent) / 2 - 2
                painter.drawText(QPointF(text_x, text_y), logical_char.char)

                # strike through
                if logical_char.format.strikethrough:
                    painter.setPen(logical_char.format.foreground)
                    strike_y = text_y - self.font_size.ascent / 3
                    # FIXME: QRectF?
                    painter.drawLine(int(text_x), int(strike_y), int(x + char_width), int(strike_y))

                # underline
                if logical_char.format.underline:
                    painter.setPen(QPen(logical_char.format.underline.color))
                    underline_y = base_y + cell_height - 3

                    match logical_char.format.underline.style:
                        case "underline":
                            painter.drawLine(base_x, underline_y, base_x + slot_width, underline_y)
                        case "underdotted":
                            pen = QPen(logical_char.format.underline.color)
                            pen.setStyle(Qt.PenStyle.DotLine)
                            painter.setPen(pen)
                            painter.drawLine(base_x, underline_y, base_x + slot_width, underline_y)
                        case "underdashed":
                            pen = QPen(logical_char.format.underline.color)
                            pen.setStyle(Qt.PenStyle.DashLine)
                            painter.setPen(pen)
                            painter.drawLine(base_x, underline_y, base_x + slot_width, underline_y)
                        case "underdouble":
                            painter.drawLine(
                                base_x, underline_y - 1, base_x + slot_width, underline_y - 1)
                            painter.drawLine(
                                base_x, underline_y + 1, base_x + slot_width, underline_y + 1)
                        case "undercurl":
                            self._draw_undercurl(
                                painter, base_x, underline_y, slot_width, logical_char.format.underline.color)
                        case _:
                            raise ValueError(
                                f"Invalid underline style: {logical_char.format.underline.style!r}"
                            )

                # # FIXME: remove blue points
                # painter.setPen(QColor(0, 0, 255))
                # painter.drawPoint(QPointF(base_x, base_y))

                # the cursor, if that is the position
                if col == cursor_col and row == cursor_row:
                    # print("============== paint cursor; char, x, width", repr(logical_char.char), base_x, slot_width)
                    self.cursor_painter(painter, base_x, base_y, slot_width - 1)

                base_x += slot_width

    def change_mode(self, mode_info):
        """Change mode."""
        # FIXME: consider a model where we assert what attributes we implement (instead of
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
            assert attr_id_lm == 0  # FIXME: we need to implement this?

        # FIXME: consider a model where all this processing is done when configuration is
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
            # FIXME
            print("============== CURSOR forma!!!", cursor_shape, cursor_perc)

        if mode_info:
            logger.warning("Some mode change info remained unprocessed: %s", mode_info)


class Vym(QMainWindow):
    def __init__(self, loop, path_to_open, nvim_exec_path):
        super().__init__()
        logger.info("Starting Vym")
        self._closing = 0
        self.nvim_manager = NvimManager(self)

        # setup the Neovim interface
        self.nvi = NvimInterface(
            nvim_exec_path, loop, self.nvim_manager.notification_handler, self._quit_callback
        )
        loop.create_task(self.setup_nvim(path_to_open))

        # FIXME: may these go away?
        self.current_display_size = None
        self.current_font = None
        self.nvimhl_to_qtfmt = {}

        # scrollbars
        self.v_scroll = QScrollBar(Qt.Orientation.Vertical)
        self.v_scroll.valueChanged.connect(self.vertical_scroll_changed)
        self.v_scroll.setMinimum(0)
        self.v_scroll.setMaximum(100)
        self.v_scroll_last_position = None
        self.h_scroll = QScrollBar(Qt.Orientation.Horizontal)
        self.h_scroll.valueChanged.connect(self.horizontal_scroll_changed)
        self.h_scroll.setMinimum(0)
        self.h_scroll.setMaximum(100)
        self.h_scroll_last_position = None

        # central widget to hold main layout
        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        # horizontal layout for text display and vertical scroll bar
        hbox = QHBoxLayout()
        self.display = TextDisplay(self, self.nvim_manager, loop)
        self.display.setFocus()
        hbox.addWidget(self.display, stretch=1)
        hbox.addWidget(self.v_scroll)
        self.main_layout.addLayout(hbox)

        # rest of vertical widgets
        self.main_layout.addWidget(self.h_scroll)

        # FIXME: dejar esto para entender que funciona
        self.button = QPushButton("Haz clic aquí", self)
        self.button.clicked.connect(self.test_action)
        self.main_layout.addWidget(self.button)

        self.button2 = QPushButton("Run async task", self)
        self.button2.clicked.connect(lambda: asyncio.create_task(self.async_task()))
        self.main_layout.addWidget(self.button2)

        self.adjustSize()  # FIXME

    async def setup_nvim(self, path_to_open):
        """Set up Neovim."""
        _, api_metadata = await self.nvi.call("nvim_get_api_info")
        version = api_metadata["version"]
        major = version["major"]
        minor = version["minor"]
        patch = version["patch"]
        logger.info("Neovim API info: version %s.%s.%s", major, minor, patch)

        nvim_config = {"ext_linegrid": True}
        await self.nvi.call("nvim_ui_attach", 80, 20, nvim_config)

        if path_to_open is not None:
            # FIXME: qué onda "-" para stdin?
            cmd = {"cmd": "edit", "args": [path_to_open]}
            opts = {"output": False}  # don't capture output
            await self.nvi.call("nvim_cmd", cmd, opts)

    def test_action(self):
        print("Botón de PyQt6 presionado")

        #fmt = QTextCharFormat()
        #fmt.setForeground(QColor("blue"))
        #fmt.setBackground(QColor("yellow"))

        ## hacks
        #self.display.lines[3] = (4, 0, [(" 0123456789" * 8, fmt)])
        #self.display.lines[5] = (5, 0, [(" Moño g 昔 ばなし CULO", fmt)])

        #fmt = QTextCharFormat()
        #fmt.setForeground(QColor("black"))
        #fmt.setBackground(QColor("white"))
        #self.display.lines[6] = (6, 0, [(" emoji 😆d", fmt)])

    async def async_task(self):
        result = await self.nvi.call("nvim_list_uis")
        print("=========== resp, result", repr(result))

    async def _quit(self):
        """Close the GUI after Neovim is down."""
        logger.debug("Start shutdown, asking")
        await self.nvi.quit()
        self._closing = 2  # allows final close
        logger.debug("Start shutdown, done")
        self.close()

    def _quit_callback(self):
        """Close the GUI because of nvim interface request."""
        if self._closing == 0:
            # only if it was not initiated internally
            logger.debug("Shutdown requested by interface")
            self._closing = 2
            self.close()

    def closeEvent(self, event):
        """Close Neovim and then let the rest to finish.

        The event is ignored so the "real closing" is interrupted, which is triggered later
        when Neovim is already down.
        """
        logger.debug("Close requested; current state %d", self._closing)
        if self._closing == 0:
            # start to close; ignore the event so GUI is still alive, but start internal procedures
            self._closing = 1
            event.ignore()
            asyncio.create_task(self._quit())
            return

        if self._closing == 1:
            # the event was received again in the middle of internal closing, keep ignoring it;
            # FIXME: let's put a "timeout" here and only keep ignoring if timeout didn't happen
            event.ignore()
            return

        # we're really done here, let the event propagate so
        assert self._closing == 2
        logger.debug("Bye")
        event.accept()

    def change_mode(self, mode_info):
        """FIXME."""
        self.display.change_mode(mode_info)

    def clear_display(self):
        """Clear the display."""
        self.display.clear()

    def set_font(self, name, size):
        """Set the font in the display."""
        self.display.set_font(name, size)

    def set_cursor(self, row, col):
        """Set the cursor in the display."""
        # default_color = self._build_text_format(None)
        self.display.set_cursor(row, col)  # , default_color)  FIXME

    def resize_display(self, size):
        """Resize the display."""
        self.display.resize_view(size)

    def _build_text_format(self, hl_id: int | None) -> CharFormat:
        """Get the format for the text. If None, return default colors."""
        # the base is always the default color
        default_colors = self.nvim_manager.structs["default_colors"]
        fmt = CharFormat(
            background=QColor(default_colors["background"]),
            foreground=QColor(default_colors["foreground"]),
        )

        # 'special' is color for underline, this is the default, may be modified later
        special_color = QColor(default_colors["special"])

        if hl_id:  # cover also the case of it being 0, which *may* indicate default colors
            hl_attrs = self.nvim_manager.structs["hl-attrs"]
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

    def present_context_window(self):
        """Present a context window with some options for the user."""
        # FIXME
        print("============ MOUSE context window!!")

    def write_display(self, row: int, col: int, sequence: list[Any]):
        """Write a sequence starting in the given row/column.

        The sequence is a list of text, or text and highlight id, or text, highlight id and
        repetitions.
        """  # FIXME
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

        self.display.write_line(row, col, textinfo)

    def flush(self):
        """FIXME."""
        self.display.flush()

    @futurize
    async def adjust_viewport(self, topline, botline, line_count, curcol):
        """Adjust scrollbar according to what Neovim says."""
        display_width, display_height = self.display.display_size

        # vertical: use information from the viewport
        print(f"====++=++== viewport vert {topline=} {botline=} {line_count=}")
        if topline == 0 and line_count <= display_height:
            self.v_scroll.setEnabled(False)
            self.v_scroll.setMaximum(0)
        else:
            self.v_scroll.setEnabled(True)
            self.v_scroll.setPageStep(display_height)
            self.v_scroll.setMaximum(line_count - 1)
            self.v_scroll_last_position = topline  # before setting value to ignore later event
            self.v_scroll.setValue(topline)

        # only if not wrapping, using current column but also queried line lengths
        # FIXME: acá en las opciones se puede pasar el "scope", qué ventana, revisitar cuando
        # tengamos muchas ventanas
        is_wrapping = await self.nvi.call("nvim_get_option_value", "wrap", {})
        if is_wrapping:
            # just turn off the scroll bar as when wrapping all text will be inside the window
            self.h_scroll.setEnabled(False)
            self.h_scroll.setMaximum(0)
            return

        # get the lengths of lines that are currently shown
        buf = 0  # FIXME: why 0?
        start = topline + 1  # getbufline's first line is 1
        end = botline - 1  # botline is the "next line, out of the view"
        cmd = f"map(getbufline({buf}, {start}, {end}), {{key, val -> strlen(val)}})"
        line_lengths = await self.nvi.call("nvim_eval", cmd)
        print(f"====++=++=== viewport horz {is_wrapping=} {curcol=} {line_lengths=}")

        max_line = max(line_lengths)
        if max_line <= display_width:
            self.h_scroll.setEnabled(False)
            self.h_scroll.setMaximum(0)
        else:
            self.h_scroll.setEnabled(True)
            self.h_scroll.setMaximum(max_line)
            win_info = await self.nvi.call("nvim_eval", "getwininfo()")
            assert len(win_info) == 1  # FIXME: multiventana?
            leftcol = win_info[0]["leftcol"]
            print("=====++=++======= left col", leftcol)
            self.h_scroll.setPageStep(display_width)
            self.h_scroll.setMaximum(max_line - 1)
            self.h_scroll_last_position = leftcol  # before setting value to ignore later event
            self.h_scroll.setValue(leftcol)

    def vertical_scroll_changed(self, value):
        """Handle the vertical scroll bar being modified through the widget."""
        delta = value - self.v_scroll_last_position
        print("=====++=++========= scroll vertical:", value, delta)
        if delta > 0:
            # down
            cmdkey = "\x05"
        elif delta < 0:
            # up
            cmdkey = "\x19"
        else:
            print("=====++=++=============== NO MOV")
            return
        self.v_scroll_last_position = value
        self.nvi.future_call("nvim_input", abs(delta) * cmdkey)

    def horizontal_scroll_changed(self, value):
        """Handle the horizontal scroll bar being modified through the widget."""
        delta = value - self.h_scroll_last_position
        print("=====++=++========= scroll horizontal:", value, delta)
        if delta > 0:
            # right
            cmdkey = "zl"
        elif delta < 0:
            # left
            cmdkey = "zh"
        else:
            print("=====++=++=============== NO MOV")
            return
        self.h_scroll_last_position = value
        self.nvi.future_call("nvim_command", f"normal! {abs(delta)}{cmdkey}")


def main(loglevel, nvim_exec_path, path_to_open):
    """Main entry point."""
    logging.getLogger("vym").setLevel(loglevel)

    app = qasync.QApplication(sys.argv)

    event_loop = qasync.QEventLoop(app)
    event_loop.set_debug(True)
    asyncio.set_event_loop(event_loop)

    app_close_event = asyncio.Event()
    app.aboutToQuit.connect(app_close_event.set)

    main_window = Vym(event_loop, path_to_open, nvim_exec_path)
    main_window.show()

    with event_loop:
        event_loop.run_until_complete(app_close_event.wait())
