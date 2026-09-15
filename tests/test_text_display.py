# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Tests for text_display.py's font-zoom feature (Ctrl +/-/0).

Real QWidgets (TextDisplay is one) need a real QApplication or Qt aborts the process -- see the
'qapp' fixture in tests/conftest.py. main_window is always a plain mock: none of the code under
test here ever needs it to be a real MainApp.
"""

from nysor.logical_lines import LogicalChar
from nysor.text_display import (
    AltModifier,
    ControlModifier,
    FONT_SIZE_MAX,
    FONT_SIZE_MIN,
    ShiftModifier,
    Qt,
    TextDisplay,
)


class TestFontZoom:
    """Ctrl +/- (zoom_font) changes only this display's font size."""

    def test_zoom_in_grows_the_size_keeping_the_family(self, qapp, mocker):
        display = TextDisplay(mocker.MagicMock())
        family = display.font.family()
        size = display.font.pointSizeF()

        display.zoom_font(1.0)

        assert display.font.pointSizeF() == size + 1.0
        assert display.font.family() == family

    def test_zoom_out_shrinks_the_size(self, qapp, mocker):
        display = TextDisplay(mocker.MagicMock())
        size = display.font.pointSizeF()

        display.zoom_font(-1.0)

        assert display.font.pointSizeF() == size - 1.0

    def test_zoom_in_clamps_to_the_maximum_size(self, qapp, mocker):
        display = TextDisplay(mocker.MagicMock())

        for _ in range(100):
            display.zoom_font(1.0)

        assert display.font.pointSizeF() == FONT_SIZE_MAX

    def test_zoom_out_clamps_to_the_minimum_size(self, qapp, mocker):
        display = TextDisplay(mocker.MagicMock())

        for _ in range(100):
            display.zoom_font(-1.0)

        assert display.font.pointSizeF() == FONT_SIZE_MIN

    def test_zoom_beyond_the_clamp_does_nothing_further(self, qapp, mocker):
        display = TextDisplay(mocker.MagicMock())
        for _ in range(100):
            display.zoom_font(-1.0)
        display.main_window.reset_mock()

        display.zoom_font(-1.0)  # already at the minimum

        display.main_window.resize_editor_grid.assert_not_called()
        display.main_window.show_font_size.assert_not_called()

    def test_zoom_never_resizes_the_main_window(self, qapp, mocker):
        display = TextDisplay(mocker.MagicMock())
        display.main_window.reset_mock()  # construction itself calls adjustSize() once

        display.zoom_font(1.0)

        display.main_window.adjustSize.assert_not_called()

    def test_zoom_asks_only_this_displays_grid_to_resize(self, qapp, mocker):
        display = TextDisplay(mocker.MagicMock())

        display.zoom_font(1.0)

        display.main_window.resize_editor_grid.assert_called_once_with(display)

    def test_zoom_reports_the_new_size(self, qapp, mocker):
        display = TextDisplay(mocker.MagicMock())

        display.zoom_font(2.0)

        display.main_window.show_font_size.assert_called_once_with(
            display, display.font.pointSizeF())


class TestFontZoomReset:
    """Ctrl+0 (reset_font_zoom) undoes any zoom, back to the editor-wide base size."""

    def test_reset_restores_the_base_size(self, qapp, mocker):
        display = TextDisplay(mocker.MagicMock())
        base = display._base_font_size
        display.zoom_font(3.0)

        display.reset_font_zoom()

        assert display.font.pointSizeF() == base

    def test_reset_does_nothing_when_not_zoomed(self, qapp, mocker):
        display = TextDisplay(mocker.MagicMock())

        display.reset_font_zoom()

        display.main_window.resize_editor_grid.assert_not_called()
        display.main_window.show_font_size.assert_not_called()

    def test_reset_uses_whatever_set_base_font_set_last(self, qapp, mocker):
        display = TextDisplay(mocker.MagicMock())
        display.set_base_font(display.font.family(), 20.0)
        display.zoom_font(5.0)

        display.reset_font_zoom()

        assert display.font.pointSizeF() == 20.0


class TestHandleKeyboardZoomRouting:
    """Ctrl +/-/0 are intercepted in handle_keyboard and never reach Neovim."""

    def test_ctrl_plus_zooms_in(self, qapp, mocker):
        display = TextDisplay(mocker.MagicMock())
        mocker.patch.object(display, "zoom_font")

        display.handle_keyboard("", Qt.Key.Key_Plus, ControlModifier)

        display.zoom_font.assert_called_once_with(1.0)
        display.main_window.nvi.future_request.assert_not_called()

    def test_ctrl_equal_also_zooms_in(self, qapp, mocker):
        # '+' needs Shift on many layouts, so plain Ctrl+= is the everyday-comfortable shortcut
        display = TextDisplay(mocker.MagicMock())
        mocker.patch.object(display, "zoom_font")

        display.handle_keyboard("=", Qt.Key.Key_Equal, ControlModifier)

        display.zoom_font.assert_called_once_with(1.0)
        display.main_window.nvi.future_request.assert_not_called()

    def test_ctrl_minus_zooms_out(self, qapp, mocker):
        display = TextDisplay(mocker.MagicMock())
        mocker.patch.object(display, "zoom_font")

        display.handle_keyboard("", Qt.Key.Key_Minus, ControlModifier)

        display.zoom_font.assert_called_once_with(-1.0)
        display.main_window.nvi.future_request.assert_not_called()

    def test_ctrl_0_resets(self, qapp, mocker):
        display = TextDisplay(mocker.MagicMock())
        mocker.patch.object(display, "reset_font_zoom")

        display.handle_keyboard("", Qt.Key.Key_0, ControlModifier)

        display.reset_font_zoom.assert_called_once()
        display.main_window.nvi.future_request.assert_not_called()

    def test_ctrl_shift_plus_still_zooms(self, qapp, mocker):
        # some keyboard layouts need Shift held to actually produce '+'
        display = TextDisplay(mocker.MagicMock())
        mocker.patch.object(display, "zoom_font")

        display.handle_keyboard("", Qt.Key.Key_Plus, ControlModifier | ShiftModifier)

        display.zoom_font.assert_called_once_with(1.0)

    def test_ctrl_alt_plus_does_not_zoom(self, qapp, mocker):
        # a genuine Ctrl-Alt-... combo must never be swallowed as a zoom shortcut
        display = TextDisplay(mocker.MagicMock())
        mocker.patch.object(display, "zoom_font")

        display.handle_keyboard("", Qt.Key.Key_Plus, ControlModifier | AltModifier)

        display.zoom_font.assert_not_called()

    def test_plain_0_without_ctrl_goes_to_neovim_as_text(self, qapp, mocker):
        display = TextDisplay(mocker.MagicMock())
        mocker.patch.object(display, "reset_font_zoom")

        display.handle_keyboard("0", Qt.Key.Key_0, Qt.KeyboardModifier.NoModifier)

        display.reset_font_zoom.assert_not_called()
        display.main_window.nvi.future_request.assert_called_once_with("nvim_input", "0")


class TestPerInstanceCaches:
    """Regression test: each display must own its own char-drawing-widths cache.

    It used to be a class attribute, shared by every TextDisplay -- so zooming one pane cached
    ITS font's character widths under a key any other display's paint() would also hit, making
    an unrelated, never-zoomed tab render with the wrong (zoomed) glyph widths ("pegoteado").
    """

    def test_two_displays_do_not_share_the_widths_cache_object(self, qapp, mocker):
        main_window = mocker.MagicMock()
        display_a = TextDisplay(main_window)
        display_b = TextDisplay(main_window)

        assert display_a._char_drawing_widths_cache is not display_b._char_drawing_widths_cache

    def test_zooming_one_display_does_not_touch_a_siblings_cache(self, qapp, mocker):
        main_window = mocker.MagicMock()
        display = TextDisplay(main_window)
        sibling = TextDisplay(main_window)
        sibling._char_drawing_widths_cache["marker"] = "untouched"

        display.zoom_font(1.0)

        assert sibling._char_drawing_widths_cache == {"marker": "untouched"}

    def test_drawing_width_reflects_this_displays_own_font_size(self, qapp, mocker):
        main_window = mocker.MagicMock()
        small = TextDisplay(main_window)
        small.set_base_font("Courier", 8)
        big = TextDisplay(main_window)
        big.set_base_font("Courier", 40)

        # LogicalChar hashes/compares by (char, is_wide) only -- format is irrelevant here, so a
        # placeholder is fine (see test_logical_lines.py's own fixtures for the same pattern)
        char = LogicalChar("M", "irrelevant-format")
        small_width, _, _ = small._get_drawing_widths(char)
        big_width, _, _ = big._get_drawing_widths(char)

        assert big_width > small_width
