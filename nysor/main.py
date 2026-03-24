# Copyright 2025-2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Main program."""

import argparse
import asyncio
import logging
import sys
import webbrowser

import qasync
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QScrollBar,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QAction

from nysor.logtools import log_notdone, logsetup, LOG_LEVELS
from nysor.nvim_interface import NvimInterface, NeovimExecutableNotFound, NeovimError
from nysor.nvim_notifications import NvimNotifications
from nysor.text_display import TextDisplay

logger = logging.getLogger(__name__)


_NVIM_EXEC_NOT_FOUND_MSG = """\
<b>{exc}</b><br/>
<br/>
Find here how to install Neovim:<br/>
<br/>
&nbsp;&nbsp;&nbsp;&nbsp;<a href="https://neovim.io/doc/install/">https://neovim.io/doc/install/</a><br/>
<br/>
The <span style="font-family:monospace; color:green">nvim</span> executable should be in the system's PATH; alternatively you can indicate the path using the <span style="font-family:monospace; color:green">--nvim</span> parameter.
"""  # NOQA

_ABOUT_TEXT = """
<br/>
<span style="font-size:+1"><b>Nysor</b></span><br/>
<br/>
Yet another graphical interface for Neovim.<br/>
<br/>
Written in Python, with Qt.<br/>
<br/>
<br/>
<small>Copyright 2025-2026 Facundo Batista</small><br/>
"""


class MainMenu:
    """Build and manage the main menu bar for the application."""

    def __init__(self, main_window):
        self._main_window = main_window
        self._menu_bar = main_window.menuBar()
        self.actions = {}

        # FIXME.90 -- for multibuffers we need also a "New" option here
        menu_structure = {
            "&File": [
                ("&Open", "file__open"),
                ("&Save", "file__save"),
                ("&Save as...", "file__save_as"),
                (None, None),
                ("E&xit", "file__exit"),
            ],
            "&Debug": [
                ("Run a blocking call", "debug__blocking_call"),
                ("Run an async task", "debug__async_task"),
            ],
            "&Help": [
                ("Open &project page", "help__open_project_page"),
                ("Create a new &issue", "help__create_issue"),
                (None, None),
                ("&About Nysor", "help__about"),
            ],
        }

        for title, options in menu_structure.items():
            menu = self._menu_bar.addMenu(title)
            for visible_name, name in options:
                if visible_name is None:
                    menu.addSeparator()
                    continue

                action = QAction(visible_name, self._menu_bar)
                action.triggered.connect(getattr(self, f"_on__{name}"))
                self.actions[name] = action
                menu.addAction(action)

    def _log_action(func):
        """Log the action indicate by the user."""
        def _f(self):
            logger.debug("User triggered menu action {!r}", func.__name__)
            return func(self)
        return _f

    @_log_action
    def _on__file__open(self):
        """Open a file."""
        # FIXME.90 -- for multibuffers, logic will change here:
        # - if current buffer has any content, open the new file in a new panel
        # - if current is empty, replace it

        if self._main_window.state_buffer_is_modified:
            dlg = QMessageBox(self._main_window)
            dlg.setIcon(QMessageBox.Icon.Warning)
            dlg.setWindowTitle("Cannot open new file")
            dlg.setText(
                "Current buffer is not saved, cannot open a new file to replace it. "
                "Save the current buffer and try again."
            )
            dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
            dlg.exec()
            return

        # indeed open a new file
        filename, _ = QFileDialog.getOpenFileName(self._main_window, "Open File", "", "")
        if filename:
            self._main_window.open_file(filename)

    @_log_action
    def _on__file__save(self):
        """Save the buffer in the current file."""
        if self._main_window.state_buffer_filepath:
            logger.debug("Saving the buffer directly with current name")
            self._main_window.buffer_save()
        else:
            filename, _ = QFileDialog.getSaveFileName(self._main_window, "Save File", "", "")
            if filename:
                logger.debug("Saving the buffer with new name {!r}", filename)
                self._main_window.save_to_new_file(filename)
            else:
                logger.debug("Saving the buffer cancelled, no new name chosen")

    @_log_action
    def _on__file__save_as(self):
        """Save the buffer in a new file."""
        filename, _ = QFileDialog.getSaveFileName(self._main_window, "Save File", "", "")
        if filename:
            logger.debug("Saving the buffer with new name {!r}", filename)
            self._main_window.save_to_new_file(filename)
        else:
            logger.debug("Saving the buffer cancelled, no new name chosen")

    @_log_action
    def _on__file__exit(self):
        """Exit the application."""
        self._main_window.close_gui()

    @_log_action
    def _on__debug__blocking_call(self):
        """Test an action triggered by the GUI; this is a test/dev helper."""
        logger.info("Code run in a standard function run from the GUI.")

    @_log_action
    def _on__debug__async_task(self):
        """Run an async task; this is a test/dev helper."""

        async def async_task():
            result = await self._main_window.nvi.call("nvim_list_uis")
            logger.info("Code run in an async task, listing UIs from Neovim: {}", result)

        asyncio.create_task(async_task())

    @_log_action
    def _on__help__open_project_page(self):
        """Open the project page in the browser."""
        webbrowser.open("https://github.com/facundobatista/nysor")

    @_log_action
    def _on__help__create_issue(self):
        """Open the issue tracker in the browser."""
        webbrowser.open("https://github.com/facundobatista/nysor/issues/new")

    @_log_action
    def _on__help__about(self):
        """Show the About dialog."""
        msg = _ABOUT_TEXT
        dlg = QMessageBox(self._main_window)
        dlg.setTextFormat(Qt.TextFormat.RichText)
        dlg.setIconPixmap(QIcon("nysor/imgs/icon-1024.png").pixmap(128, 128))
        dlg.setWindowTitle("About Nysor")
        dlg.setText(msg)
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        dlg.exec()


class MainApp(QMainWindow):
    """The main application window."""

    def __init__(self, loop, path_to_open, nvim_exec_path):
        super().__init__()
        self.setWindowIcon(QIcon("nysor/imgs/icon-1024.png"))
        self._menu = MainMenu(self)

        logger.info("Starting Nysor")
        self._closing = 0
        self.state_buffer_is_modified = False
        self.state_buffer_filepath = None
        self.nvim_notifs = NvimNotifications(self)

        # setup the Neovim interface
        try:
            self.nvi = NvimInterface(
                nvim_exec_path, loop, self.nvim_notifs.handler, self._quit_callback
            )
        except NeovimExecutableNotFound as exc:
            logger.error("Failed to start neovim interface: {!r}", exc)
            msg = _NVIM_EXEC_NOT_FOUND_MSG.format(exc=exc)
            dlg = QMessageBox(self)
            dlg.setTextFormat(Qt.TextFormat.RichText)
            dlg.setIcon(QMessageBox.Icon.Critical)
            dlg.setWindowTitle("Startup Error")
            dlg.setText(msg)
            dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
            dlg.exec()
            exit(1)

        loop.create_task(self.setup_nvim(path_to_open))

        # scrollbars
        self.v_scroll = QScrollBar(Qt.Orientation.Vertical)
        self.v_scroll.valueChanged.connect(self.vertical_scroll_changed)
        self.v_scroll.setMinimum(0)
        self.v_scroll.setMaximum(100)
        self.v_scroll_last_position = None
        self.h_scroll = QScrollBar(Qt.Orientation.Horizontal)
        self.h_scroll.valueChanged.connect(self.horizontal_scroll_changed)
        self.h_scroll.setMinimum(0)
        self.h_scroll.setMaximum(100)
        self.h_scroll_last_position = None

        # central widget to hold main layout
        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        # horizontal layout for text display and vertical scroll bar
        hbox = QHBoxLayout()
        self.text_display = self.nvim_notifs.text_display = TextDisplay(self)
        self.text_display.setFocus()
        hbox.addWidget(self.text_display, stretch=1)
        hbox.addWidget(self.v_scroll)
        self.main_layout.addLayout(hbox)

        # rest of vertical widgets
        self.main_layout.addWidget(self.h_scroll)

    def set_buffer_state(self, is_modified=None, filepath=None):
        """Set the state state."""
        # FIXME.90 -- all these needs to evolve to multibuffers
        if is_modified is not None:
            self.state_buffer_is_modified = is_modified
            self._menu.actions["file__save"].setEnabled(is_modified)
            self._menu.actions["file__open"].setEnabled(not is_modified)
        if filepath is not None:
            self.state_buffer_filepath = filepath

    async def setup_nvim(self, path_to_open):
        """Set up Neovim."""
        channel_id, api_metadata = await self.nvi.call("nvim_get_api_info")
        version = api_metadata["version"]
        major = version["major"]
        minor = version["minor"]
        patch = version["patch"]
        logger.info("Neovim API info: version {}.{}.{}", major, minor, patch)

        # attach the UI
        nvim_config = {"ext_linegrid": True}
        await self.nvi.call("nvim_ui_attach", 80, 20, nvim_config)

        # subscribe to the buffer file change (will comeback as a 'set_buffer_state' call)
        # FIXME.90 -- this needs to evolve to multibuffers
        _code = f"""
            vim.api.nvim_create_autocmd({{'BufFilePost'}}, {{
                callback = function()
                    local name = vim.api.nvim_buf_get_name(0)
                    vim.rpcnotify({channel_id}, 'filepath_changed', name)
                end
            }})
        """
        await self.nvi.call("nvim_exec_lua", _code, [])

        # subscribe to the buffer modified change (will comeback as a 'set_buffer_state' call)
        # FIXME.90 -- this needs to evolve to multibuffers
        _code = f"""
            vim.api.nvim_create_autocmd({{'BufModifiedSet', 'BufWritePost'}}, {{
                callback = function()
                    vim.rpcnotify({channel_id}, 'modified_changed', vim.bo.modified)
                end
            }})
        """
        await self.nvi.call("nvim_exec_lua", _code, [])
        self.set_buffer_state(is_modified=False)  # initially it's always not modified

        if path_to_open is not None:
            # FIXME.92: properly support "-" to read from stdin
            cmd = {"cmd": "edit", "args": [path_to_open]}
            opts = {"output": False}  # don't capture output
            try:
                await self.nvi.call("nvim_cmd", cmd, opts)
            except NeovimError as err:
                log_notdone("Got error when opening the file {}, this should never happen", err)

    async def _quit(self):
        """Close the GUI after Neovim is down."""
        logger.debug("Start shutdown, asking")
        error = await self.nvi.quit()
        if error:
            dlg = QMessageBox(self)
            dlg.setIcon(QMessageBox.Icon.Warning)
            dlg.setWindowTitle("Neovim Error")
            dlg.setText(error)
            dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
            dlg.exec()
            self._closing = 0  # reset
            return

        self._closing = 2  # allows final close
        logger.debug("Start shutdown, done")
        self.close()

    def _quit_callback(self):
        """Close the GUI because of nvim interface request."""
        if self._closing == 0:
            # only if it was not initiated internally
            logger.debug("Shutdown requested by nvim interface")
            self._closing = 2
            self.close()

    def close_gui(self, event=None):
        """Close Neovim and then let the rest to finish.

        If event is not None this is the closeEvent (from alt-F4, click in the X, etc.), so
        ignoring/accepting it is the real indication to close the GUI.

        If event is None (called from the menu) we need to call 'close' explicitly.
        """
        logger.debug("Close requested (event={}); current state {:d}", event, self._closing)
        if self._closing == 0:
            # initiate the process to close; ignore the event so GUI is still alive, but
            # start internal procedures
            self._closing = 1
            if event is not None:
                event.ignore()
            asyncio.create_task(self._quit())
            return

        if self._closing == 1:
            # the request was received again in the middle of internal closing, keep ignoring it
            if event is not None:
                event.ignore()
            return

        # we're really done here, let the event propagate if we have it, or close manually
        assert self._closing == 2
        logger.debug("Bye")
        if event is None:
            self.close()
        else:
            event.accept()

    def closeEvent(self, event):
        """Handle the event from the GUI to close itself."""
        self.close_gui(event)

    def present_context_window(self):
        """Present a context window with some options for the user."""
        # FIXME.93
        log_notdone("Mouse context window!")

    async def adjust_viewport(self, topline, botline, line_count, curcol):
        """Adjust scrollbar according to what Neovim says.

        Called from the Neovim layer on viewport changes notifications.
        """
        display_width, display_height = self.text_display.display_size

        # vertical: use information from the viewport
        if topline == 0 and line_count <= display_height:
            self.v_scroll.setEnabled(False)
            self.v_scroll.setMaximum(0)
        else:
            self.v_scroll.setEnabled(True)
            self.v_scroll.setPageStep(display_height)
            self.v_scroll.setMaximum(line_count - 1)
            self.v_scroll_last_position = topline  # before setting value to ignore later event
            self.v_scroll.setValue(topline)

        # only if not wrapping, using current column but also queried line lengths
        # FIXME.90: a "scope" can be given here, revisit this when we have multiple windows
        is_wrapping = await self.nvi.call("nvim_get_option_value", "wrap", {})
        if is_wrapping:
            # just turn off the scroll bar as when wrapping all text will be inside the window
            self.h_scroll.setEnabled(False)
            self.h_scroll.setMaximum(0)
            return

        # get the lengths of lines that are currently shown
        buf = 0  # FIXME.90: why 0? first buffer per window? revisit when multiple windows
        start = topline + 1  # getbufline's first line is 1
        end = botline - 1  # botline is the "next line, out of the view"
        cmd = f"map(getbufline({buf}, {start}, {end}), {{key, val -> strlen(val)}})"
        line_lengths = await self.nvi.call("nvim_eval", cmd)
        if not line_lengths:
            return

        max_line = max(line_lengths)
        if max_line <= display_width:
            self.h_scroll.setEnabled(False)
            self.h_scroll.setMaximum(0)
        else:
            self.h_scroll.setEnabled(True)
            self.h_scroll.setPageStep(display_width)
            self.h_scroll.setMaximum(max_line - 1)

            win_info = await self.nvi.call("nvim_eval", "winsaveview()")
            leftcol = win_info["leftcol"]
            self.h_scroll_last_position = leftcol  # before setting value to ignore later event
            self.h_scroll.setValue(leftcol)

    def vertical_scroll_changed(self, value):
        """Handle the vertical scroll bar being modified through the widget."""
        delta = value - self.v_scroll_last_position
        if delta > 0:
            # down
            cmdkey = "\x05"
        elif delta < 0:
            # up
            cmdkey = "\x19"
        else:
            return
        self.v_scroll_last_position = value
        self.nvi.future_request("nvim_command", f"normal! {abs(delta)}{cmdkey}")

    def horizontal_scroll_changed(self, value):
        """Handle the horizontal scroll bar being modified through the widget."""
        delta = value - self.h_scroll_last_position
        if delta > 0:
            # right
            cmdkey = "zl"
        elif delta < 0:
            # left
            cmdkey = "zh"
        else:
            return
        self.h_scroll_last_position = value
        self.nvi.future_request("nvim_command", f"normal! {abs(delta)}{cmdkey}")

    # -- set of functions to interact with buffers/neovim

    def open_file(self, filename):
        """Tell neovim to open a file and load into current buffer."""
        self.nvi.future_request("nvim_command", f"edit {filename}")

    def buffer_save(self):
        """Save the current buffer."""
        self.nvi.future_request("nvim_command", "write")

    def save_to_new_file(self, filename):
        """Save current buffer to a new filename."""
        self.nvi.future_request("nvim_command", f"saveas {filename}")


def start():
    """Start the application."""
    # mutually exclusive verbosity levels
    parser = argparse.ArgumentParser()
    loggroup = parser.add_mutually_exclusive_group()
    for option, (_, helpmsg) in LOG_LEVELS.items():
        if option:
            loggroup.add_argument(
                f"-{option[0]}",
                f"--{option}",
                action="store_const",
                const=option,
                dest="loglevel",
                help=helpmsg
            )

    # the rest of argument parsing
    parser.add_argument("--nvim", action="store", help="Path to the Neovim executable.")
    parser.add_argument(
        "path", action="store", nargs="?", default=None,
        help="Path to the file to edit or directory to open (optional)"
    )

    # parse arguments
    args = parser.parse_args()

    # setup logging and create the app itself
    logsetup(args.loglevel)
    app = qasync.QApplication(sys.argv)

    # connect with async's event loop
    event_loop = qasync.QEventLoop(app)
    event_loop.set_debug(True)
    asyncio.set_event_loop(event_loop)
    app_close_event = asyncio.Event()
    app.aboutToQuit.connect(app_close_event.set)

    # start and show GUI
    main_window = MainApp(event_loop, args.path, args.nvim)
    main_window.show()

    # go!
    with event_loop:
        event_loop.run_until_complete(app_close_event.wait())
