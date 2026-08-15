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


class GridEntry:
    """One row of the GridRegistry: everything nysor knows about a single window grid.

    It ties together, for one grid, the nouns that already exist elsewhere -- the Neovim window
    id, the buffer, the Qt pane and the filepath. It is not a new domain concept, just the
    association among them. The Qt pane is held as an opaque reference (the registry never touches
    Qt).
    """

    def __init__(self, grid_id: int, pane) -> None:
        self.grid_id = grid_id
        self.pane = pane
        self.win_id: int | None = None
        self.bufnr: int | None = None
        self.filepath: str | None = None


class GridRegistry:
    """Single source of truth relating Neovim grids/windows/buffers to Qt panes.

    Grid 1 is the global compositing grid (never rendered directly). One grid is the message grid
    (announced by 'msg_set_pos'); the bottom strip renders it. Every other grid is a window grid,
    tracked as a GridEntry, with reverse indexes so lookups by window/buffer/pane are O(1).
    The tab's position is intentionally NOT stored here: the QTabWidget owns tab order (so dragging
    tabs to reorder just works), and the current index is asked for on demand.
    """

    GLOBAL_GRID_ID = 1

    # grid roles, returned by get_kind_by_grid() and used across the code as GridRegistry.GRID_*
    GRID_GLOBAL = "global"
    GRID_MESSAGE = "message"
    GRID_WINDOW = "window"

    def __init__(self) -> None:
        self.message_grid: int | None = None
        self._by_grid: dict[int, GridEntry] = {}  # grid_id -> entry
        self._by_win: dict[int, int] = {}         # Neovim window id -> grid_id
        self._by_buf: dict[int, int] = {}         # Neovim buffer id -> owning grid_id
        self._by_pane: dict[object, int] = {}     # Qt pane -> grid_id

    def get_kind_by_grid(self, grid_id: int) -> str:
        """Return the role of a grid: GRID_GLOBAL, GRID_MESSAGE, or GRID_WINDOW."""
        if grid_id == self.GLOBAL_GRID_ID:
            return self.GRID_GLOBAL
        if grid_id == self.message_grid:
            return self.GRID_MESSAGE
        return self.GRID_WINDOW

    def set_message_grid(self, grid_id: int) -> None:
        """Record which grid is the message grid."""
        self.message_grid = grid_id

    # -- entry mutation

    def add_grid(self, grid_id: int, pane) -> GridEntry:
        """Start tracking a window grid backed by the given Qt pane; return its entry."""
        entry = GridEntry(grid_id, pane)
        self._by_grid[grid_id] = entry
        self._by_pane[pane] = grid_id
        return entry

    def has_grid(self, grid_id: int) -> bool:
        """Whether a window grid entry exists for this grid."""
        return grid_id in self._by_grid

    def set_win(self, grid_id: int, win_id: int) -> None:
        """Set (or update) the Neovim window id of a window grid."""
        entry = self._by_grid.get(grid_id)
        if entry is None:
            return
        if entry.win_id is not None:
            self._by_win.pop(entry.win_id, None)
        entry.win_id = win_id
        self._by_win[win_id] = grid_id

    def set_buffer(self, grid_id: int, bufnr: int, filepath: str) -> None:
        """Set (or update) the buffer and filepath a window grid shows."""
        entry = self._by_grid.get(grid_id)
        if entry is None:
            return
        if entry.bufnr is not None and self._by_buf.get(entry.bufnr) == grid_id:
            self._by_buf.pop(entry.bufnr, None)
        entry.bufnr = bufnr
        entry.filepath = filepath
        # claim the buffer only if free, so the index keeps pointing at the first (owning) grid
        # even while a duplicate is transiently being collapsed
        self._by_buf.setdefault(bufnr, grid_id)

    def forget_grid(self, grid_id: int) -> None:
        """Drop a grid that was destroyed."""
        if grid_id == self.message_grid:
            self.message_grid = None
        entry = self._by_grid.pop(grid_id, None)
        if entry is None:
            return
        if entry.win_id is not None:
            self._by_win.pop(entry.win_id, None)
        if entry.bufnr is not None and self._by_buf.get(entry.bufnr) == grid_id:
            self._by_buf.pop(entry.bufnr, None)
        self._by_pane.pop(entry.pane, None)

    # -- queries

    def get_entry_by_grid(self, grid_id: int) -> GridEntry | None:
        """Return the entry of a window grid, or None."""
        return self._by_grid.get(grid_id)

    def get_pane_by_grid(self, grid_id: int):
        """Return the Qt pane of a window grid, or None."""
        entry = self._by_grid.get(grid_id)
        return entry.pane if entry is not None else None

    def get_grid_by_win(self, win_id: int) -> int | None:
        """Return the grid showing the given Neovim window id, or None."""
        return self._by_win.get(win_id)

    def get_grid_by_buffer(self, bufnr: int) -> int | None:
        """Return the grid that owns the given Neovim buffer, or None."""
        return self._by_buf.get(bufnr)

    def get_grid_by_pane(self, pane) -> int | None:
        """Return the grid backed by the given Qt pane, or None."""
        return self._by_pane.get(pane)

    def get_all_entries(self) -> list:
        """Return all window grid entries."""
        return list(self._by_grid.values())

    def has_path(self, filepath: str) -> bool:
        """Whether some window grid currently shows the given filepath."""
        return any(entry.filepath == filepath for entry in self._by_grid.values())


class NvimNotifications:
    """Dance at the rhythm of Neovim.

    Hold the relevant structures and handle all notifications. Some are sent to the TextDisplay
    widgets, other to the main waindow.
    """

    def __init__(self, main_window):
        self.main_window = main_window
        self.message_display = None  # bottom strip (message grid) display; set before use
        self.statusline_display = None  # strip rendering the status row(s) of the global grid
        self.options = {}

        # this two currently work "in tandem", we may want to unify them under the same structure
        # in the future
        self.structs = {}
        self.dyncache = DynamicCache()

        # multigrid bookkeeping
        self.grids = GridRegistry()
        self._grid_sizes = {}  # grid_id -> (width, height) as last reported by grid_resize
        self._msg_row = None  # top row of the message grid within the global grid
        # buffer info that arrived before its window grid was known (win_id -> (bufnr, filepath))
        self._pending_buffers = {}

    def _display_for(self, grid_id):
        """Return the display that renders the given grid, or None if not rendered."""
        kind = self.grids.get_kind_by_grid(grid_id)
        if kind == GridRegistry.GRID_GLOBAL:
            # under multigrid the global grid is empty except for the statusline row(s),
            # which the statusline strip renders (using a view origin, see below)
            return self.statusline_display
        if kind == GridRegistry.GRID_MESSAGE:
            return self.message_display
        return self._ensure_editor(grid_id)

    def _ensure_editor(self, grid_id):
        """Return the editor display for a window grid, creating its tab the first time.

        The editor layer (main_window) owns font/cursor-mode and applies them to the freshly
        built display; here we only apply what is genuinely notification data: the grid size.
        """
        entry = self.grids.get_entry_by_grid(grid_id)
        if entry is None:
            display = self.main_window.build_editor_tab()
            entry = self.grids.add_grid(grid_id, display.pane)
            size = self._grid_sizes.get(grid_id)
            if size is not None:
                display.resize_view(size)
        return entry.pane.text_display

    def _layout_message_strip(self):
        """Lay out the message strip from the message grid's own size and position.

        Width is exactly the message grid's width (as Neovim reported it). The message grid is
        full height but only its bottom part is on screen; that visible height is the grid's
        height minus the row where 'msg_set_pos' placed it.
        """
        grid = self.grids.message_grid
        if self.message_display is None or grid is None:
            return
        size = self._grid_sizes.get(grid)
        if size is None or self._msg_row is None:
            return
        width, height = size
        visible_rows = max(1, height - self._msg_row)
        self.message_display.resize_view((width, visible_rows))
        self.message_display.updateGeometry()

    def _layout_statusline_strip(self):
        """Lay out the statusline strip: the single global-grid row just above the messages.

        Width is exactly the global grid's width (as Neovim reported it). The statusline sits one
        row above the message area, so its row is `msg_row - 1` (independent of window geometry;
        this is set early, from msg_set_pos/grid_resize, before the content arrives).
        """
        if self.statusline_display is None or self._msg_row is None:
            return
        size = self._grid_sizes.get(GridRegistry.GLOBAL_GRID_ID)
        if size is None:
            return
        width, _ = size
        self.statusline_display.view_origin_row = max(0, self._msg_row - 1)
        self.statusline_display.resize_view((width, 1))
        self.statusline_display.updateGeometry()

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

    def _h__modified_changed(self, win_id: int, is_modified: bool):
        """Handle the notification when a window's buffer starts/stops having changes."""
        grid = self.grids.get_grid_by_win(win_id)
        # grid may legitimately be None when the change arrives for a window we don't know yet
        if grid is not None:
            self.main_window.set_tab_modified(grid, is_modified)

    def _h__window_buffer(self, win_id: int, bufnr: int, filepath: str):
        """Handle the notification about which buffer a window shows.

        Records the buffer/filepath and updates the tab. If the window's grid is not known yet
        (win_pos not received), we stash it and apply it when win_pos arrives.
        """
        grid = self.grids.get_grid_by_win(win_id)
        if grid is not None:
            self.grids.set_buffer(grid, bufnr, filepath)
            self.main_window.refresh_tab(grid)
        else:
            self._pending_buffers[win_id] = (bufnr, filepath)

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
        for entry in self.grids.get_all_entries():
            entry.pane.text_display.flush()
        if self.message_display is not None:
            self.message_display.flush()
        if self.statusline_display is not None:
            self.statusline_display.flush()

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
        """Resize a grid: render each grid's display at exactly the size Neovim reports."""
        grid_id, width, height = args
        self._grid_sizes[grid_id] = (width, height)
        kind = self.grids.get_kind_by_grid(grid_id)
        if kind == GridRegistry.GRID_WINDOW:
            # a not-yet-created window keeps its size in _grid_sizes; _ensure_editor applies it
            entry = self.grids.get_entry_by_grid(grid_id)
            if entry is not None:
                entry.pane.text_display.resize_view((width, height))
        elif kind == GridRegistry.GRID_MESSAGE:
            self._layout_message_strip()
        elif kind == GridRegistry.GRID_GLOBAL:
            self._layout_statusline_strip()

    def _n_redraw__grid_scroll(self, args):
        """Scroll a grid."""
        grid_id, top, bottom, left, right, rows, cols = args
        display = self._display_for(grid_id)
        if display is not None:
            display.scroll((top, bottom, rows), (left, right, cols))

    def _n_redraw__chdir(self, *args):
        """Neovim changed its working directory. Nothing to render; ignored for now."""

    def _n_redraw__grid_destroy(self, *args):
        """Drop grids that Neovim destroyed (their windows were closed)."""
        for (grid_id,) in args:
            entry = self.grids.get_entry_by_grid(grid_id)
            self.grids.forget_grid(grid_id)
            if entry is not None:
                # let the GUI decide what to do with that window's buffer (close it, or, if it
                # has unsaved changes, re-show it and re-attach this tab)
                self.main_window.on_editor_window_closed(entry)

    def _n_redraw__win_pos(self, *args):
        """Associate window grids with their Neovim window handles and geometry."""
        for grid_id, win_handle, _startrow, _startcol, _width, _height in args:

            # normalize a window handle (decoded as ['Window', id]) to its numeric id
            win_type, win_id = win_handle
            assert win_type == "Window"

            # ensure the window has its editor tab, record its Neovim window id, and make it the
            # active one (this is how opening a file in a new tabpage switches the GUI to it)
            self._ensure_editor(grid_id)
            self.grids.set_win(grid_id, win_id)
            self.main_window.set_active_editor(grid_id)

            # if buffer info arrived before this window was known, apply it now
            if win_id in self._pending_buffers:
                bufnr, filepath = self._pending_buffers.pop(win_id)
                self.grids.set_buffer(grid_id, bufnr, filepath)
                self.main_window.refresh_tab(grid_id)
        self._layout_statusline_strip()

    def _n_redraw__win_hide(self, *args):
        """Handle windows no longer shown (e.g. belonging to an inactive tabpage)."""
        # FIXME.90: relevant for multi-tab; for now nothing to do with a single window

    def _n_redraw__win_close(self, *args):
        """Handle closed windows; the paired grid_destroy cleans the registry."""

    def _n_redraw__win_viewport_margins(self, *args):
        """Window internal margins (winbar, borders). Accepted and ignored for now."""
        # FIXME.90: use it in adjust_viewport when a winbar/border is present

    def _n_redraw__msg_set_pos(self, *args):
        """Define which grid is the message grid and where it starts."""
        for batch in args:
            grid_id, row = batch[0], batch[1]
            self.grids.set_message_grid(grid_id)
            self._msg_row = row
        self._layout_message_strip()
        # the message row is also the lower bound of the statusline region
        self._layout_statusline_strip()

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
        """Information about cursor mode; the editor layer owns and applies it."""
        mode, mode_idx = args
        # we ignore the mode idx as we stored in the modes in a dict using the name
        mode_info = self.structs["mode-info"][mode]
        self.main_window.set_editor_mode(mode_info)

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

        # react to some of those options; the editor layer owns and applies the font
        if "guifont" in options:
            name, size = options["guifont"].split(":")
            assert size[0] == "h"
            size = float(size[1:])
            self.main_window.set_editor_font(name, size)

    def _n_redraw__set_icon(self, param):
        """Set the icon, if any."""
        (icon,) = param
        if icon:
            logger.warning("[NvimNotifications] need to implement set icon with {!r}", icon)

    def _n_redraw__set_title(self, param):
        """Ignore Neovim's global title (the editor layer titles each GUI window itself).

        Neovim's title reflects the CURRENT window (which may be a detached one), but each GUI
        window's title must follow its own shown tab -- see MainApp._refresh_main_title and
        _refresh_tab_label.
        """

    def _n_redraw__win_viewport(self, args):
        """Information for the GUI viewport; routed to the pane that owns the window grid."""
        grid, _win, topline, botline, curline, curcol, line_count, scroll_delta = args
        # Note: can't find use to scroll_delta (maybe for smooth scrollbar?)
        pane = self.grids.get_pane_by_grid(grid)
        if pane is not None:
            call_async(pane.adjust_viewport, topline, botline, line_count, curcol)
