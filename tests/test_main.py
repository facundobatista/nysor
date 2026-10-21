# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Tests for main.py.

MainApp's __init__ spawns a real Neovim process and a real swarm server, and nothing in this
repo instantiates it for real in a test -- so these tests call its plain methods unbound, with a
mocker.MagicMock() standing in for `self`, instead of building a whole MainApp. MainMenu's
__init__ is light (it just stores its collaborators), so it is built for real, the same way
tests/test_nvim_notifications.py builds a real NvimNotifications with a mocked host.
"""

import asyncio
import os
import re

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMenu

from nysor.main import BusyIndicator, MainApp, MainMenu


class TestOpenFileDialog:

    def test_picked_file_is_delegated_to_open_path(self, mocker):
        """A confirmed pick calls open_path with the chosen filename."""
        fake_self = mocker.MagicMock()
        mocker.patch("nysor.main.QFileDialog.getOpenFileName", return_value=("/picked", ""))
        MainApp.open_file_dialog(fake_self)
        fake_self.open_path.assert_called_once_with("/picked")

    def test_cancelled_dialog_does_nothing(self, mocker):
        """Cancelling the dialog (empty filename) never calls open_path."""
        fake_self = mocker.MagicMock()
        mocker.patch("nysor.main.QFileDialog.getOpenFileName", return_value=("", ""))
        MainApp.open_file_dialog(fake_self)
        fake_self.open_path.assert_not_called()


class TestOpenPath:

    def test_registers_recent_before_anything_else(self, mocker):
        """The normalized path is registered as recent regardless of the outcome below."""
        fake_self = mocker.MagicMock()
        fake_self._reveal_path.return_value = True
        register = mocker.patch("nysor.main.recent_files.register")
        MainApp.open_path(fake_self, __file__)
        register.assert_called_once_with(os.path.realpath(__file__))

    def test_already_open_here_just_reveals_it(self, mocker):
        """When _reveal_path finds it locally, the swarm is never consulted."""
        fake_self = mocker.MagicMock()
        fake_self._reveal_path.return_value = True
        mocker.patch("nysor.main.recent_files.register")
        call_async = mocker.patch("nysor.main.call_async")
        MainApp.open_path(fake_self, __file__)
        call_async.assert_not_called()

    def test_not_open_here_goes_through_the_swarm(self, mocker):
        """When _reveal_path can't find it locally, opening is delegated to the swarm check."""
        fake_self = mocker.MagicMock()
        fake_self._reveal_path.return_value = False
        mocker.patch("nysor.main.recent_files.register")
        call_async = mocker.patch("nysor.main.call_async")
        MainApp.open_path(fake_self, __file__)
        call_async.assert_called_once_with(
            fake_self._open_if_free_in_swarm, os.path.realpath(__file__))


class TestOpenPathsFromCli:

    async def test_registers_each_path_as_recent(self, mocker):
        """Paths opened from the command line must show up in 'Recent' just like manual opens."""
        fake_self = mocker.MagicMock()
        fake_self._feed_neovim_from_path = mocker.AsyncMock()
        register = mocker.patch("nysor.main.recent_files.register")

        await MainApp._open_paths_from_cli(fake_self, ["/a", "/b"])

        assert register.call_args_list == [mocker.call("/a"), mocker.call("/b")]

    async def test_first_path_reuses_window_rest_open_new_tabs(self, mocker):
        """The first path opens in the current window; subsequent ones open in new tabs."""
        fake_self = mocker.MagicMock()
        fake_self._feed_neovim_from_path = mocker.AsyncMock()
        mocker.patch("nysor.main.recent_files.register")

        await MainApp._open_paths_from_cli(fake_self, ["/a", "/b"])

        fake_self._feed_neovim_from_path.assert_has_calls([
            mocker.call("/a", new_tab=False),
            mocker.call("/b", new_tab=True),
        ])


class TestUndoEntry:

    def test_none_entry_does_nothing(self, mocker):
        """No target editor -> no RPC sent."""
        fake_self = mocker.MagicMock()
        MainApp.undo_entry(fake_self, None)
        fake_self.nvi.future_request.assert_not_called()

    def test_sends_undo_to_the_entrys_window(self, mocker):
        """'undo' is run in the context of the target's own window."""
        fake_self = mocker.MagicMock()
        entry = mocker.MagicMock(win_id=42)
        MainApp.undo_entry(fake_self, entry)
        fake_self.nvi.future_request.assert_called_once_with(
            "nvim_call_function", "win_execute", [42, "undo"])


class TestRedoEntry:

    def test_none_entry_does_nothing(self, mocker):
        """No target editor -> no RPC sent."""
        fake_self = mocker.MagicMock()
        MainApp.redo_entry(fake_self, None)
        fake_self.nvi.future_request.assert_not_called()

    def test_sends_redo_to_the_entrys_window(self, mocker):
        """'redo' is run in the context of the target's own window."""
        fake_self = mocker.MagicMock()
        entry = mocker.MagicMock(win_id=42)
        MainApp.redo_entry(fake_self, entry)
        fake_self.nvi.future_request.assert_called_once_with(
            "nvim_call_function", "win_execute", [42, "redo"])


class TestGetUndoRedoState:

    async def test_none_entry_can_do_neither(self, mocker):
        """No target editor -> nothing to undo or redo."""
        fake_self = mocker.MagicMock()
        assert await MainApp.get_undo_redo_state(fake_self, None) == (False, False)

    async def test_at_oldest_change_cannot_undo(self, mocker):
        """seq_cur == 0 means the buffer is already at its original, pre-edit state."""
        fake_self = mocker.MagicMock()
        fake_self.nvi.call = mocker.AsyncMock(return_value=[0, 5])
        entry = mocker.MagicMock(win_id=42)
        assert await MainApp.get_undo_redo_state(fake_self, entry) == (False, True)

    async def test_at_newest_change_cannot_redo(self, mocker):
        """seq_cur == seq_last means there is no later state to redo into."""
        fake_self = mocker.MagicMock()
        fake_self.nvi.call = mocker.AsyncMock(return_value=[5, 5])
        entry = mocker.MagicMock(win_id=42)
        assert await MainApp.get_undo_redo_state(fake_self, entry) == (True, False)

    async def test_mid_history_can_do_both(self, mocker):
        """Somewhere between the oldest and newest change, both directions are open."""
        fake_self = mocker.MagicMock()
        fake_self.nvi.call = mocker.AsyncMock(return_value=[3, 5])
        entry = mocker.MagicMock(win_id=42)
        assert await MainApp.get_undo_redo_state(fake_self, entry) == (True, True)


class TestCutEntry:

    def test_none_entry_does_nothing(self, mocker):
        """No target editor -> no RPC sent."""
        fake_self = mocker.MagicMock()
        MainApp.cut_entry(fake_self, None)
        fake_self.nvi.future_request.assert_not_called()

    def test_reselects_and_deletes_into_the_clipboard_register(self, mocker):
        """'gv' restores the last selection (any kind) before deleting it into '+'."""
        fake_self = mocker.MagicMock()
        entry = mocker.MagicMock(win_id=42)
        MainApp.cut_entry(fake_self, entry)
        fake_self.nvi.future_request.assert_called_once_with(
            "nvim_call_function", "win_execute", [42, 'normal! gv"+d'])


class TestCopyEntry:

    def test_none_entry_does_nothing(self, mocker):
        """No target editor -> no RPC sent."""
        fake_self = mocker.MagicMock()
        MainApp.copy_entry(fake_self, None)
        fake_self.nvi.future_request.assert_not_called()

    def test_reselects_and_yanks_into_the_clipboard_register(self, mocker):
        """'gv' restores the last selection (any kind) before yanking it into '+'."""
        fake_self = mocker.MagicMock()
        entry = mocker.MagicMock(win_id=42)
        MainApp.copy_entry(fake_self, entry)
        fake_self.nvi.future_request.assert_called_once_with(
            "nvim_call_function", "win_execute", [42, 'normal! gv"+y'])


class TestPasteEntry:

    def test_none_entry_does_nothing(self, mocker):
        """No target editor -> no RPC sent."""
        fake_self = mocker.MagicMock()
        MainApp.paste_entry(fake_self, None)
        fake_self.nvi.future_request.assert_not_called()

    def test_pastes_the_clipboard_register(self, mocker):
        """No selection is involved; it's just a plain paste of the '+' register."""
        fake_self = mocker.MagicMock()
        entry = mocker.MagicMock(win_id=42)
        MainApp.paste_entry(fake_self, entry)
        fake_self.nvi.future_request.assert_called_once_with(
            "nvim_call_function", "win_execute", [42, 'normal! "+p'])


class TestGetVisualSelectionActive:

    async def test_none_entry_is_never_active(self, mocker):
        """No target editor -> no selection to speak of."""
        fake_self = mocker.MagicMock()
        assert await MainApp.get_visual_selection_active(fake_self, None) is False

    async def test_normal_mode_is_not_active(self, mocker):
        """Plain Normal mode has no selection."""
        fake_self = mocker.MagicMock()
        fake_self.nvi.call = mocker.AsyncMock(return_value={"mode": "n", "blocking": False})
        entry = mocker.MagicMock(win_id=42)
        assert await MainApp.get_visual_selection_active(fake_self, entry) is False

    async def test_charwise_visual_is_active(self, mocker):
        """Lowercase 'v' is charwise Visual mode."""
        fake_self = mocker.MagicMock()
        fake_self.nvi.call = mocker.AsyncMock(return_value={"mode": "v", "blocking": False})
        entry = mocker.MagicMock(win_id=42)
        assert await MainApp.get_visual_selection_active(fake_self, entry) is True

    async def test_linewise_visual_is_active(self, mocker):
        """Uppercase 'V' is linewise Visual mode."""
        fake_self = mocker.MagicMock()
        fake_self.nvi.call = mocker.AsyncMock(return_value={"mode": "V", "blocking": False})
        entry = mocker.MagicMock(win_id=42)
        assert await MainApp.get_visual_selection_active(fake_self, entry) is True

    async def test_blockwise_visual_is_active(self, mocker):
        """Ctrl-V ('\\x16') is blockwise Visual mode."""
        fake_self = mocker.MagicMock()
        fake_self.nvi.call = mocker.AsyncMock(return_value={"mode": "\x16", "blocking": False})
        entry = mocker.MagicMock(win_id=42)
        assert await MainApp.get_visual_selection_active(fake_self, entry) is True



class TestCloseGui:
    """The close sequence hands the exit over to the main() coroutine only at the very end."""

    async def test_initial_close_kicks_off_the_quit_sequence(self, mocker):
        """The first close only starts the async quit sequence; the window stays up."""
        fake_self = mocker.MagicMock()
        fake_self._closing = 0
        fake_self._quit = mocker.AsyncMock()
        fake_self._close_event = asyncio.Event()
        event = mocker.MagicMock()

        MainApp.close_gui(fake_self, event)

        assert fake_self._closing == 1
        event.ignore.assert_called_once()
        assert not fake_self._close_event.is_set()

    def test_mid_close_request_changes_nothing(self, mocker):
        """A close request arriving while the quit sequence runs is just ignored."""
        fake_self = mocker.MagicMock()
        fake_self._closing = 1
        fake_self._close_event = asyncio.Event()
        event = mocker.MagicMock()

        MainApp.close_gui(fake_self, event)

        event.ignore.assert_called_once()
        assert not fake_self._close_event.is_set()

    def test_final_close_signals_main_coroutine_and_accepts(self, mocker):
        """The final close sets the app-close event (waking main() on a live loop) and accepts."""
        fake_self = mocker.MagicMock()
        fake_self._closing = 2
        fake_self._close_event = asyncio.Event()
        event = mocker.MagicMock()

        MainApp.close_gui(fake_self, event)

        assert fake_self._close_event.is_set()
        event.accept.assert_called_once()


@pytest.fixture
def indicator(qapp):
    """A real BusyIndicator; always cleaned up after the test (cursor restored, hidden)."""
    ind = BusyIndicator()
    yield ind
    ind.set_busy(False)


class TestBusyIndicator:
    """The indicator (spinner + busy cursor) waits before kicking in, so short busy periods
    never flash anything."""

    def test_starts_hidden(self, indicator):
        """No busy period was reported; nothing is shown."""
        assert indicator.isHidden()

    def test_busy_does_not_show_immediately(self, indicator):
        """Right after busy_start the spinner is still hidden (grace delay running)."""
        indicator.set_busy(True)
        assert indicator.isHidden()
        assert indicator._appearer.isActive()

    def test_shows_and_rotates_after_the_delay(self, indicator):
        """Once the grace delay elapses the spinner shows and starts rotating."""
        indicator.set_busy(True)
        indicator._start_indicating()  # the appearer's slot, fired directly (no real wait)
        assert not indicator.isHidden()
        assert indicator._rotator.isActive()
        first_frame = indicator.text()
        indicator._advance()
        assert indicator.text() != first_frame

    def test_sets_the_busy_cursor_after_the_delay(self, qapp, indicator):
        """When the indicator really starts, the mouse cursor goes busy app-wide."""
        indicator.set_busy(True)
        assert qapp.overrideCursor() is None  # grace delay: not yet
        indicator._start_indicating()
        assert qapp.overrideCursor().shape() == Qt.CursorShape.BusyCursor

    def test_short_pause_cancels_the_appearance(self, indicator, qapp):
        """A busy_stop before the grace delay elapses leaves everything untouched."""
        indicator.set_busy(True)
        indicator.set_busy(False)
        assert indicator.isHidden()
        assert not indicator._appearer.isActive()
        assert qapp.overrideCursor() is None

    def test_busy_stop_hides_and_restores_everything(self, qapp, indicator):
        """Stopping while indicating hides the spinner, stops the rotation, restores the cursor."""
        indicator.set_busy(True)
        indicator._start_indicating()
        indicator.set_busy(False)
        assert indicator.isHidden()
        assert not indicator._rotator.isActive()
        assert qapp.overrideCursor() is None

    def test_repeated_busy_start_does_not_stack_cursor_overrides(self, qapp, indicator, mocker):
        """Several busy_start in a row set the cursor override only once."""
        indicator.set_busy(True)
        spy = mocker.spy(QApplication, "setOverrideCursor")
        indicator._start_indicating()
        indicator.set_busy(True)  # already indicating: must be a no-op
        assert spy.call_count == 1

    def test_main_app_delegates_to_the_indicator(self, mocker):
        """MainApp.set_busy just forwards to the indicator widget."""
        fake_self = mocker.MagicMock()
        MainApp.set_busy(fake_self, True)
        fake_self._busy_indicator.set_busy.assert_called_once_with(True)


@pytest.fixture
def main_menu(mocker):
    """A real MainMenu with mocked app/host, the same pattern used for NvimNotifications."""
    return MainMenu(mocker.MagicMock(), mocker.MagicMock(), MainMenu.SCOPE_MAIN, lambda: None)


class TestPopulateOpenRecent:

    def test_no_recent_files_shows_disabled_placeholder(self, qapp, main_menu, mocker):
        """An empty recent list shows one disabled informational item."""
        mocker.patch("nysor.main.recent_files.get_recent", return_value=[])
        recent_menu = QMenu()
        main_menu._populate_open_recent(recent_menu)
        actions = recent_menu.actions()
        assert len(actions) == 1
        assert not actions[0].isEnabled()

    def test_lists_existing_paths_and_skips_missing_ones(self, qapp, main_menu, mocker, tmp_path):
        """Only paths that still exist on disk are shown, most recent first."""
        existing = tmp_path / "here.txt"
        existing.write_text("x")
        missing = tmp_path / "gone.txt"
        mocker.patch(
            "nysor.main.recent_files.get_recent", return_value=[existing, missing])

        recent_menu = QMenu()
        main_menu._populate_open_recent(recent_menu)

        actions = recent_menu.actions()
        assert [a.text() for a in actions] == ["here.txt"]

    def test_label_is_filename_and_tooltip_is_directory(self, qapp, main_menu, mocker, tmp_path):
        """The path is too long to show whole; the filename is the label, the dir a tooltip."""
        existing = tmp_path / "here.txt"
        existing.write_text("x")
        mocker.patch("nysor.main.recent_files.get_recent", return_value=[existing])

        recent_menu = QMenu()
        main_menu._populate_open_recent(recent_menu)

        action = recent_menu.actions()[0]
        assert action.text() == "here.txt"
        assert action.toolTip() == str(tmp_path)

    def test_clicking_an_entry_opens_that_path(self, qapp, main_menu, mocker, tmp_path):
        """Triggering a recent-file action opens that exact path via the app."""
        existing = tmp_path / "here.txt"
        existing.write_text("x")
        mocker.patch("nysor.main.recent_files.get_recent", return_value=[existing])

        recent_menu = QMenu()
        main_menu._populate_open_recent(recent_menu)
        recent_menu.actions()[0].trigger()

        main_menu._app.open_path.assert_called_once_with(existing)

    def test_rebuilds_from_scratch_on_each_call(self, qapp, main_menu, mocker, tmp_path):
        """A second populate call does not accumulate stale entries from the first one."""
        existing = tmp_path / "here.txt"
        existing.write_text("x")
        mocker.patch("nysor.main.recent_files.get_recent", return_value=[existing])

        recent_menu = QMenu()
        main_menu._populate_open_recent(recent_menu)
        main_menu._populate_open_recent(recent_menu)

        assert len(recent_menu.actions()) == 1


class TestRefreshEditMenuState:

    @pytest.fixture
    def menu_actions(self, main_menu, mocker):
        """Stand-in QAction mocks for the four state-dependent Edit entries."""
        actions = {
            "&Undo": mocker.MagicMock(),
            "Re&do": mocker.MagicMock(),
            "Cu&t": mocker.MagicMock(),
            "&Copy": mocker.MagicMock(),
        }
        main_menu.menu_actions = actions
        return actions

    async def test_enables_undo_redo_per_the_apps_report(self, main_menu, menu_actions, mocker):
        """Undo/Redo are enabled or disabled to match what the app reports for the target."""
        main_menu._app.get_undo_redo_state = mocker.AsyncMock(return_value=(True, False))
        main_menu._app.get_visual_selection_active = mocker.AsyncMock(return_value=False)

        await main_menu._refresh_edit_menu_state()

        menu_actions["&Undo"].setEnabled.assert_called_once_with(True)
        menu_actions["Re&do"].setEnabled.assert_called_once_with(False)

    async def test_enables_cut_copy_per_the_apps_report(self, main_menu, menu_actions, mocker):
        """Cut/Copy are enabled together, based on whether there is an active selection."""
        main_menu._app.get_undo_redo_state = mocker.AsyncMock(return_value=(False, False))
        main_menu._app.get_visual_selection_active = mocker.AsyncMock(return_value=True)

        await main_menu._refresh_edit_menu_state()

        menu_actions["Cu&t"].setEnabled.assert_called_once_with(True)
        menu_actions["&Copy"].setEnabled.assert_called_once_with(True)


def _duplicate_mnemonics(labels):
    """Return the mnemonic letters (after '&') that show up more than once among `labels`."""
    letters = [re.search(r"&(\w)", label).group(1).upper() for label in labels]
    return {letter for letter in letters if letters.count(letter) > 1}


class TestMenuMnemonics:
    """Two entries sharing a mnemonic (the letter after '&') make one unreachable via keyboard."""

    @pytest.mark.parametrize("title", ["&File", "&Edit", "&Help"])
    def test_submenu_entries_have_unique_mnemonics(self, title):
        options, _ = MainMenu.MENU[title]
        labels = [entry[0] for entry in options if entry is not None]
        duplicates = _duplicate_mnemonics(labels)
        assert not duplicates, f"Repeated mnemonic(s) in {title!r} menu: {duplicates}"

    def test_top_level_menus_have_unique_mnemonics(self):
        """E.g. 'Fil&e' and 'H&elp' would both bind to 'E' -- one becomes unreachable."""
        duplicates = _duplicate_mnemonics(MainMenu.MENU.keys())
        assert not duplicates, f"Repeated mnemonic(s) among top-level menus: {duplicates}"
