# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Shared fixtures for integration tests that talk to a real, live Neovim over RPC.

These never mock Neovim: they spawn a real `nvim --headless` subprocess and speak msgpack-RPC
against it using the project's own NvimInterface. Skipped entirely if no usable nvim binary can be
found at all (see nvim_path below); otherwise they run for real, against whatever version was
resolved, and fail for real if something is wrong -- there is no version-gated skip here, that is
what the CI matrix (several exact-pinned nvim versions) is for.
"""

import asyncio
import os
import shutil

import pytest

from nysor import nvim_notifications
from nysor.nvim_interface import NvimInterface
from nysor.nvim_notifications import GridRegistry, NvimNotifications

NVIM_ENV_VAR = "NYSOR_NVIM"


def _resolve_nvim_path():
    """Resolve which real nvim binary to use: NYSOR_NVIM env var, else PATH; None if neither."""
    return os.environ.get(NVIM_ENV_VAR) or shutil.which("nvim")


@pytest.fixture(scope="session")
def nvim_path():
    """The real nvim executable to test against; skip the whole suite if none can be found."""
    path = _resolve_nvim_path()
    if path is None:
        pytest.skip(f"no nvim found: set {NVIM_ENV_VAR} or install nvim on PATH")
    return path


@pytest.fixture
async def nvim(nvim_path, mocker):
    """A live NvimInterface talking to a freshly-spawned real nvim (one process per test).

    Notifications are routed through a real NvimNotifications -- so the actual event-parsing and
    registry logic runs against real nvim output -- but with a mocked `main_window`: no Qt is
    involved. A fresh GridRegistry is patched in for the duration of the test so registry state
    never leaks between tests (it is a module-level singleton in production).

    Yields (iface, notifs, main_window, raw_events): `raw_events` collects every
    (method, params) notification exactly as received, for tests that need to check the raw event
    shapes/ordering themselves (e.g. win_hide vs grid_destroy) rather than NvimNotifications'
    processed state.
    """
    mocker.patch.object(nvim_notifications, "registry", GridRegistry())

    main_window = mocker.MagicMock()

    def _new_display(*_args, **_kwargs):
        # a fresh display (with a real, awaitable adjust_viewport) per grid built, mirroring
        # each grid getting its own EditorPane in production
        display = mocker.MagicMock()
        display.pane.adjust_viewport = mocker.AsyncMock()
        return display

    main_window.build_editor_tab.side_effect = _new_display
    notifs = NvimNotifications(main_window)

    raw_events = []

    def handler(method, params):
        raw_events.append((method, params))
        notifs.handler(method, params)

    iface = NvimInterface(nvim_path, asyncio.get_running_loop(), handler, lambda: None)
    await iface.setup_completed_event.wait()
    try:
        yield iface, notifs, main_window, raw_events
    finally:
        await iface.quit()
        # belt-and-suspenders: make sure nothing lingers even if the graceful quit above didn't
        # actually take (e.g. a test left the buffer in a state Neovim refused to quit from)
        if iface._proc.poll() is None:
            iface._proc.kill()
            iface._proc.wait(timeout=5)


def handle_id(ext_value):
    """Unwrap a decoded ext-type handle into the plain int nysor's own code uses everywhere.

    E.g. ['Window', 5] -> 5 (see nvim_interface.ext_hook). Neovim's API accepts a plain int for
    these handles, but does NOT accept the decoded ['Window', N] form back as a call parameter.
    """
    return ext_value[1]


async def settle(iface):
    """Force any notifications already in flight to be dispatched before inspecting state.

    Neovim may reply to a command before the redraw notifications for that same change have been
    fully read on our end; a cheap extra round-trip guarantees ordering, since Neovim writes bytes
    in the order it produces them -- anything already queued arrives before this reply does.
    """
    await iface.call("nvim_get_current_buf")


def redraw_events(raw_events):
    """Flatten every 'redraw' notification's submethod batches into (submethod, args) pairs."""
    events = []
    for method, params in raw_events:
        if method != "redraw":
            continue
        for batch in params:
            submethod, *args = batch
            events.append((submethod, args))
    return events
