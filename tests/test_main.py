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
from PyQt6.QtWidgets import QMenu

from nysor.main import BusySpinner, MainApp, MainMenu


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


class TestBusySpinner:
    """The spinner waits before appearing, so short busy periods never flash anything."""

    def test_starts_hidden(self, qapp):
        """No busy period was reported; nothing is shown."""
        spinner = BusySpinner()
        assert spinner.isHidden()

    def test_busy_does_not_show_immediately(self, qapp):
        """Right after busy_start the spinner is still hidden (grace delay running)."""
        spinner = BusySpinner()
        spinner.set_busy(True)
        assert spinner.isHidden()
        assert spinner._appearer.isActive()

    def test_shows_and_rotates_after_the_delay(self, qapp):
        """Once the grace delay elapses the spinner shows and starts rotating."""
        spinner = BusySpinner()
        spinner.set_busy(True)
        spinner._start_spinning()  # the appearer's slot, fired directly (no real wait)
        assert not spinner.isHidden()
        assert spinner._rotator.isActive()
        first_frame = spinner.text()
        spinner._advance()
        assert spinner.text() != first_frame

    def test_short_pause_cancels_the_appearance(self, qapp):
        """A busy_stop before the grace delay elapses leaves the spinner hidden."""
        spinner = BusySpinner()
        spinner.set_busy(True)
        spinner.set_busy(False)
        assert spinner.isHidden()
        assert not spinner._appearer.isActive()

    def test_busy_stop_hides_and_stops_everything(self, qapp):
        """Stopping while already spinning hides the spinner and stops the rotation."""
        spinner = BusySpinner()
        spinner.set_busy(True)
        spinner._start_spinning()
        spinner.set_busy(False)
        assert spinner.isHidden()
        assert not spinner._rotator.isActive()

    def test_main_app_delegates_to_the_spinner(self, mocker):
        """MainApp.set_busy just forwards to the spinner widget."""
        fake_self = mocker.MagicMock()
        MainApp.set_busy(fake_self, True)
        fake_self._busy_spinner.set_busy.assert_called_once_with(True)


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


def _duplicate_mnemonics(labels):
    """Return the mnemonic letters (after '&') that show up more than once among `labels`."""
    letters = [re.search(r"&(\w)", label).group(1).upper() for label in labels]
    return {letter for letter in letters if letters.count(letter) > 1}


class TestMenuMnemonics:
    """Two entries sharing a mnemonic (the letter after '&') make one unreachable via keyboard."""

    @pytest.mark.parametrize("title", ["&File", "&Help"])
    def test_submenu_entries_have_unique_mnemonics(self, title):
        labels = [entry[0] for entry in MainMenu.MENU[title] if entry is not None]
        duplicates = _duplicate_mnemonics(labels)
        assert not duplicates, f"Repeated mnemonic(s) in {title!r} menu: {duplicates}"

    def test_top_level_menus_have_unique_mnemonics(self):
        """E.g. 'Fil&e' and 'H&elp' would both bind to 'E' -- one becomes unreachable."""
        duplicates = _duplicate_mnemonics(MainMenu.MENU.keys())
        assert not duplicates, f"Repeated mnemonic(s) among top-level menus: {duplicates}"
