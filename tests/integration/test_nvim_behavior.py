# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Integration tests locking in empirically-verified Neovim RPC/multigrid behavior.

Each test here reproduces a fact about Neovim's protocol that nysor's code currently assumes so a
future Neovim version that changes any of this makes these fail instead of silently breaking for
users. RPC-protocol level only: no Qt, no MainApp.
"""

import pytest

from nysor import nvim_notifications
from nysor.nvim_interface import NeovimError
from nysor.nvim_notifications import GridRegistry

from conftest import handle_id, redraw_events, settle


async def attach_multigrid(iface):
    """Attach a UI with ext_linegrid+ext_multigrid (the base every test here needs)."""
    await iface.call("nvim_ui_attach", 80, 24, {"ext_linegrid": True, "ext_multigrid": True})
    await settle(iface)


class TestGridModel:
    """Attaching multigrid on a single fresh window produces exactly the documented 3 grids."""

    async def test_three_grids_with_their_documented_roles(self, nvim):
        iface, _notifs, _main_window, _raw = nvim
        await attach_multigrid(iface)

        entries = nvim_notifications.registry.get_all_entries()
        assert len(entries) == 1, "exactly one window grid for the one open window"
        window_grid = entries[0].grid_id

        assert nvim_notifications.registry.get_kind_by_grid(1) == GridRegistry.GRID_GLOBAL
        assert (
            nvim_notifications.registry.get_kind_by_grid(window_grid) == GridRegistry.GRID_WINDOW)
        message_grid = nvim_notifications.registry.message_grid
        assert message_grid is not None
        assert nvim_notifications.registry.get_kind_by_grid(message_grid) == (
            GridRegistry.GRID_MESSAGE)


class TestTabLifecycleEvents:
    """Tab switching under multigrid: only the current tabpage's window is ever live."""

    async def test_tabedit_hides_the_previous_window_not_destroys_it(self, nvim, tmp_path):
        iface, _notifs, main_window, raw = nvim
        await attach_multigrid(iface)
        first_calls = main_window.build_editor_tab.call_count

        other_file = tmp_path / "other.txt"
        other_file.write_text("hello\n")
        await iface.call("nvim_command", f"tabedit {other_file}")
        await settle(iface)

        # a second grid was built for the new tabpage's window ...
        assert main_window.build_editor_tab.call_count == first_calls + 1
        # ... and the previous window was hidden, not destroyed
        submethods = [name for name, _args in redraw_events(raw)]
        assert "win_hide" in submethods
        assert "grid_destroy" not in submethods

    async def test_tabnext_and_tabprevious_swap_hide_and_pos(self, nvim, tmp_path):
        iface, _notifs, _main_window, raw = nvim
        await attach_multigrid(iface)
        other_file = tmp_path / "other.txt"
        other_file.write_text("hello\n")
        await iface.call("nvim_command", f"tabedit {other_file}")
        await settle(iface)
        raw.clear()  # only care about events from here on

        await iface.call("nvim_command", "tabprevious")
        await settle(iface)
        submethods = [name for name, _args in redraw_events(raw)]
        assert "win_hide" in submethods
        assert "win_pos" in submethods


class TestLastWindowClose:
    """Neovim refuses to close the sole remaining window (E444); a second one closes fine."""

    async def test_closing_one_of_two_windows_succeeds(self, nvim):
        iface, _notifs, _main_window, _raw = nvim
        await attach_multigrid(iface)
        await iface.call("nvim_command", "vsplit")
        await settle(iface)
        wins = [handle_id(w) for w in await iface.call("nvim_list_wins")]
        assert len(wins) == 2

        await iface.call("nvim_win_close", wins[0], False)
        remaining = await iface.call("nvim_list_wins")
        assert len(remaining) == 1

    async def test_closing_the_last_window_raises(self, nvim):
        iface, _notifs, _main_window, _raw = nvim
        await attach_multigrid(iface)
        wins = [handle_id(w) for w in await iface.call("nvim_list_wins")]
        assert len(wins) == 1

        with pytest.raises(NeovimError) as exc_info:
            await iface.call("nvim_win_close", wins[0], False)
        assert "E444" in str(exc_info.value)


class TestWinExecuteAcrossWindows:
    """Does 'win_execute(win, "edit!")' reliably reload a NON-current window's buffer?

    This is the exact question behind nysor's Reload feature (main.py's reload()); answering it
    empirically here removes the need to guess.
    """

    async def test_edit_bang_reloads_a_non_current_window(self, nvim, tmp_path):
        iface, _notifs, _main_window, _raw = nvim
        await attach_multigrid(iface)

        file_a = tmp_path / "a.txt"
        file_a.write_text("hello a\n")
        file_b = tmp_path / "b.txt"
        file_b.write_text("hello b\n")

        await iface.call("nvim_command", f"edit {file_a}")
        win_a = handle_id(await iface.call("nvim_get_current_win"))
        buf_a = handle_id(await iface.call("nvim_get_current_buf"))
        await iface.call("nvim_command", f"vsplit {file_b}")
        win_b = handle_id(await iface.call("nvim_get_current_win"))
        assert win_b != win_a

        # modify window A's buffer without writing it, while window B stays current
        await iface.call("nvim_call_function", "win_execute", [win_a, "normal! Ixxx"])
        modified = await iface.call("nvim_get_option_value", "modified", {"buf": buf_a})
        assert modified is True

        # reload window A's buffer exactly as nysor's reload() does, from window B
        await iface.call("nvim_call_function", "win_execute", [win_a, "edit!"])

        lines = await iface.call("nvim_buf_get_lines", buf_a, 0, -1, False)
        assert lines == ["hello a"]
        modified = await iface.call("nvim_get_option_value", "modified", {"buf": buf_a})
        assert modified is False
        # win_execute must not have changed which window is current
        current_win = handle_id(await iface.call("nvim_get_current_win"))
        assert current_win == win_b


class TestMouseGridTargeting:
    """A mouse event's grid id, not just its row/col, decides which window it targets."""

    async def test_click_on_each_grid_targets_its_own_window(self, nvim):
        iface, _notifs, _main_window, _raw = nvim
        await attach_multigrid(iface)
        await iface.call("nvim_command", "vsplit")
        await settle(iface)

        entries = nvim_notifications.registry.get_all_entries()
        assert len(entries) == 2

        # tell the two windows apart by screen column: vsplit puts one at col 0, the other after
        positions = {}
        for entry in entries:
            row, col = await iface.call("nvim_win_get_position", entry.win_id)
            positions[entry.win_id] = col
        left, right = sorted(entries, key=lambda e: positions[e.win_id])

        await iface.call(
            "nvim_input_mouse", "left", "press", "", left.grid_id, 0, 0)
        await iface.call(
            "nvim_input_mouse", "left", "release", "", left.grid_id, 0, 0)
        await settle(iface)
        current = handle_id(await iface.call("nvim_get_current_win"))
        assert current == left.win_id

        await iface.call(
            "nvim_input_mouse", "left", "press", "", right.grid_id, 0, 0)
        await iface.call(
            "nvim_input_mouse", "left", "release", "", right.grid_id, 0, 0)
        await settle(iface)
        current = handle_id(await iface.call("nvim_get_current_win"))
        assert current == right.win_id
