# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Tests for nysor/nvim_notifications.py."""

from unittest.mock import MagicMock

import pytest

from nysor import nvim_notifications
from nysor.nvim_notifications import DynamicCache, GridRegistry, NvimNotifications


@pytest.fixture
def notif(mocker):
    """NvimNotifications with a mocked main_window, strips, and call_async.

    Grid 2 is pre-registered as a window whose pane's display is `notif._editor`.
    """
    mocker.patch("nysor.nvim_notifications.call_async")
    nn = NvimNotifications(main_window=MagicMock())
    nn._editor = MagicMock()          # the TextDisplay of grid 2's tab
    nn._pane = MagicMock()            # the EditorPane of grid 2's tab
    nn._pane.text_display = nn._editor
    nn.grids.add_grid(2, nn._pane)  # grid 2 -> pane -> display
    nn.message_display = MagicMock()
    nn.statusline_display = MagicMock()
    return nn


class TestDynamicCache:

    def test_get_empty_returns_none(self):
        """Getting a key from an empty cache returns None."""
        cache = DynamicCache()
        assert cache.get(("a",), "key") is None

    def test_set_then_get_returns_value(self):
        """Stored value can be retrieved with the same labels and key."""
        cache = DynamicCache()
        cache.set(("a",), "key", "value")
        assert cache.get(("a",), "key") == "value"

    def test_different_labels_are_independent(self):
        """Caches under different label sets do not share values."""
        cache = DynamicCache()
        cache.set(("a",), "key", "value")
        assert cache.get(("b",), "key") is None

    def test_clean_clears_matching_cache(self):
        """Cleaning a label removes all entries stored under it."""
        cache = DynamicCache()
        cache.set(("a",), "key", "value")
        cache.clean("a")
        assert cache.get(("a",), "key") is None

    def test_clean_shared_label_clears_all_sets(self):
        """Cleaning a label clears every label-set that contains it."""
        cache = DynamicCache()
        cache.set(("a", "b"), "key1", "value1")
        cache.set(("a", "c"), "key2", "value2")
        cache.clean("a")
        assert cache.get(("a", "b"), "key1") is None
        assert cache.get(("a", "c"), "key2") is None

    def test_clean_unknown_label_does_nothing(self):
        """Cleaning an unknown label does not raise and leaves other data intact."""
        cache = DynamicCache()
        cache.set(("a",), "key", "value")
        cache.clean("unknown")
        assert cache.get(("a",), "key") == "value"

    def test_clean_does_not_affect_other_label_sets(self):
        """Cleaning one label does not disturb caches under unrelated labels."""
        cache = DynamicCache()
        cache.set(("a",), "key1", "value1")
        cache.set(("b",), "key2", "value2")
        cache.clean("a")
        assert cache.get(("b",), "key2") == "value2"


class TestNvimNotificationsHandler:

    def test_known_method_is_dispatched(self, notif):
        """handler() calls the matching _h__* method with the notification params."""
        notif.grids.set_win(2, 5)  # grid 2 (window id 5) already has notif._pane
        notif.handler("modified_changed", [5, True])
        notif.main_window.set_tab_modified.assert_called_once_with(2, True)

    def test_unknown_method_logs_error(self, notif, logs):
        """handler() logs an error for unknown methods and does not raise."""
        notif.handler("unknown_method", [])
        assert "not implemented" in logs.error


class TestNvimNotificationsRedraw:

    def test_known_submethod_is_dispatched(self, notif):
        """_h__redraw() calls the matching _n_redraw__* method."""
        notif._h__redraw(["option_set", ["guifont", "Monospace:h14"]])
        notif.main_window.set_editor_font.assert_called_once_with("Monospace", 14.0)

    def test_unknown_submethod_logs_error(self, notif, logs):
        """_h__redraw() logs an error for unknown submethods and does not raise."""
        notif._h__redraw(["unknown_submethod", []])
        assert "not implemented" in logs.error

    def test_exception_is_caught_and_execution_continues(self, notif, logs):
        """Exception in one submethod is logged; remaining submethods still run."""
        notif._editor.flush.side_effect = RuntimeError("boom")
        notif._h__redraw(["flush", None], ["option_set", ["guifont", "Mono:h10"]])
        notif.main_window.set_editor_font.assert_called_once_with("Mono", 10.0)
        assert "Crash" in logs.error

    def test_multiple_submethods_all_dispatched(self, notif):
        """All submethods in one _h__redraw() call are dispatched in order."""
        notif._h__redraw(["flush", None], ["flush", None])
        assert notif._editor.flush.call_count == 2


class TestNvimNotificationsHandlers:

    def test_modified_changed(self, notif):
        """A modified change for a known window marks that window's tab."""
        notif.grids.set_win(2, 5)  # grid 2 (window id 5) already has notif._pane
        notif._h__modified_changed(5, True)
        notif.main_window.set_tab_modified.assert_called_once_with(2, True)

    def test_window_buffer_known_window(self, notif):
        """Buffer info for a known window records the buffer and refreshes the tab."""
        notif.grids.set_win(2, 5)  # grid 2 (window id 5) already has notif._pane
        notif._h__window_buffer(5, 7, "/some/path")
        assert notif.grids.get_grid_by_buffer(7) == 2
        assert notif.grids.get_entry_by_grid(2).filepath == "/some/path"
        notif.main_window.refresh_tab.assert_called_once_with(2)

    def test_window_buffer_pending_for_unknown_window(self, notif):
        """Buffer info for a not-yet-known window is stashed until its win_pos arrives."""
        notif._h__window_buffer(99, 7, "/some/path")
        notif.main_window.refresh_tab.assert_not_called()
        assert notif._pending_buffers[99] == (7, "/some/path")


class TestNvimNotificationsRedrawHandlers:

    def test_default_colors_set(self, notif, mocker):
        """Updates default_colors struct and cleans the cache."""
        mock_clean = mocker.patch.object(notif.dyncache, "clean")
        notif._n_redraw__default_colors_set([100, 200, 300, 0, 0])
        assert notif.structs["default_colors"] == {
            "foreground": 100, "background": 200, "special": 300}
        mock_clean.assert_called_once_with("default_colors")

    def test_flush(self, notif):
        """Calls text_display.flush()."""
        notif._n_redraw__flush(None)
        notif._editor.flush.assert_called_once()

    def test_grid_clear(self, notif):
        """A window grid routes clear() to the editor display."""
        notif._n_redraw__grid_clear([2])
        notif._editor.clear.assert_called_once()

    def test_grid_clear_global_grid_routes_to_statusline(self, notif):
        """The global grid (1) is rendered by the statusline strip, not the editor."""
        notif._n_redraw__grid_clear([1])
        notif.statusline_display.clear.assert_called_once()
        notif._editor.clear.assert_not_called()

    def test_grid_cursor_goto(self, notif):
        """A window grid routes set_cursor(row, col) to the editor display."""
        notif._n_redraw__grid_cursor_goto([2, 5, 10])
        notif._editor.set_cursor.assert_called_once_with(5, 10)

    def test_grid_line_single(self, notif):
        """Calls text_display.write_grid for a single line item."""
        notif._n_redraw__grid_line([2, 3, 0, [["a", 1]], False])
        notif._editor.write_grid.assert_called_once_with(3, 0, [["a", 1]])

    def test_grid_line_multiple(self, notif):
        """Calls text_display.write_grid once per line item."""
        notif._n_redraw__grid_line(
            [2, 3, 0, [["a", 1]], False],
            [2, 4, 2, [["b", 1]], False],
        )
        assert notif._editor.write_grid.call_count == 2

    def test_grid_resize(self, notif):
        """A window grid routes resize_view((width, height)) to the editor display."""
        notif._n_redraw__grid_resize([2, 80, 24])
        notif._editor.resize_view.assert_called_once_with((80, 24))

    def test_grid_scroll(self, notif):
        """Calls text_display.scroll with the correct row and column arguments."""
        notif._n_redraw__grid_scroll([2, 0, 24, 0, 80, 3, 0])
        notif._editor.scroll.assert_called_once_with((0, 24, 3), (0, 80, 0))

    def test_hl_attr_define(self, notif, mocker):
        """Stores highlight attributes in structs and cleans the cache."""
        mock_clean = mocker.patch.object(notif.dyncache, "clean")
        notif._n_redraw__hl_attr_define([2, {"foreground": 100}, {}, []])
        assert notif.structs["hl-attrs"][2] == {"foreground": 100}
        mock_clean.assert_called_once_with("hl-attrs")

    def test_hl_group_set(self, notif, mocker):
        """Stores highlight group mappings in structs and cleans the cache."""
        mock_clean = mocker.patch.object(notif.dyncache, "clean")
        notif._n_redraw__hl_group_set(["SpecialKey", 161], ["EndOfBuffer", 162])
        assert notif.structs["hl-groups"]["SpecialKey"] == 161
        assert notif.structs["hl-groups"]["EndOfBuffer"] == 162
        mock_clean.assert_called_once_with("hl-groups")

    def test_mode_change(self, notif):
        """Looks up mode info in structs and hands it to the editor layer (main_window)."""
        notif.structs["mode-info"] = {"normal": {"cursor_shape": "block"}}
        notif._n_redraw__mode_change(["normal", 0])
        notif.main_window.set_editor_mode.assert_called_once_with({"cursor_shape": "block"})

    def test_mode_info_set(self, notif, mocker):
        """Populates structs['mode-info'] stripping name/short_name, and cleans cache."""
        mock_clean = mocker.patch.object(notif.dyncache, "clean")
        mode_data = [{"name": "normal", "short_name": "n", "cursor_shape": "block"}]
        notif._n_redraw__mode_info_set([True, mode_data])
        assert notif.structs["mode-info"]["normal"] == {"cursor_shape": "block"}
        mock_clean.assert_called_once_with("mode-info")

    def test_mouse_on_does_not_raise(self, notif):
        """_n_redraw__mouse_on is a no-op."""
        notif._n_redraw__mouse_on(None)

    def test_mouse_off_does_not_raise(self, notif):
        """_n_redraw__mouse_off is a no-op."""
        notif._n_redraw__mouse_off(None)

    def test_option_set_without_guifont(self, notif):
        """Updates options dict without touching the editor font."""
        notif._n_redraw__option_set(["arabicshape", True])
        assert notif.options["arabicshape"] is True
        notif.main_window.set_editor_font.assert_not_called()

    def test_option_set_with_guifont(self, notif):
        """Updates options and hands the font to the editor layer (main_window)."""
        notif._n_redraw__option_set(["guifont", "Monospace:h14"])
        notif.main_window.set_editor_font.assert_called_once_with("Monospace", 14.0)

    def test_set_icon_empty_no_warning(self, notif, logs):
        """set_icon with an empty icon does not log a warning."""
        notif._n_redraw__set_icon([""])
        assert "set icon" not in logs.warning

    def test_set_icon_nonempty_logs_warning(self, notif, logs):
        """set_icon with a non-empty icon logs a warning."""
        notif._n_redraw__set_icon(["myicon"])
        assert "set icon" in logs.warning

    def test_set_title_is_ignored(self, notif):
        """set_title is ignored: the editor layer manages each window's title itself."""
        notif._n_redraw__set_title(["My Editor"])
        notif.main_window.setWindowTitle.assert_not_called()

    def test_win_viewport(self, notif):
        """Routes adjust_viewport to the pane of the window grid, with the right arguments."""
        notif._n_redraw__win_viewport([2, {}, 10, 50, 25, 5, 100, 3])
        nvim_notifications.call_async.assert_called_once_with(
            notif._pane.adjust_viewport, 10, 50, 100, 5)


class TestGridRegistry:

    def test_global_grid_is_global(self):
        """Grid 1 is always classified as the global grid."""
        assert GridRegistry().get_kind_by_grid(1) == "global"

    def test_unknown_grid_is_a_window(self):
        """Any grid that is neither global nor the message grid is a window."""
        assert GridRegistry().get_kind_by_grid(2) == "window"

    def test_message_grid_is_classified(self):
        """A grid registered via set_message_grid is classified as the message grid."""
        reg = GridRegistry()
        reg.set_message_grid(3)
        assert reg.get_kind_by_grid(3) == "message"
        assert reg.get_kind_by_grid(2) == "window"

    def test_add_grid_creates_entry_and_indexes_pane(self):
        """add_grid creates an entry backed by the given pane and indexes it by pane."""
        reg = GridRegistry()
        pane = object()
        entry = reg.add_grid(2, pane)
        assert entry.grid_id == 2
        assert entry.pane is pane
        assert reg.get_entry_by_grid(2) is entry
        assert reg.get_pane_by_grid(2) is pane
        assert reg.get_grid_by_pane(pane) == 2
        assert reg.has_grid(2)

    def test_set_win_direct_lookup(self):
        """set_win indexes the window id so grid_of_win resolves it without iterating."""
        reg = GridRegistry()
        reg.add_grid(2, object())
        reg.add_grid(4, object())
        reg.set_win(2, 5)
        reg.set_win(4, 9)
        assert reg.get_grid_by_win(5) == 2
        assert reg.get_grid_by_win(9) == 4
        assert reg.get_grid_by_win(123) is None

    def test_set_win_replaces_previous_id(self):
        """Updating a grid's window id drops the stale reverse lookup."""
        reg = GridRegistry()
        reg.add_grid(2, object())
        reg.set_win(2, 5)
        reg.set_win(2, 8)
        assert reg.get_grid_by_win(5) is None
        assert reg.get_grid_by_win(8) == 2

    def test_set_buffer_records_buffer_and_path(self):
        """set_buffer stores bufnr/filepath and indexes the owning grid by bufnr."""
        reg = GridRegistry()
        reg.add_grid(2, object())
        reg.set_buffer(2, 7, "/some/path")
        assert reg.get_entry_by_grid(2).bufnr == 7
        assert reg.get_entry_by_grid(2).filepath == "/some/path"
        assert reg.get_grid_by_buffer(7) == 2

    def test_set_buffer_keeps_first_owner(self):
        """A second grid showing the same buffer does not steal ownership of the bufnr index."""
        reg = GridRegistry()
        reg.add_grid(2, object())
        reg.add_grid(4, object())
        reg.set_buffer(2, 7, "/p")
        reg.set_buffer(4, 7, "/p")
        assert reg.get_grid_by_buffer(7) == 2

    def test_has_path(self):
        """has_path reports whether any window grid shows the given filepath."""
        reg = GridRegistry()
        reg.add_grid(2, object())
        reg.set_buffer(2, 7, "/some/path")
        assert reg.has_path("/some/path")
        assert not reg.has_path("/other")

    def test_records_lists_all_windows(self):
        """records() returns every window grid record."""
        reg = GridRegistry()
        reg.add_grid(2, object())
        reg.add_grid(4, object())
        assert {r.grid_id for r in reg.get_all_entries()} == {2, 4}

    def test_forget_message_grid(self):
        """Forgetting the message grid reverts its classification to window."""
        reg = GridRegistry()
        reg.set_message_grid(3)
        reg.forget_grid(3)
        assert reg.get_kind_by_grid(3) == "window"
        assert reg.message_grid is None

    def test_forget_window_drops_all_lookups(self):
        """Forgetting a window grid drops its win, buffer, and pane reverse lookups."""
        reg = GridRegistry()
        pane = object()
        reg.add_grid(2, pane)
        reg.set_win(2, 5)
        reg.set_buffer(2, 7, "/p")
        reg.forget_grid(2)
        assert reg.get_entry_by_grid(2) is None
        assert reg.get_grid_by_win(5) is None
        assert reg.get_grid_by_buffer(7) is None
        assert reg.get_grid_by_pane(pane) is None
