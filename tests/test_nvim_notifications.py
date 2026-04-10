# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Tests for nysor/nvim_notifications.py."""

from unittest.mock import MagicMock

import pytest

from nysor import nvim_notifications
from nysor.nvim_notifications import DynamicCache, NvimNotifications


@pytest.fixture
def notif(mocker):
    """NvimNotifications with mocked main_window, text_display, and call_async."""
    mocker.patch("nysor.nvim_notifications.call_async")
    nn = NvimNotifications(main_window=MagicMock())
    nn.text_display = MagicMock()
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
        """handler() calls the matching _h__* method."""
        notif.handler("modified_changed", [True])
        notif.main_window.set_buffer_state.assert_called_once_with(is_modified=True)

    def test_unknown_method_logs_error(self, notif, logs):
        """handler() logs an error for unknown methods and does not raise."""
        notif.handler("unknown_method", [])
        assert "not implemented" in logs.error


class TestNvimNotificationsRedraw:

    def test_known_submethod_is_dispatched(self, notif):
        """_h__redraw() calls the matching _n_redraw__* method."""
        notif._h__redraw(["set_title", ["My Title"]])
        notif.main_window.setWindowTitle.assert_called_once_with("My Title")

    def test_unknown_submethod_logs_error(self, notif, logs):
        """_h__redraw() logs an error for unknown submethods and does not raise."""
        notif._h__redraw(["unknown_submethod", []])
        assert "not implemented" in logs.error

    def test_exception_is_caught_and_execution_continues(self, notif, logs):
        """Exception in one submethod is logged; remaining submethods still run."""
        notif.text_display.flush.side_effect = RuntimeError("boom")
        notif._h__redraw(["flush", None], ["set_title", ["Title"]])
        notif.main_window.setWindowTitle.assert_called_once_with("Title")
        assert "Crash" in logs.error

    def test_multiple_submethods_all_dispatched(self, notif):
        """All submethods in one _h__redraw() call are dispatched in order."""
        notif._h__redraw(["flush", None], ["flush", None])
        assert notif.text_display.flush.call_count == 2


class TestNvimNotificationsHandlers:

    def test_modified_changed(self, notif):
        """Calls main_window.set_buffer_state with is_modified."""
        notif._h__modified_changed(True)
        notif.main_window.set_buffer_state.assert_called_once_with(is_modified=True)

    def test_filepath_changed(self, notif):
        """Calls main_window.set_buffer_state with filepath."""
        notif._h__filepath_changed("/some/path")
        notif.main_window.set_buffer_state.assert_called_once_with(filepath="/some/path")


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
        notif.text_display.flush.assert_called_once()

    def test_grid_clear(self, notif):
        """Calls text_display.clear()."""
        notif._n_redraw__grid_clear([1])
        notif.text_display.clear.assert_called_once()

    def test_grid_cursor_goto(self, notif):
        """Calls text_display.set_cursor(row, col)."""
        notif._n_redraw__grid_cursor_goto([1, 5, 10])
        notif.text_display.set_cursor.assert_called_once_with(5, 10)

    def test_grid_line_single(self, notif):
        """Calls text_display.write_grid for a single line item."""
        notif._n_redraw__grid_line([1, 3, 0, [["a", 1]], False])
        notif.text_display.write_grid.assert_called_once_with(3, 0, [["a", 1]])

    def test_grid_line_multiple(self, notif):
        """Calls text_display.write_grid once per line item."""
        notif._n_redraw__grid_line(
            [1, 3, 0, [["a", 1]], False],
            [1, 4, 2, [["b", 1]], False],
        )
        assert notif.text_display.write_grid.call_count == 2

    def test_grid_resize(self, notif):
        """Calls text_display.resize_view with (width, height)."""
        notif._n_redraw__grid_resize([1, 80, 24])
        notif.text_display.resize_view.assert_called_once_with((80, 24))

    def test_grid_scroll(self, notif):
        """Calls text_display.scroll with the correct row and column arguments."""
        notif._n_redraw__grid_scroll([1, 0, 24, 0, 80, 3, 0])
        notif.text_display.scroll.assert_called_once_with((0, 24, 3), (0, 80, 0))

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
        """Looks up mode info in structs and calls text_display.change_mode."""
        notif.structs["mode-info"] = {"normal": {"cursor_shape": "block"}}
        notif._n_redraw__mode_change(["normal", 0])
        notif.text_display.change_mode.assert_called_once_with({"cursor_shape": "block"})

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
        """Updates options dict without calling text_display.set_font."""
        notif._n_redraw__option_set(["arabicshape", True])
        assert notif.options["arabicshape"] is True
        notif.text_display.set_font.assert_not_called()

    def test_option_set_with_guifont(self, notif):
        """Updates options and calls text_display.set_font(name, size)."""
        notif._n_redraw__option_set(["guifont", "Monospace:h14"])
        notif.text_display.set_font.assert_called_once_with("Monospace", 14.0)

    def test_set_icon_empty_no_warning(self, notif, logs):
        """set_icon with an empty icon does not log a warning."""
        notif._n_redraw__set_icon([""])
        assert "set icon" not in logs.warning

    def test_set_icon_nonempty_logs_warning(self, notif, logs):
        """set_icon with a non-empty icon logs a warning."""
        notif._n_redraw__set_icon(["myicon"])
        assert "set icon" in logs.warning

    def test_set_title(self, notif):
        """Calls main_window.setWindowTitle with the given title."""
        notif._n_redraw__set_title(["My Editor"])
        notif.main_window.setWindowTitle.assert_called_once_with("My Editor")

    def test_win_viewport(self, notif):
        """Calls call_async with adjust_viewport and the correct arguments."""
        notif._n_redraw__win_viewport([1, {}, 10, 50, 25, 5, 100, 3])
        nvim_notifications.call_async.assert_called_once_with(
            notif.main_window.adjust_viewport, 10, 50, 100, 5)
