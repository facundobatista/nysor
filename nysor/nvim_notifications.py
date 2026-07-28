# Copyright 2025-2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Receive, process, and manage all notifications from Neovim."""

import logging
from collections import defaultdict
from typing import Any

from nysor.utils import call_async

logger = logging.getLogger(__name__)


class DynamicCache:
    """A cache that is cleaned up when any of the section labels change."""

    def __init__(self):
        self._data = {}
        self._labels = defaultdict(set)

    def get(self, labels, key):
        """Get the key from the cache indicated by labels.

        This is the critical path. The rest of the methods need to accommodate for this
        to be as fast as possible.
        """
        # the real cache is under the tuple of labels
        cache = self._data.setdefault(labels, {})
        return cache.get(key)

    def set(self, labels, key, value):
        """Store the value under given key in the cache indicated by labels."""
        # store
        cache = self._data.setdefault(labels, {})
        cache[key] = value

        # also annotate the labels for future cleanup
        for label in labels:
            self._labels[label].add(labels)

    def clean(self, label):
        """Clean all caches for all set of labels where this label is present."""
        for section in self._labels[label]:
            self._data[section].clear()


class GridRegistry:
    """Classify the grids Neovim sends under multigrid.

    Grid 1 is the global compositing grid (never rendered directly). One grid is the
    message grid (announced by 'msg_set_pos'); the bottom strip renders it. Every other
    grid is a window grid (an editor window).
    """

    GLOBAL_GRID = 1
    # C? Mejor GLOBAL_GRID_ID ?

    def __init__(self):
        self.message_grid = None
        self.windows = {}  # grid_id -> Neovim window handle

    def set_message_grid(self, grid_id):
        """Record which grid is the message grid."""
        self.message_grid = grid_id

    def register_window(self, grid_id, win_handle):
        """Associate a window grid with its Neovim window handle."""
        self.windows[grid_id] = win_handle

    def forget(self, grid_id):
        """Drop a grid that was destroyed."""
        self.windows.pop(grid_id, None)
        if grid_id == self.message_grid:
            self.message_grid = None

    def kind_of(self, grid_id):
        """Return the role of a grid: 'global', 'message', or 'window'."""
        if grid_id == self.GLOBAL_GRID:
            return "global"
        # C? no está bueno tener estos strings "raros", qué te parece algo como GridRegistry.GRID_GLOBAL y GridRegistry.GRID_MESSAGE (que tienen el valor de la cadena, pero en el resto del código se usan esos atributos)
        if grid_id == self.message_grid:
            return "message"
        return "window"


class NvimNotifications:
    """Dance at the rhythm of Neovim.

    Hold the relevant structures and handle all notifications. Some are sent to the TextDisplay
    widgets, other to the main waindow.
    """

    def __init__(self, main_window):
        self.main_window = main_window
        self.text_display = None  # editor (window grid) display; set before first usage
        self.message_display = None  # bottom strip (message grid) display; set before use
        self.options = {}

        # this two currently work "in tandem", we may want to unify them under the same structure
        # in the future
        self.structs = {}
        self.dyncache = DynamicCache()

        # multigrid bookkeeping
        self.grids = GridRegistry()
        self._global_size = (80, 24)  # size of the global grid (grid 1)
        # C? de dónde sale este hardcodeo de 80 y 24?
        self._grid_sizes = {}  # grid_id -> (width, height) as last reported by grid_resize
        self._msg_row = None  # top row of the message grid within the global grid

    def _display_for(self, grid_id):
        """Return the display that renders the given grid, or None if not rendered."""
        kind = self.grids.kind_of(grid_id)
        if kind == "global":
            return None
        if kind == "message":
            return self.message_display
        return self.text_display

    def _resize_message_display(self):
        """Size the message strip: full width, height = rows below the msg_set_pos row."""
        if self.message_display is None or self.grids.message_grid is None:
            return
        if self._msg_row is None:
            return
        width, _ = self._grid_sizes.get(self.grids.message_grid, self._global_size)
        _, global_rows = self._global_size
        visible = max(1, global_rows - self._msg_row)
        self.message_display.resize_view((width, visible))
        self.message_display.updateGeometry()

    def _h__redraw(self, *parameters: tuple[Any]):
        """Handle the 'redraw' notification."""
        for submethod, *args in parameters:
            n_name = "_n_redraw__" + submethod
            n_meth = getattr(self, n_name, None)
            if n_meth is None:
                logger.error(
                    "[NvimNotifications] Submethod {!r} not implemented in 'redraw', params: {}",
                    submethod, args
                )
            else:
                try:
                    # FIXME.94 allow logging system to use `logger.trace`
                    logger.log(
                        logging.TRACE,
                        "[NvimNotifications] Handle 'redraw': {} - {}", submethod, args
                    )
                    n_meth(*args)
                except Exception:
                    logger.exception("Crash when calling {!r} with {!r}", n_name, args)

    def _h__modified_changed(self, is_modified: bool):
        """Handle the notification when the buffer starts/stop having changes."""
        self.main_window.set_buffer_state(is_modified=is_modified)

    def _h__filepath_changed(self, filepath: str):
        """Handle the notification when the buffer has a file associated or not."""
        self.main_window.set_buffer_state(filepath=filepath)

    def handler(self, method: str, parameters: list[Any]):
        """Handle all notifications from Neovim."""
        h_name = "_h__" + method
        h_meth = getattr(self, h_name, None)
        if h_meth is None:
            logger.error(
                "[NvimNotifications] Method {!r} not implemented, params: {}", method, parameters)
            return

        h_meth(*parameters)

    # -- specific notification handlers

    def _n_redraw__default_colors_set(self, colors):
        """Set the default colors."""
        rgb_fg, rgb_bg, rgb_sp, _, _ = colors  # last two are ignored because are for terminals
        self.structs.setdefault("default_colors", {}).update({
            "foreground": rgb_fg,
            "background": rgb_bg,
            "special": rgb_sp,
        })
        self.dyncache.clean("default_colors")

    def _n_redraw__flush(self, _):
        """Flush all changes to the grids."""
        self.text_display.flush()
        if self.message_display is not None:
            self.message_display.flush()

    def _n_redraw__grid_clear(self, args):
        """Clear the grid."""
        (grid_id,) = args
        display = self._display_for(grid_id)
        if display is not None:
            display.clear()

    def _n_redraw__grid_cursor_goto(self, args):
        """Move the cursor in the grid."""
        grid_id, row, col = args
        display = self._display_for(grid_id)
        if display is not None:
            display.set_cursor(row, col)

    def _n_redraw__grid_line(self, *args):
        """Expose a line in the grid."""
        for item in args:
            grid, row, col_start, cells, wrap = item
            display = self._display_for(grid)
            if display is not None:
                # note we ignore "wrap", couldn't find proper utility for it
                display.write_grid(row, col_start, cells)

    def _n_redraw__grid_resize(self, args):
        """Resize a grid."""
        grid_id, width, height = args
        self._grid_sizes[grid_id] = (width, height)
        if grid_id == GridRegistry.GLOBAL_GRID:
            self._global_size = (width, height)
            return
        if grid_id == self.grids.message_grid:
            # the strip height is driven by msg_set_pos, not by the grid's own height
            self._resize_message_display()
            return
        display = self._display_for(grid_id)
        if display is not None:
            display.resize_view((width, height))

    def _n_redraw__grid_scroll(self, args):
        """Scroll a grid."""
        grid_id, top, bottom, left, right, rows, cols = args
        display = self._display_for(grid_id)
        if display is not None:
            display.scroll((top, bottom, rows), (left, right, cols))

    def _n_redraw__grid_destroy(self, args):
        """Drop a grid that Neovim destroyed (its window was closed)."""
        (grid_id,) = args
        self.grids.forget(grid_id)

    def _n_redraw__win_pos(self, args):
        """Associate a window grid with its Neovim window handle and geometry."""
        grid_id, win, _startrow, _startcol, _width, _height = args
        self.grids.register_window(grid_id, win)

    def _n_redraw__win_hide(self, args):
        """Handle a window no longer shown (e.g. it belongs to an inactive tabpage)."""
        # FIXME.90: relevant for multi-tab; for now nothing to do with a single window

    def _n_redraw__win_close(self, args):
        """Handle a closed window; the paired grid_destroy cleans the registry."""

    def _n_redraw__win_viewport_margins(self, args):
        """Window internal margins (winbar, borders). Accepted and ignored for now."""
        # FIXME.90: use it in adjust_viewport when a winbar/border is present

    def _n_redraw__msg_set_pos(self, args):
        """Define which grid is the message grid and where it starts."""
        grid_id, row = args[0], args[1]
        self.grids.set_message_grid(grid_id)
        self._msg_row = row
        self._resize_message_display()

    def _n_redraw__hl_attr_define(self, *args):
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
        self.dyncache.clean("hl-attrs")

    def _n_redraw__hl_group_set(self, *args):
        """Set highlight groups.

        E.g.: [['SpecialKey', 161], ['EndOfBuffer', 161], ...]
        """
        hl_groups = self.structs.setdefault("hl-groups", {})
        for group_name, hl_id in args:
            hl_groups[group_name] = hl_id
        self.dyncache.clean("hl-groups")

    def _n_redraw__mode_change(self, args):
        """Information about cursor mode."""
        mode, mode_idx = args
        # we ignore the mode idx as we stored in the modes in a dict using the name
        mode_info = self.structs["mode-info"][mode]
        self.text_display.change_mode(mode_info)

    def _n_redraw__mode_info_set(self, args):
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
        self.dyncache.clean("mode-info")

    def _n_redraw__mouse_on(self, args):
        """Properly ignored."""

    def _n_redraw__mouse_off(self, args):
        """Properly ignored."""

    def _n_redraw__option_set(self, *options):
        options = dict(options)
        logger.debug("[NvimNotifications] options set: {}", options)
        self.options.update(options)

        # react to some of those options
        if "guifont" in options:
            name, size = options["guifont"].split(":")
            assert size[0] == "h"
            size = float(size[1:])
            self.text_display.set_font(name, size)
            if self.message_display is not None:
                self.message_display.set_font(name, size)

    def _n_redraw__set_icon(self, param):
        """Set the icon, if any."""
        (icon,) = param
        if icon:
            logger.warning("[NvimNotifications] need to implement set icon with {!r}", icon)

    def _n_redraw__set_title(self, param):
        """Set title."""
        (title,) = param
        self.main_window.setWindowTitle(title)

    def _n_redraw__win_viewport(self, args):
        """Information for the GUI viewport."""
        grid, objinfo, topline, botline, curline, curcol, line_count, scroll_delta = args
        # FIXME.90: ignore grid and objinfo so far, need to revisit this when multiwindow

        # Note: can't find use to scroll_delta (maybe for smooth scrollbar?)
        call_async(self.main_window.adjust_viewport, topline, botline, line_count, curcol)
