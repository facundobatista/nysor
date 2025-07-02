# Copyright 2025 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Receive, process, and manage all notifications from Neovim."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class NvimNotifications:
    """Dance at the rythm of Neovim.

    Hold the relevant structures and handle all notifications. Some are sent to the TextDisplay
    widgets, other to the main waindow.
    """
    def __init__(self, main_window):
        self.main_window = main_window
        self.text_display = None  # will be set before first usage
        self.structs = {}
        self.options = {}

    def handler(self, method: str, parameters: list[Any]):
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

    # -- specific notification handlers

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
        self.text_display.flush()

    def _n__grid_clear(self, args):
        """Clear the grid."""
        (grid_id,) = args
        assert grid_id == 1  # FIXME.90: is it always 1? when do we have more than one?
        self.text_display.clear()

    def _n__grid_cursor_goto(self, args):
        """Resize a grid."""
        grid_id, row, col = args
        assert grid_id == 1  # FIXME.90: is it always 1? when do we have more than one?
        self.text_display.set_cursor(row, col)

    def _n__grid_line(self, *args):
        """Expose a line in the grid."""
        for item in args:
            grid, row, col_start, cells, wrap = item
            assert grid == 1  # FIXME.90: same question we do in grid_resize

            # note we ignore "wrap", couldn't find proper utility for it
            self.text_display.write_grid(row, col_start, cells)

    def _n__grid_resize(self, args):
        """Resize a grid."""
        grid_id, width, height = args
        assert grid_id == 1  # FIXME.90: is it always 1? when do we have more than one?
        self.text_display.resize_view((width, height))

    def _n__grid_scroll(self, args):
        """Scroll a grid."""
        grid_id, top, bottom, left, right, rows, cols = args
        assert grid_id == 1  # FIXME.90: is it always 1? when do we have more than one?
        self.text_display.scroll((top, bottom, rows), (left, right, cols))

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
        self.text_display.change_mode(mode_info)

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
            self.text_display.set_font(name, size)

    def _n__set_icon(self, param):
        """Set the icon, if any."""
        (icon,) = param
        if icon:
            logger.warning("[NvimManager] need to implement set icon with %r", icon)

    def _n__set_title(self, param):
        """Set title."""
        (title,) = param
        self.main_window.setWindowTitle(title)

    def _n__win_viewport(self, args):
        """Information for the GUI viewport."""
        grid, objinfo, topline, botline, curline, curcol, line_count, scroll_delta = args
        # FIXME.90: ignore grid and objinfo so far, need to revisit this when multiwindow

        # Note: can't find use to scroll_delta (maybe for smooth scroolbar?)
        self.main_window.adjust_viewport(topline, botline, line_count, curcol)
