"""Main program."""

import asyncio
import functools
import logging
import math
import sys
from typing import Any

import qasync
from PyQt6.QtWidgets import QMainWindow, QPushButton, QVBoxLayout, QWidget
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QPainter,
    QTextCharFormat,
    QTextLayout,
)
from PyQt6.QtCore import QPointF, Qt, QRectF

from vym.nvim_interface import NvimInterface
from vym.logical_lines import LogicalLines

logging.basicConfig(
    format='%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)
# FIXME: replace prints
# FIXME: foffing?


# conversion between the names used by Neovim to the styles defined in Qt6
UNDERLINE_STYLES = [
    ("underline", QTextCharFormat.UnderlineStyle.SingleUnderline),
    ("undercurl", QTextCharFormat.UnderlineStyle.WaveUnderline),
    ("underdouble", QTextCharFormat.UnderlineStyle.DashDotDotLine),  # not exactly "double" :/
    ("underdotted", QTextCharFormat.UnderlineStyle.DotLine),
    ("underdashed", QTextCharFormat.UnderlineStyle.DashUnderline),
]


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
            assert not wrap  # XXX: need to implement this, if real
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
            self._set_guifont(options["guifont"])

    def _n__set_icon(self, param):
        """Set the icon, if any."""
        (icon,) = param
        if icon:
            logger.warning("[NvimManager] FIXME! need to implement set icon with %r", icon)

    def _n__set_title(self, param):
        """Set title."""
        (title,) = param
        self.main_window.setWindowTitle(title)

    # -- helper methods

    def _set_guifont(self, fontspec):
        """Set the font in the GUI."""
        name, size = fontspec.split(":")
        assert size[0] == "h"
        size = float(size[1:])
        self.main_window.set_font(name, size)


class TextDisplay(QWidget):
    def __init__(self, main_window, nvim_manager):
        super().__init__()
        self.main_window = main_window
        self.nvim_manager = nvim_manager

        # some defaults
        self.cell_size = None
        self.display_size = (80, 20)
        self.set_font("Courier", 12)

        #self.paint_lines = set()
        self.lines = LogicalLines()
        self.cursor_pos = (0, 0)
        self.cursor_painter = None
        self.need_grid_clearing = True

    def clear(self):
        """Clear the display."""
        self.need_grid_clearing = True
        print("================= clear scheduled")
        self.lines = LogicalLines()

    def set_font(self, name, size):
        """Set the font."""
        # when requesting the font itself, round up the size, as it may not work
        # properly with non-ints
        print("========= set font!", name, size)
        self.font = QFont(name, math.ceil(size))
        self.font.setFixedPitch(True)

        # try to set the real size, however it may not work in all systems
        self.font.setPointSizeF(size)

        # store font sizes
        fm = QFontMetricsF(self.font)
        char_width = fm.horizontalAdvance("█")
        line_height = fm.height()
        self.cell_size = (char_width, line_height)
        print("========== FM! horiz advance", char_width)
        print("========== FM! height", line_height)
        print("========== FM! bound rect", fm.boundingRect("█"))
        print("========== FM! size", fm.size(0, "█"))
        #br = fm.boundingRect("M")
        #self.cell_size = (math.ceil(br.width()), math.ceil(br.height()))

        self.resize_view()

    def resize_view(self, size=None):
        """Resize the display.

        If size is given (W x H) it is used; else use current size (if not set, default to 80x20.
        """
        print("========= resize", size)
        if size is None:
            size = self.display_size
        else:
            self.display_size = size
        cols, rows = size

        # adjust display size for font, if we have it
        if self.cell_size is None:
            return

        print("========== resize font size?", self.font, self.cell_size)
        # margin = self.display.frameWidth() + 3
        # view_width = math.ceil(char_width * cols + 2 * margin)
        # view_height = math.ceil(line_height * rows + 2 * margin)

        char_width, line_height = self.cell_size
        view_width = math.ceil(char_width * cols)
        view_height = math.ceil(line_height * rows)
        print("======== full", view_width, view_height)
        self.setFixedSize(view_width, view_height)
        self.main_window.adjustSize()

    def write_line(self, row, col, textinfo):
        """Write a line in the display."""
        self.lines.add(row, col, textinfo)
        ## expand the textinfo so we have one format per character, to keep our logical grid
        #expanded = []
        #for text, fmt in textinfo:
        #    expanded.extend((char, fmt) for char in text)

        #print("============= write line row", row)
        #prvline = self.logical_lines.setdefault(row, [])
        #if col > len(prvline):
        #    raise ValueError("Trying to write outside the line; needs to rethink model!!!")
        #print("============= prev?", len(prvline))
        #prvline[col: col + len(expanded)] = expanded
        #print("============= prev!", len(prvline))
        #print("============= prev=", repr("".join(text for text, fmt in prvline)))
        ##self.paint_lines.add(row)
        ##print("============= rows to repaint", sorted(self.paint_lines))

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
        print("========= new cursor pos", row, col)
        self.cursor_pos = (row, col)

    def _paint_cursor_block(self, painter, start_x, start_y):
        """Draw a cursor as a block."""
        cell_width, cell_height = self.cell_size
        print("============= cursor block (CAMBIAR COLOR)?", (
            start_x, start_y, cell_width, cell_height))
        painter.fillRect(
            int(start_x), int(start_y), int(cell_width), int(cell_height), Qt.GlobalColor.blue)

    def _paint_cursor_vertical(self, percentage, painter, start_x, start_y):
        """Draw a cursor as a block."""
        cell_width, cell_height = self.cell_size
        print("============= cursor vertical (CAMBIAR COLOR)?", percentage, (
            start_x, start_y, cell_width, cell_height))
        painter.fillRect(
            int(start_x), int(start_y),
            int(cell_width * percentage / 100), int(cell_height), Qt.GlobalColor.blue)

    def paintEvent(self, event):
        """Paint the widget."""
        print("======= PAINT!")
        painter = QPainter(self)
        painter.setFont(self.font)

        #if self.need_grid_clearing:
        #    # special initial case to clear up the whole viewer
        #    logger.debug("Initial clearing")
        #    self.need_grid_clearing = False
        #    print("============= CLEAR (really)")
        #    painter.fillRect(self.rect(), Qt.GlobalColor.white)

        cell_width, cell_height = self.cell_size
        for row in range(self.display_size[1]):
            orig_y = row * cell_height
            x = 0  # all logical lines start at column 0

            logical_line = self.lines.get(row)
            if logical_line is None:
                rect = QRectF(x, orig_y, self.width(), cell_height)
                painter.fillRect(rect, Qt.GlobalColor.white)
                continue

            # FIXME: some of all this should be cached in LogicalLine, no need to recreate
            # everything on each paint
            complete_text = "".join(text for text, fmt in logical_line)

            print("================ paint", row, len(complete_text), repr(complete_text))
            layout = QTextLayout(complete_text, self.font)

            # formats
            all_fmt_ranges = []
            for start, (char, fmt) in enumerate(logical_line):
                fmt_range = QTextLayout.FormatRange()
                fmt_range.start = start
                fmt_range.length = 1
                fmt_range.format = fmt
                all_fmt_ranges.append(fmt_range)
            layout.setFormats(all_fmt_ranges)

            layout.beginLayout()
            line = layout.createLine()
            layout.endLayout()

            if line.isValid():
                # extra y to accommodate base of the font to it's place
                natural_height = line.naturalTextRect().height
                delta_y = (line.height() - natural_height() - 1.2) * 2
                line.setPosition(QPointF(0, delta_y))

                # Recortamos lo que se desborda del alto deseado
                rect = QRectF(0, orig_y, self.width(), cell_height - 1)
                painter.save()
                painter.setClipRect(rect)
                layout.draw(painter, QPointF(x, orig_y))
                painter.restore()

                # FIXME: remove blue points
                painter.setPen(QColor(0, 0, 255))
                painter.drawPoint(QPointF(x, orig_y))

            else:
                logger.warning("Invalid display line: %d %d %s", row, logical_line)

        # paint the cursor if we can
        cursor_row, cursor_col = self.cursor_pos
        if self.cursor_painter is not None:
            print("======== cursor paint?", cursor_row)
            logical_line = self.lines.get(cursor_row) or []
            fm = QFontMetricsF(self.font)  # FIXME instantiate once when font is changed?
            after_text = "".join(text for text, fmt in logical_line[:cursor_col])
            print("======== cursor text after?", repr(after_text))
            cursor_x = fm.horizontalAdvance(after_text)
            cursor_y = cursor_row * cell_height
            self.cursor_painter(painter, cursor_x, cursor_y)

        painter.end()
        #self.paint_lines.clear()

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
            self.cursor_painter = self._paint_cursor_block
        elif cursor_shape == "vertical":
            self.cursor_painter = functools.partial(self._paint_cursor_vertical, cursor_perc)
        else:
            # FIXME
            print("============== CURSOR forma!!!", cursor_shape, cursor_perc)

        if mode_info:
            logger.warning("Some mode change info remained unprocessed: %s", mode_info)


class Vym(QMainWindow):
    def __init__(self, loop):
        super().__init__()
        self.nvim_manager = NvimManager(self)

        self.current_display_size = None
        self.current_font = None
        self.nvimhl_to_qtfmt = {}

        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)

        self.display = TextDisplay(self, self.nvim_manager)
        self.layout.addWidget(self.display)

        # FIXME: dejar esto para entender que funciona
        self.button = QPushButton("Haz clic aquí", self)
        self.button.clicked.connect(self.test_action)
        self.layout.addWidget(self.button)

        self.button2 = QPushButton("Run async task", self)
        self.button2.clicked.connect(lambda: asyncio.create_task(self.async_task()))
        self.layout.addWidget(self.button2)

        # setup the Neovim interface
        self.nvi = NvimInterface(loop, self.nvim_manager.notification_handler)
        nvim_config = {"ext_linegrid": True}
        loop.create_task(self.nvi.request(None, "nvim_ui_attach", 80, 20, nvim_config))

    def test_action(self):
        print("Botón de PyQt6 presionado")

        fmt = QTextCharFormat()
        fmt.setForeground(QColor("blue"))
        fmt.setBackground(QColor("yellow"))

        # hacks
        self.display.lines[3] = (4, 0, [(" 0123456789" * 8, fmt)])
        self.display.lines[5] = (5, 0, [(" Moño g 昔 ばなし CULO", fmt)])

        fmt = QTextCharFormat()
        fmt.setForeground(QColor("black"))
        fmt.setBackground(QColor("white"))
        self.display.lines[6] = (6, 0, [(" emoji 😆d", fmt)])

    async def async_task(self):
        def f(result):
            print("=========== resp, result", repr(result))

        await self.nvi.request(f, "nvim_list_uis")

    async def _send_key_to_nvim(self, key):
        """FIXME."""
        await self.nvi.request(None, "nvim_input", key)

    def keyPressEvent(self, event: QKeyEvent):
        """Evita que PyQt6 capture el teclado, permitiendo que Neovim lo maneje por completo."""
        event.ignore()  # FIXME: para qué?
        key = event.text()  # FIXME: eso qué da, y que le podemos pasar a nvim?
        print("\n========== Key", repr(key))
        # FIXME: es raro; el Tab no se ve, y los modificadores a veces vienen o no
        asyncio.create_task(self._send_key_to_nvim(key))

    def closeEvent(self, event):
        """Cierra Neovim correctamente al cerrar la ventana."""
        print("============ close??")
        # FIXME: cerrar el proceso de nvim
        # if self.nvim_process.state() == QProcess.ProcessState.Running:
        #     self.nvim_process.terminate()
        #     self.nvim_process.waitForFinished(3000)
        # event.accept()

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

    def _build_text_format(self, hl_id: int | None) -> QTextCharFormat:
        """Get the format for the text. If None, return default colors."""
        # the base is always the default color
        default_colors = self.nvim_manager.structs["default_colors"]
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(default_colors["foreground"]))
        fmt.setBackground(QColor(default_colors["background"]))

        # 'special' is color for underline, this is the default, may be modified later
        special_color = QColor(default_colors["special"])

        if hl_id:  # cover also the case of it being 0, which *may* indicate default colors
            hl_attrs = self.nvim_manager.structs["hl-attrs"]
            hl = hl_attrs[hl_id].copy()  # copy because will consume

            # basic colors, that may be reversed
            reverse = hl.pop("reverse", False)
            if reverse:
                color_meth = [("foreground", fmt.setBackground), ("background", fmt.setForeground)]
            else:
                color_meth = [("foreground", fmt.setForeground), ("background", fmt.setBackground)]
            for color_indicator, setting_method in color_meth:
                if color_indicator in hl:
                    color = QColor(hl.pop(color_indicator))
                    setting_method(color)

            # other formats
            if "strikethrough" in hl:
                fmt.setFontStrikeOut(hl.pop("strikethrough"))
            if "italic" in hl:
                fmt.setFontItalic(hl.pop("italic"))
            if "bold" in hl:
                if hl.pop("bold"):
                    fmt.setFontWeight(QFont.Weight.Bold)

            # XXX: we need to support 'url', but not sure the info that comes and how it spans

            # XXX: we need to support 'blend', but still not sure how
            #   blend: blend level (0-100). Could be used by UIs to support blending floating
            #   windows to the background or to signal a transparent cursor.

            # all variations of underlining; note the 'for' continues to the end (instead of
            # breaking on first find) because we want to "consume" all possible flags
            underline_style = None
            for nvim_name, qt_style in UNDERLINE_STYLES:
                if hl.pop(nvim_name, False):
                    underline_style = qt_style
            if underline_style is not None:
                if "special" in hl:
                    special_color = QColor(hl.pop("special"))
                fmt.setFontUnderline(True)
                fmt.setUnderlineStyle(underline_style)
                fmt.setUnderlineColor(special_color)

            if hl:
                logger.warning("Some text format remained unprocessed: %s", hl)

        return fmt

    def write_display(self, row: int, col: int, sequence: list[Any]):
        """Write a sequence starting in the given row/column.

        The sequence is a list of text, or text and highlight id, or text, highlight id and
        repetitions.
        """  # FIXME
        textinfo = []
        print("========== WRITE", row, col, sequence)

        fmt = self._build_text_format(None)
        for item in sequence:
            match item:
                case [text]:
                    hl_id = None
                case [text, hl_id]:
                    pass
                case [text, hl_id, repeat]:
                    text = text * repeat
                case _:
                    raise ValueError(f"Wrong sequence format when writing to display: {item!r}")

            if not text:
                # some indications come empty (specially (only?) at the end of each sequence)
                continue

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


if __name__ == "__main__":
    if "-v" in sys.argv or "--verbose" in sys.argv:
        logger.setLevel(logging.DEBUG)

    app = qasync.QApplication(sys.argv)

    event_loop = qasync.QEventLoop(app)
    event_loop.set_debug(True)
    asyncio.set_event_loop(event_loop)

    app_close_event = asyncio.Event()
    app.aboutToQuit.connect(app_close_event.set)

    main_window = Vym(event_loop)
    main_window.show()

    with event_loop:
        event_loop.run_until_complete(app_close_event.wait())
