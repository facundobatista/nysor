# Copyright 2025-2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Main program."""

import argparse
import asyncio
import logging
import sys

import qasync
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollBar,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from nysor.logtools import log_notdone, logsetup, LOG_LEVELS
from nysor.nvim_interface import NvimInterface, NeovimExecutableNotFound
from nysor.nvim_notifications import NvimNotifications
from nysor.text_display import TextDisplay

logger = logging.getLogger(__name__)


_nvim_exec_not_found_msg = """\
<b>{exc}</b><br/>
<br/>
Find here how to install Neovim:<br/>
<br/>
&nbsp;&nbsp;&nbsp;&nbsp;<a href="https://neovim.io/doc/install/">https://neovim.io/doc/install/</a><br/>
<br/>
The <span style="font-family:monospace; color:green">nvim</span> executable should be in the system's PATH; alternatively you can indicate the path using the <span style="font-family:monospace; color:green">--nvim</span> parameter.
"""



class MainApp(QMainWindow):
    """The main application window."""

    def __init__(self, loop, path_to_open, nvim_exec_path):
        super().__init__()
        logger.info("Starting Nysor")
        self._closing = 0
        self.nvim_notifs = NvimNotifications(self)

        # setup the Neovim interface
        try:
            self.nvi = NvimInterface(
                nvim_exec_path, loop, self.nvim_notifs.handler, self._quit_callback
            )
        except NeovimExecutableNotFound as exc:
            logger.error("Failed to start neovim interface: {!r}", exc)
            msg = _nvim_exec_not_found_msg.format(exc=exc)
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

        # FIXME.91: dejar esto para entender que funciona
        self.button = QPushButton("Click here for a blocking call", self)
        self.button.clicked.connect(self.test_action)
        self.main_layout.addWidget(self.button)
        self.button2 = QPushButton("Run async task", self)
        self.button2.clicked.connect(lambda: asyncio.create_task(self.async_task()))
        self.main_layout.addWidget(self.button2)

    async def setup_nvim(self, path_to_open):
        """Set up Neovim."""
        _, api_metadata = await self.nvi.call("nvim_get_api_info")
        version = api_metadata["version"]
        major = version["major"]
        minor = version["minor"]
        patch = version["patch"]
        logger.info("Neovim API info: version {}.{}.{}", major, minor, patch)

        nvim_config = {"ext_linegrid": True}
        await self.nvi.call("nvim_ui_attach", 80, 20, nvim_config)

        if path_to_open is not None:
            # FIXME.92: properly support "-" to read from stdin
            cmd = {"cmd": "edit", "args": [path_to_open]}
            opts = {"output": False}  # don't capture output
            await self.nvi.call("nvim_cmd", cmd, opts)

    def test_action(self):
        """Test an action triggered by the GUI; this is a test/dev helper."""
        print("==== PyQt6 button pressed")
        log_notdone("test msg", foo=3, bar="xxtra")

    async def async_task(self):
        """Run an async task; this is a test/dev helper."""
        result = await self.nvi.call("nvim_list_uis")
        print("=========== Async task! result:", repr(result))

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

    def closeEvent(self, event):
        """Close Neovim and then let the rest to finish.

        The event is ignored so the "real closing" is interrupted, which is triggered later
        when Neovim is already down.
        """
        logger.debug("Close requested; current state {:d}", self._closing)
        if self._closing == 0:
            # start to close; ignore the event so GUI is still alive, but start internal procedures
            self._closing = 1
            event.ignore()
            asyncio.create_task(self._quit())
            return

        if self._closing == 1:
            # the event was received again in the middle of internal closing, keep ignoring it;
            event.ignore()
            return

        # we're really done here, let the event propagate so
        assert self._closing == 2
        logger.debug("Bye")
        event.accept()

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
