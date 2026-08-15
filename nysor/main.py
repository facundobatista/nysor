# Copyright 2025-2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Main program."""

import argparse
import asyncio
import logging
import os
import platform
import subprocess
import sys
import tempfile
import webbrowser
from importlib.metadata import version, PackageNotFoundError
from urllib.parse import urlencode

import qasync
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollBar,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QIcon, QAction


from nysor import swarm
from nysor.logtools import log_notdone, logsetup, LOG_LEVELS
from nysor.nvim_interface import NvimInterface, NeovimExecutableNotFound, NeovimError
from nysor.nvim_notifications import NvimNotifications
from nysor.text_display import TextDisplay, MIN_COLS_ROWS
from nysor.utils import call_async

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
<span style="font-size:+1"><b>Nysor</b> {version}</span><br/>
<br/>
Yet another graphical interface for Neovim.<br/>
<br/>
Written in Python, with Qt.<br/>
<br/>
<br/>
<small>Copyright 2025-2026 Facundo Batista</small><br/>
"""

_NEW_ISSUE_TEXT = """\
{description}

---
Automatically Included System Information:
```
{info}
```
"""

# the special path that indicates to read from stdin
SPECIAL_STDIN_PATH = "-"


def get_nysor_version():
    """Return the Nysor version, from the installed metadata, or fallback to git."""
    try:
        return version("nysor")
    except PackageNotFoundError:
        # package not installed, most probably being run from the project, fallback to git
        pass

    cmd = ["git", "describe", "--tags"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if proc.returncode == 0:
        git_version = proc.stdout.decode().strip()
        return f"(git) {git_version}"

    return "unknown"


def get_system_info():
    """Return versions of system."""
    info = {}

    # Python
    info["Python Version"] = sys.version
    info["Python Executable"] = sys.executable

    # OS
    info["Platform"] = platform.platform()
    info["System"] = platform.system()
    info["Release"] = platform.release()

    # Desktop / entorno gráfico
    info["Desktop"] = os.environ.get("XDG_CURRENT_DESKTOP", "unknown")
    info["Wayland"] = os.environ.get("WAYLAND_DISPLAY", "none")
    info["Display"] = os.environ.get("DISPLAY", "none")

    return info


def open_new_issue_page(title, description):
    """Open a new issue page in the browser with all the info prefilled."""
    # build body
    parts = [f"{k}: {v}" for k, v in get_system_info().items()]
    info = "\n".join(parts) + "\n"
    body = _NEW_ISSUE_TEXT.format(description=description, info=info)

    # build url
    base = "https://github.com/facundobatista/nysor/issues/new"
    params = {
        "title": title,
        "body": body,
        "labels": "auto"
    }
    url = base + "?" + urlencode(params)

    # open
    webbrowser.open(url)


class CreateIssueDialog(QDialog):
    """Dialog for user to input information to create an issue."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Report an Issue")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Create a new issue on GitHub..."))

        layout.addWidget(QLabel("Title"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Add a title")
        self.title_edit.textChanged.connect(self._update_create_button)
        layout.addWidget(self.title_edit)

        layout.addWidget(QLabel("Description"))
        self.descrip_edit = QTextEdit()
        self.descrip_edit.setPlaceholderText("Add a description")
        self.descrip_edit.setMinimumHeight(150)
        self.descrip_edit.textChanged.connect(self._update_create_button)
        layout.addWidget(self.descrip_edit)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_button = QPushButton("Cancel")
        cancel_button.setDefault(True)
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)

        self.create_button = QPushButton("Create issue in GitHub")
        self.create_button.clicked.connect(self.accept)
        self.create_button.setEnabled(False)
        button_layout.addWidget(self.create_button)

        layout.addLayout(button_layout)

    def _update_create_button(self):
        """Enable or disable the create button according to the provided values."""
        has_values = all(v for v in self.get_values().values())
        self.create_button.setEnabled(has_values)

    def get_values(self):
        """Return values from user inputs."""
        return {
            "title": self.title_edit.text().strip(),
            "description": self.descrip_edit.toPlainText().strip(),
        }


class MainMenu:
    """Build and manage the main menu bar for the application."""

    def __init__(self, main_window):
        self._main_window = main_window
        self._menu_bar = main_window.menuBar()
        self.actions = {}

        menu_structure = {
            "&File": [
                ("&New", "file__new"),
                ("&Open", "file__open"),
                ("&Save", "file__save"),
                ("&Save as...", "file__save_as"),
                (None, None),
                ("&Close Tab", "file__close_tab"),
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
    def _on__file__new(self):
        """Open a new empty tab."""
        self._main_window.new_file()

    @_log_action
    def _on__file__open(self):
        """Open a file (in a new tab; see MainApp.open_file)."""
        self._main_window.open_file_dialog()

    @_log_action
    def _on__file__save(self):
        """Save the active buffer, asking for a name if it does not have one yet."""
        if self._main_window.active_filepath():
            logger.debug("Saving the buffer directly with current name")
            self._main_window.buffer_save()
        else:
            self._main_window.save_active_as()

    @_log_action
    def _on__file__save_as(self):
        """Save the active buffer to a new file."""
        self._main_window.save_active_as()

    @_log_action
    def _on__file__close_tab(self):
        """Close the currently active tab (its Neovim window)."""
        call_async(self._main_window.close_active_tab)

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
        dialog = CreateIssueDialog(self._main_window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.get_values()
        open_new_issue_page(values["title"], values["description"])

    @_log_action
    def _on__help__about(self):
        """Show the About dialog."""
        msg = _ABOUT_TEXT.format(version=self._main_window.nysor_version)
        dlg = QMessageBox(self._main_window)
        dlg.setTextFormat(Qt.TextFormat.RichText)
        dlg.setIconPixmap(QIcon("nysor/imgs/icon-1024.png").pixmap(128, 128))
        dlg.setWindowTitle("About Nysor")
        dlg.setText(msg)
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        dlg.exec()


class EditorPane(QWidget):
    """A tab page: one editor TextDisplay with its own vertical/horizontal scroll bars.

    Each Neovim window (tabpage) gets its own pane, so scroll positions are independent and
    preserved across tab switches.
    """

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        # pass ourselves as the pane so the display always has its back-reference set correctly
        self.text_display = TextDisplay(main_window, pane=self)
        self.closed = False  # set when the tab is removed, so in-flight async work bails out
        # identity (window/buffer/filepath) lives in the GridRegistry; the pane only keeps the
        # transient modified flag that its tab label shows
        self.modified = False

        self.v_scroll = QScrollBar(Qt.Orientation.Vertical)
        self.v_scroll.setMinimum(0)
        self.v_scroll.setMaximum(100)
        self.v_scroll.valueChanged.connect(self.vertical_scroll_changed)
        self.v_scroll_last_position = None

        self.h_scroll = QScrollBar(Qt.Orientation.Horizontal)
        self.h_scroll.setMinimum(0)
        self.h_scroll.setMaximum(100)
        self.h_scroll.valueChanged.connect(self.horizontal_scroll_changed)
        self.h_scroll_last_position = None

        layout = QVBoxLayout(self)
        hbox = QHBoxLayout()
        hbox.addWidget(self.text_display, stretch=1)
        hbox.addWidget(self.v_scroll)
        layout.addLayout(hbox)
        layout.addWidget(self.h_scroll)

    async def adjust_viewport(self, topline, botline, line_count, curcol):
        """Adjust this pane's scroll bars according to what Neovim says.

        Called from the Neovim layer on this window's viewport change notifications. This is
        async (it queries Neovim), so the tab may be closed while it runs; we bail out at each
        step if the pane was destroyed, to not touch already-deleted Qt widgets.
        """
        if self.closed:
            return
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
        is_wrapping = await self.main_window.nvi.call("nvim_get_option_value", "wrap", {})
        if self.closed:
            return
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
        line_lengths = await self.main_window.nvi.call("nvim_eval", cmd)
        if self.closed or not line_lengths:
            return

        max_line = max(line_lengths)
        if max_line <= display_width:
            self.h_scroll.setEnabled(False)
            self.h_scroll.setMaximum(0)
        else:
            self.h_scroll.setEnabled(True)
            self.h_scroll.setPageStep(display_width)
            self.h_scroll.setMaximum(max_line - 1)

            win_info = await self.main_window.nvi.call("nvim_eval", "winsaveview()")
            if self.closed:
                return
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
        self.main_window.nvi.future_request("nvim_command", f"normal! {abs(delta)}{cmdkey}")

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
        self.main_window.nvi.future_request("nvim_command", f"normal! {abs(delta)}{cmdkey}")


class DetachedWindow(QMainWindow):
    """An editor pane pulled out of the tab strip into its own OS window.

    This is a purely-Qt move: the EditorPane is reparented here, no Neovim windows/buffers change.
    Because Neovim (under multigrid) only draws the current tabpage, only the focused editor is
    live; the others (tabs or detached windows) show their last content until focused again.
    Focusing this window makes its pane the active one (via the same win_gotoid used for tabs);
    closing it re-attaches the pane as a tab (it does NOT close the buffer -- ':q' does that).
    """

    def __init__(self, app, pane):
        super().__init__()
        self._app = app
        self._pane = pane
        self.setCentralWidget(pane)
        pane.show()  # removeTab hid the pane; setCentralWidget does not re-show it on its own

    def changeEvent(self, event):
        """When this window gains focus, make its pane the active editor."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._app.activate_pane(self._pane)

    def closeEvent(self, event):
        """Re-attach the pane as a tab instead of destroying it (unless the app is quitting)."""
        if self._app.is_closing():
            event.accept()  # the whole app is going down; let this window close
            return
        event.ignore()
        self._app.reattach_pane(self._pane)


class MainApp(QMainWindow):
    """The main application window."""

    def __init__(self, version, loop, paths_to_open, nvim_exec_path):
        super().__init__()
        self.setWindowIcon(QIcon("nysor/imgs/icon-1024.png"))
        self._menu = MainMenu(self)
        self.nysor_version = version

        self._closing = 0
        self.state_buffer_is_modified = False
        # a pane kept aside while its buffer is reopened, to re-attach to the new window
        self._reattach_pending = None
        # panes pulled out of the tab strip into their own OS window (pane -> DetachedWindow)
        self._detached = {}
        # set while detaching so removing the tab does not bounce activation to a sibling tab
        self._suppress_tab_activation = False
        self.nvim_notifs = NvimNotifications(self)
        self.grids = self.nvim_notifs.grids  # the central registry (single source of truth)

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

        self._swarm = swarm.SwarmServer(loop, self._path_discover_cb)
        loop.create_task(self.setup_nvim(paths_to_open))

        # central widget to hold main layout
        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        # the editors live under a tab system: one tab (an EditorPane, with its own scroll bars)
        # per Neovim window. Panes are created on demand; the first is pre-created here and
        # claimed by the first window. 'text_display' tracks the active editor display.
        self.tabs = QTabWidget()
        self.main_layout.addWidget(self.tabs, stretch=1)

        self._unclaimed_pane = EditorPane(self)
        self.tabs.addTab(self._unclaimed_pane, "[No Name]")
        self.text_display = self._unclaimed_pane.text_display

        # switching a tab from the GUI must move Neovim (see _on_tab_changed)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # right-clicking a tab offers per-tab File actions (see _show_tab_menu); the tab bar only
        # spans the tabs themselves, so right-clicking the empty part of the header row reaches the
        # QTabWidget instead and offers New/Open (see _show_new_open_menu)
        tab_bar = self.tabs.tabBar()
        tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(self._show_tab_menu)
        self.tabs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self._show_new_open_menu)

        # editor-wide font and cursor mode: owned here (the editor layer), remembered so tabs
        # created later start with the right values
        self._editor_font = None
        self._editor_mode = None

        # the statusline strip: a display-only view of the global grid's status row(s) (mode,
        # file, position, etc.), which live on grid 1 under multigrid; sits above the messages
        self.statusline_display = self.nvim_notifs.statusline_display = TextDisplay(
            self, interactive=False
        )
        # fixed size: the strip is exactly as wide as its grid (Neovim's reported width),
        # left-aligned so it lines up with the editor text above
        self.statusline_display.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self.statusline_display.resize_view((self.text_display.display_size[0], 1))
        self.main_layout.addWidget(self.statusline_display, alignment=Qt.AlignmentFlag.AlignLeft)

        # the message strip: a display-only view of Neovim's message grid, always visible at
        # the bottom of the window; it never takes focus nor mouse input, and its height is
        # driven by Neovim (grows when a message spans several lines)
        self.message_display = self.nvim_notifs.message_display = TextDisplay(
            self, interactive=False
        )
        self.message_display.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        # start as a single line, as wide as the editor display; Neovim resizes it right away
        self.message_display.resize_view((self.text_display.display_size[0], 1))
        self.main_layout.addWidget(self.message_display, alignment=Qt.AlignmentFlag.AlignLeft)

        self.text_display.setFocus()

    def _sync_menu_state(self):
        """Enable/disable the file menu entries according to the active tab's modified state."""
        is_modified = self.text_display.pane.modified if self.text_display is not None else False
        self.state_buffer_is_modified = is_modified  # keeps the (still global) menu logic working
        self._menu.actions["file__save"].setEnabled(is_modified)
        self._menu.actions["file__open"].setEnabled(not is_modified)

    def _active_entry(self):
        """Return the GridEntry backing the active tab, or None."""
        if self.text_display is None:
            return None
        grid = self.grids.get_grid_by_pane(self.text_display.pane)
        return self.grids.get_entry_by_grid(grid)

    def active_filepath(self):
        """Return the filepath shown in the active tab, or None."""
        entry = self._active_entry()
        return entry.filepath if entry is not None else None

    def _refresh_main_title(self):
        """Set the main window title to its CURRENT tab's filepath.

        It follows the shown tab, not the globally-active editor: while you work in a detached
        window, the main window keeps showing (and titling) its own current tab.
        """
        pane = self.tabs.currentWidget()
        grid = self.grids.get_grid_by_pane(pane) if pane is not None else None
        entry = self.grids.get_entry_by_grid(grid)
        path = entry.filepath if entry is not None else None
        self.setWindowTitle(path or "Nysor")

    def _refresh_tab_label(self, grid_id):
        """Rebuild a window grid's label (tab text or detached-window title) from its filepath."""
        entry = self.grids.get_entry_by_grid(grid_id)
        if entry is None:
            logger.warning("_refresh_tab_label for a grid with no entry: {}", grid_id)
            return
        name = os.path.basename(entry.filepath) if entry.filepath else "[No Name]"
        if entry.pane.modified:
            name = f"● {name}"
        window = self._detached.get(entry.pane)
        if window is not None:
            window.setWindowTitle(name)
            return
        index = self.tabs.indexOf(entry.pane)
        if index != -1:
            self.tabs.setTabText(index, name)
        self._refresh_main_title()  # the current tab's filepath may have changed

    def refresh_tab(self, grid_id):
        """Update a window grid's tab after its buffer/filepath changed, enforcing one-per-buffer.

        One-buffer-one-tab (the in-process analogue of the swarm's cross-process path check): if
        another grid already owns this buffer, collapse this duplicate window into it -- switch to
        the existing tab and close the redundant Neovim window.
        """
        self._refresh_tab_label(grid_id)
        entry = self.grids.get_entry_by_grid(grid_id)
        if entry is None or entry.bufnr is None:
            return
        owner = self.grids.get_grid_by_buffer(entry.bufnr)
        if owner is not None and owner != grid_id:
            self._collapse_duplicate(grid_id, owner)

    def _collapse_duplicate(self, dup_grid, existing_grid):
        """Go to the tab that already owns the buffer and close the redundant window."""
        dup = self.grids.get_entry_by_grid(dup_grid)
        existing = self.grids.get_entry_by_grid(existing_grid)
        # mark the duplicate's pane as closing right away, so switching to the existing tab (which
        # may fire its own refresh) does not try to collapse in the other direction
        dup.pane.closed = True
        if existing is not None and existing.win_id is not None:
            self.nvi.future_request("nvim_call_function", "win_gotoid", [existing.win_id])
        if dup.win_id is not None:
            self.nvi.future_request("nvim_win_close", dup.win_id, False)

    def set_tab_modified(self, grid_id, modified):
        """Set a window grid's modified state (label marker; menu if it is the active tab)."""
        entry = self.grids.get_entry_by_grid(grid_id)
        if entry is None:
            return
        entry.pane.modified = modified
        self._refresh_tab_label(grid_id)
        if entry.pane.text_display is self.text_display:
            self._sync_menu_state()

    def _editor_displays(self):
        """Return every editor display, whether held in a tab or in a detached window."""
        tabbed = [self.tabs.widget(i).text_display for i in range(self.tabs.count())]
        detached = [pane.text_display for pane in self._detached]
        return tabbed + detached

    def set_editor_font(self, name, size):
        """Set the font for all editor displays and the strips, and remember it for new tabs."""
        self._editor_font = (name, size)
        for display in self._editor_displays():
            display.set_font(name, size)
        self.message_display.set_font(name, size)
        self.statusline_display.set_font(name, size)

    def set_editor_mode(self, mode_info):
        """Set the cursor mode for the ACTIVE editor, and remember it for new tabs.

        The mode (e.g. insert -> a bar cursor) belongs to Neovim's current window; applying it to
        every display would change the cursor in the inactive/frozen editors too. New tabs pick up
        the remembered mode on build; a window picks it up again when it becomes active.
        """
        self._editor_mode = mode_info
        if self.text_display is not None:
            self.text_display.change_mode(mode_info)

    def build_editor_tab(self):
        """Create (or reuse) the editor pane for a new window, returning its display.

        If a pane is waiting to be re-attached (its window was reopened after aborting a close),
        reuse it so the same tab keeps its place and state. Otherwise reuse the initial tab or
        make a new one.
        """
        if self._reattach_pending is not None:
            pane = self._reattach_pending
            self._reattach_pending = None
            return pane.text_display
        if self._unclaimed_pane is not None:
            pane = self._unclaimed_pane
            self._unclaimed_pane = None
        else:
            pane = EditorPane(self)
            self.tabs.addTab(pane, "[No Name]")
        # a freshly created display starts with the current editor-wide font and cursor mode
        if self._editor_font is not None:
            pane.text_display.set_font(*self._editor_font)
        if self._editor_mode is not None:
            pane.text_display.change_mode(self._editor_mode)
        return pane.text_display

    def destroy_editor_tab(self, pane):
        """Remove the given editor pane, whether it lives in a tab or a detached window."""
        pane.closed = True  # in-flight async work (e.g. adjust_viewport) must bail out
        window = self._detached.pop(pane, None)
        if window is not None:
            window.deleteLater()  # deletes the detached window together with its child pane
        else:
            index = self.tabs.indexOf(pane)
            if index != -1:
                self.tabs.removeTab(index)
            pane.deleteLater()
        if self.text_display is pane.text_display:
            current = self.tabs.currentWidget()
            self.text_display = current.text_display if current is not None else None

    def on_editor_window_closed(self, entry):
        """React to Neovim closing a window (e.g. ':close'), keeping tab<->buffer consistent.

        `entry` is the (already-forgotten) GridEntry of the closed window. As a GUI we do not
        honor ':close' literally (which would just hide the buffer). Instead:
        - if that buffer is still owned by another (live) tab -- i.e. we are cleaning up a
          duplicate we just collapsed -- drop this tab without touching the buffer;
        - if it has no unsaved changes, close the tab and delete the buffer in Neovim;
        - if it has unsaved changes, we can't yet tell a plain ':close'/':q' (which just hides
          the still-modified buffer) from a forced ':q!' (which discards the changes), because a
          forced quit emits no 'modified_changed', so our cached pane.modified stays stale. Ask
          Neovim for the buffer's real state and decide there (see _resolve_modified_close).
        While the app itself is quitting we only drop the tab (qall already prompts).
        """
        pane = entry.pane
        bufnr = entry.bufnr
        # the grid was already forgotten, so another owner means the buffer lives in another tab
        shown_elsewhere = bufnr is not None and self.grids.get_grid_by_buffer(bufnr) is not None
        if self._closing != 0 or bufnr is None or shown_elsewhere:
            self.destroy_editor_tab(pane)
            return

        if not pane.modified:
            self.destroy_editor_tab(pane)
            self.nvi.future_request("nvim_buf_delete", bufnr, {})
            return

        call_async(self._resolve_modified_close, pane, bufnr, entry.filepath)

    async def _resolve_modified_close(self, pane, bufnr, filepath):
        """Decide what to do with a closed window whose buffer we still think is modified.

        Our cached modified flag can't distinguish ':close'/':q' (the buffer stays loaded and
        changed) from ':q!' (the changes are discarded and the buffer is unloaded), so we read the
        buffer's real 'changed' state from Neovim:
        - still changed  -> genuine ':close': abort by re-showing the buffer and re-attaching this
          same tab, then tell the user to save first;
        - not changed    -> the changes were discarded (':q!') or already saved: honor it, close
          the tab and delete the (now dangling) buffer.
        """
        info = await self.nvi.call("nvim_call_function", "getbufinfo", [bufnr])
        still_modified = bool(info) and info[0].get("changed")
        if still_modified:
            self._reattach_pending = pane
            self.nvi.future_request("nvim_command", f"tab sbuffer {bufnr}")
            self._show_close_aborted(filepath)
        else:
            self.destroy_editor_tab(pane)
            if info:  # buffer still lingers (unloaded but listed) -> clean it up
                self.nvi.future_request("nvim_buf_delete", bufnr, {})

    def _show_close_aborted(self, filepath):
        """Tell the user we kept a tab open because its buffer had unsaved changes."""
        name = os.path.basename(filepath) if filepath else "[No Name]"
        dlg = QMessageBox(self)
        dlg.setIcon(QMessageBox.Icon.Information)
        dlg.setWindowTitle("Close aborted")
        dlg.setText(
            f"{name!r} has unsaved changes, so its tab was kept open.\n"
            "Save it (or use a Neovim command like :w / :q!) before closing."
        )
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        dlg.open()  # non-blocking; just informational

    async def close_active_tab(self):
        """Close the active tab (see close_tab)."""
        await self.close_tab(self._active_entry())

    async def close_tab(self, entry):
        """Close a tab from the GUI, prompting if its buffer has unsaved changes.

        Closing the last remaining pane (counting detached windows) is really "quit the app", so it
        is routed to close_gui (which drives ':qall', with its own unsaved-changes prompt).
        Otherwise we close just this pane's Neovim window; the actual teardown happens in
        on_editor_window_closed when the resulting 'grid_destroy' arrives.
        """
        if self.tabs.count() + len(self._detached) <= 1:
            self.close_gui()
            return
        if entry is None or entry.win_id is None:
            logger.warning("close_tab with no known Neovim window for the tab")
            return

        if entry.pane.modified:
            choice = await self._ask_close_modified(entry.filepath)
            if choice == "cancel":
                return
            if choice == "save":
                # write this window's buffer first, then close it (order is preserved: the write
                # request is sent before the close one)
                self.nvi.future_request(
                    "nvim_call_function", "win_execute", [entry.win_id, "write"])
            elif choice == "discard":
                # run ':q!' in this window to drop the unsaved changes and close it (note
                # nvim_win_close(force=True) would NOT discard: it just hides the buffer, still
                # changed). The resulting grid_destroy tears the tab down.
                self.nvi.future_request(
                    "nvim_call_function", "win_execute", [entry.win_id, "q!"])
                return
        self.nvi.future_request("nvim_win_close", entry.win_id, False)

    async def _ask_close_modified(self, filepath):
        """Ask the user how to close a tab with unsaved changes; return save/discard/cancel."""
        name = os.path.basename(filepath) if filepath else "[No Name]"
        dlg = QMessageBox(self)
        dlg.setIcon(QMessageBox.Icon.Warning)
        dlg.setWindowTitle("Unsaved changes")
        dlg.setText(f"{name!r} has unsaved changes.")
        dlg.setInformativeText("Save it before closing the tab?")
        save_btn = dlg.addButton(QMessageBox.StandardButton.Save)
        discard_btn = dlg.addButton(QMessageBox.StandardButton.Discard)
        dlg.addButton(QMessageBox.StandardButton.Cancel)
        dlg.setDefaultButton(save_btn)

        answered = asyncio.Event()
        dlg.finished.connect(lambda _result: answered.set())
        dlg.open()  # non-blocking, so the async loop keeps running while the user decides
        await answered.wait()

        clicked = dlg.clickedButton()
        if clicked is save_btn:
            return "save"
        if clicked is discard_btn:
            return "discard"
        return "cancel"

    def _show_new_open_menu(self, pos):
        """Show a New/Open context menu when the empty part of the tab-bar row is right-clicked.

        This handler is on the whole QTabWidget, so it also fires for right-clicks on the page
        area (the editor); we ignore those (only the header row, above the pages, is ours) and let
        the editor handle its own. `pos` is in the QTabWidget's coordinates.
        """
        if pos.y() > self.tabs.tabBar().height():
            return  # below the tab-bar row -> the editor area, not our menu

        menu = QMenu(self)
        new_act = menu.addAction("New")
        open_act = menu.addAction("Open...")
        chosen = menu.exec(self.tabs.mapToGlobal(pos))

        if chosen is new_act:
            self.new_file()
        elif chosen is open_act:
            self.open_file_dialog()

    def _show_tab_menu(self, pos):
        """Show the per-tab context menu (Save / Save As / Close) for the right-clicked tab."""
        tab_bar = self.tabs.tabBar()
        index = tab_bar.tabAt(pos)
        if index == -1:
            return
        entry = self.grids.get_entry_by_grid(self.grids.get_grid_by_pane(self.tabs.widget(index)))

        menu = QMenu(self)
        save_act = menu.addAction("Save")
        save_act.setEnabled(entry is not None and entry.pane.modified)
        save_as_act = menu.addAction("Save As...")
        save_as_act.setEnabled(entry is not None)
        menu.addSeparator()
        detach_act = menu.addAction("Detach")
        detach_act.setEnabled(entry is not None)
        close_act = menu.addAction("Close")
        chosen = menu.exec(tab_bar.mapToGlobal(pos))

        if chosen is save_act:
            self._save_tab(entry)
        elif chosen is save_as_act:
            self._save_entry_as(entry)
        elif chosen is detach_act:
            self.detach_tab(entry)
        elif chosen is close_act:
            call_async(self.close_tab, entry)

    def save_active_as(self):
        """Save the active buffer to a new filename (menu-bar Save As / Save on an unnamed one)."""
        self._save_entry_as(self._active_entry())

    def _save_tab(self, entry):
        """Write the given tab's buffer; if it has no filename yet, ask for one."""
        if entry is None or entry.win_id is None:
            return
        if entry.filepath:
            self.nvi.future_request("nvim_call_function", "win_execute", [entry.win_id, "write"])
        else:
            self._save_entry_as(entry)

    def _save_entry_as(self, entry):
        """Save a tab's buffer to a user-chosen filename.

        QFileDialog already confirms overwriting an existing file, so a returned filename means
        the user picked a fresh name or approved the overwrite; we then write with 'saveas!' (the
        bang only stops Neovim from refusing the -already approved- existing file).
        """
        if entry is None or entry.win_id is None:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Save File", "", "")
        if not filename:
            return
        # run 'saveas!' in that window's context (it may not be the active one); vim.cmd.saveas
        # lets Neovim escape the path, and the bang overwrites the file the dialog confirmed
        _code = """
            local win, fname = ...
            vim.api.nvim_win_call(win, function()
                vim.cmd.saveas({args = {fname}, bang = true})
            end)
        """
        self.nvi.future_request("nvim_exec_lua", _code, [entry.win_id, filename])

    def set_active_editor(self, grid_id):
        """Bring the given window grid's pane to the front and focus it (Neovim -> GUI).

        The pane may live in a tab (select it) or in a detached window (raise it). text_display is
        updated *before* bringing it forward, so the activation this triggers is recognized as
        already-active by activate_pane and does not bounce back to Neovim.
        """
        entry = self.grids.get_entry_by_grid(grid_id)
        if entry is None:
            logger.warning("set_active_editor for a grid with no entry: {}", grid_id)
            return
        pane = entry.pane
        self.text_display = pane.text_display
        window = self._detached.get(pane)
        if window is not None:
            window.raise_()
            window.activateWindow()
        else:
            index = self.tabs.indexOf(pane)
            if index == -1:
                logger.warning("set_active_editor for a pane that is neither a tab nor detached")
                return
            if self.tabs.currentIndex() != index:  # not an error: may already be current
                self.tabs.setCurrentIndex(index)
        pane.text_display.setFocus()
        self._sync_menu_state()  # the file menu follows the newly active editor
        # pin its grid to its current on-screen size (a freshly shown/activated window may not emit
        # a resizeEvent, e.g. the very first window or an unchanged geometry on tab switch)
        self.resize_editor_grid(pane.text_display)

    def activate_pane(self, pane):
        """Make the given pane the active editor by switching Neovim to its window (GUI -> Neovim).

        Triggered when the user focuses a pane -- clicking a tab (see _on_tab_changed) or a
        detached window (DetachedWindow.changeEvent). Neovim-driven switches call in here too, but
        by then the pane is already the active one, so we detect that and do not bounce back.
        """
        if self.text_display is not None and pane is self.text_display.pane:
            return  # already active; nothing to send back to Neovim
        grid = self.grids.get_grid_by_pane(pane)
        entry = self.grids.get_entry_by_grid(grid)
        if entry is None or entry.win_id is None:
            # a focusable pane should always have its Neovim window known by now
            logger.warning("focused a pane with no known Neovim window")
            return
        # win_gotoid takes the plain window id and switches window (and its tabpage)
        self.nvi.future_request("nvim_call_function", "win_gotoid", [entry.win_id])

    def _on_tab_changed(self, index):
        """When the user switches tab in the GUI, make that tab's pane the active editor."""
        self._refresh_main_title()  # the shown tab changed -> retitle the main window
        if self._suppress_tab_activation:
            return  # a detach is shuffling tabs; the detached pane stays the active one
        pane = self.tabs.widget(index)
        if pane is None or self.text_display is None:
            return  # no current tab / no active display: only while tearing down, nothing to do
        self.activate_pane(pane)

    def detach_tab(self, entry):
        """Pull a tab's editor pane out into its own OS window (see DetachedWindow)."""
        if entry is None:
            return
        pane = entry.pane
        if pane in self._detached:
            return  # already detached
        index = self.tabs.indexOf(pane)
        if index == -1:
            return
        # keep this pane the active one: suppress the activation that removing its tab would bounce
        # to a sibling (if it was the active tab, Neovim should stay on it, now in its own window)
        self._suppress_tab_activation = True
        self.tabs.removeTab(index)  # reparents the pane out; may switch the current tab
        self._suppress_tab_activation = False
        window = DetachedWindow(self, pane)  # setCentralWidget reparents the pane into it
        self._detached[pane] = window
        window.resize(self.size())  # roughly the main window size, so the grid is not clipped
        self._refresh_tab_label(entry.grid_id)  # now routes to the detached window's title
        window.show()
        window.raise_()
        window.activateWindow()  # focusing it activates the pane (via changeEvent -> win_gotoid)
        # pin the grid to the detached window size now (it was pinned to the smaller tab area, and
        # detaching the ACTIVE tab keeps it active, so no set_active_editor fires to re-pin it)
        self.resize_editor_grid(pane.text_display)

    def reattach_pane(self, pane):
        """Move a detached pane back into the tab strip and drop its window."""
        window = self._detached.pop(pane, None)
        if window is None:
            return
        window.takeCentralWidget()  # release the pane from the window without deleting it
        index = self.tabs.addTab(pane, "[No Name]")
        window.deleteLater()
        grid = self.grids.get_grid_by_pane(pane)
        if grid is not None:
            self._refresh_tab_label(grid)  # restore the proper tab label and modified marker
        self.tabs.setCurrentIndex(index)  # bring it to front (activates it via _on_tab_changed)

    def resizeEvent(self, event):
        """Resize Neovim's global grid to the whole editing area on a window resize."""
        super().resizeEvent(event)
        self._resize_global_grid()

    def changeEvent(self, event):
        """React to the main window regaining focus: reactivate its tab and re-check its file."""
        super().changeEvent(event)
        if self.is_closing():
            return  # shutting down: closing windows shifts focus around; don't react to it
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            # returning to the main window (e.g. from a detached one) makes its current tab the
            # active editor again, so keyboard/scroll go to the right Neovim window
            pane = self.tabs.currentWidget()
            if pane is not None:
                self.activate_pane(pane)
            # only check the buffer the user is actually looking at (the active tab == Neovim's
            # current buffer); a change to some other tab's file is caught when they switch to it
            # (the BufEnter autocmd). checktime lets Neovim prompt (load / ignore / ...); fire-and-
            # forget so we never block on that prompt
            _code = "vim.cmd.checktime({ args = { tostring(vim.api.nvim_get_current_buf()) } })"
            self.nvi.future_request("nvim_exec_lua", _code, [])

    def _resize_global_grid(self):
        """Tell Neovim the global grid size, computed from the full editing area.

        This is driven by the window size (not the editor tab's size), so a strip growing or
        shrinking a tab never feeds back into a resize (which used to loop, e.g. on a swap-file
        prompt). Neovim carves the statusline/message rows out of this global size itself.

        It uses the main window's tab area (not the active display, which may be a detached window)
        so detaching/activating a floating window never changes the global grid size.
        """
        display = self.tabs.currentWidget()
        display = display.text_display if display is not None else None
        if display is None or display.font_size is None:
            return
        font_size = display.font_size
        # width from the editor's text area (excludes the scroll bar); height from the whole
        # area (so a strip growing/shrinking never changes the size we send -> no resize loop)
        cols = max(MIN_COLS_ROWS, int(display.width() / font_size.width))
        rows = max(MIN_COLS_ROWS, int(self.central_widget.height() / font_size.height))
        self.nvi.future_request("nvim_ui_try_resize", cols, rows)

    def resize_editor_grid(self, display):
        """Resize the active editor's Neovim window grid to match its display's on-screen size.

        Called from the display's resizeEvent (and on activation). Each pane drives its own grid,
        so a docked tab and a detached window get their real sizes in Neovim independently
        (try_resize_grid is sticky and does not disturb the other grids nor the global one).

        Only the ACTIVE display is resized: its window is the current tabpage's, the only one that
        Neovim lets us resize (try_resize_grid on a window in a hidden tabpage errors 'Invalid
        window handle'). The others re-pin when they become active (see set_active_editor).
        """
        if display is not self.text_display:
            return  # only the active window's tabpage is current; Neovim rejects resizing the rest
        if display.pane is None or display.font_size is None:
            return
        grid = self.grids.get_grid_by_pane(display.pane)
        if grid is None:
            return  # the window is not wired to a Neovim grid yet
        font_size = display.font_size
        cols = max(MIN_COLS_ROWS, int(display.width() / font_size.width))
        rows = max(MIN_COLS_ROWS, int(display.height() / font_size.height))
        if (cols, rows) == display._last_grid_size:
            return  # nothing changed at grid granularity; skip the redundant resize
        display._last_grid_size = (cols, rows)
        self.nvi.future_request("nvim_ui_try_resize_grid", grid, cols, rows)

    def _path_discover_cb(self, path):
        """Indicate if the given path is opened here (in any tab) and claim GUI attention if so."""
        is_here = self.grids.has_path(path)
        if is_here:
            # we have somebody else's path, claim attention!
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.show()
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
            self.show()
        return is_here

    async def setup_nvim(self, paths_to_open):
        """Set up Neovim from the GUI PoV.

        This comes in tandem with the Neovim internal setup, so the first thing we do is to
        wait that one to finish.
        """
        await self.nvi.setup_completed_event.wait()

        # attach the UI; multigrid gives each Neovim window (and the message area) its own
        # grid, which we route to separate displays (see NvimNotifications)
        nvim_config = {"ext_linegrid": True, "ext_multigrid": True}
        await self.nvi.call("nvim_ui_attach", 80, 20, nvim_config)

        # the tab(s) are rendered by Qt, so Neovim must never use the top grid line for its
        # own tabline
        await self.nvi.call("nvim_set_option_value", "showtabline", 0, {})

        # the window is likely already shown at its real size; sync the global grid to it now
        # (resize requests before the attach were ignored)
        self._resize_global_grid()

        # subscribe to which buffer is shown in which window; we send (win, bufnr, name) so the
        # GUI can label the right tab and remember each window's buffer (needed to act on it when
        # the window later closes). BufWinEnter also covers a buffer (re)appearing in a window
        # without a read, e.g. ':tab sbuffer' when we abort a modified tab's close
        _code = f"""
            vim.api.nvim_create_autocmd({{'BufFilePost', 'BufReadPost', 'BufWinEnter'}}, {{
                callback = function()
                    local win = vim.api.nvim_get_current_win()
                    local buf = vim.api.nvim_get_current_buf()
                    local name = vim.api.nvim_buf_get_name(0)
                    vim.rpcnotify({self.nvi.channel_id}, 'window_buffer', win, buf, name)
                end
            }})
        """
        await self.nvi.call("nvim_exec_lua", _code, [])

        # subscribe to the buffer modified change; we send the current window id so the GUI can
        # mark the right tab (comes back as a 'modified_changed' notification)
        _code = f"""
            vim.api.nvim_create_autocmd({{'BufModifiedSet', 'BufWritePost'}}, {{
                callback = function()
                    local win = vim.api.nvim_get_current_win()
                    vim.rpcnotify({self.nvi.channel_id}, 'modified_changed', win, vim.bo.modified)
                end
            }})
        """
        await self.nvi.call("nvim_exec_lua", _code, [])

        # notice files changed on disk behind our back and let Neovim ask what to do (load /
        # ignore / ...): keep autoread OFF (it defaults ON) so 'checktime' prompts instead of
        # silently reloading, and re-check a buffer whenever it is entered (this refreshes a tab
        # when the user switches to it); regaining window focus re-checks all buffers too (see
        # changeEvent -> ':checktime')
        _code = """
            vim.o.autoread = false
            vim.api.nvim_create_autocmd('BufEnter', {
                callback = function(ev) vim.cmd.checktime({ args = { tostring(ev.buf) } }) end,
            })
        """
        await self.nvi.call("nvim_exec_lua", _code, [])
        self._sync_menu_state()  # initial menu state (no active tab modified yet)

        # if a source is indicated, open it, differentiating if it's a file or standard input
        if paths_to_open == SPECIAL_STDIN_PATH:
            await self._feed_neovim_from_stdin()
        else:
            # first path opens in the current window; the rest open each in a new tabpage
            for index, path in enumerate(paths_to_open):
                await self._feed_neovim_from_path(path, new_tab=index > 0)

    async def _feed_neovim_from_stdin(self):
        """Feed neovim with data read from standard input."""
        temp_fd, temp_filepath = tempfile.mkstemp(prefix="nysor_stdin_")
        stdin_fd = sys.stdin.fileno()
        while True:
            try:
                transferred = os.splice(stdin_fd, temp_fd, 1024 * 1024)
            except OSError:
                # this happens when stdin was not open in first place
                logger.warning(
                    "Got OSError when reading from stding; "
                    "are you sure you piped data in?"
                )
                return
            if transferred == 0:  # EOF
                break
        os.close(temp_fd)
        await self._feed_neovim_from_path(temp_filepath)

    async def _feed_neovim_from_path(self, path_to_open, new_tab=False):
        """Indicate neovim to open a file from a path, in the current window or a new tabpage."""
        cmd = {"cmd": "tabedit" if new_tab else "edit", "args": [path_to_open]}
        opts = {"output": False}  # don't capture output
        try:
            await self.nvi.call("nvim_cmd", cmd, opts)
        except NeovimError as err:
            log_notdone("Got error when opening the file, this should never happen", err=err)

    async def _save_for_quit(self, entry):
        """Save an editor's buffer before closing it during quit; return False if the user cancels.

        A named buffer is written directly; an unnamed one asks for a filename (saveas!). Awaited
        so the write completes before the window is closed and before the final qall.
        """
        if entry.filepath:
            await self.nvi.call("nvim_call_function", "win_execute", [entry.win_id, "write"])
            return True
        filename, _ = QFileDialog.getSaveFileName(self, "Save File", "", "")
        if not filename:
            return False
        # saveas! in that window's context (vim.cmd escapes the path; the bang overwrites the file
        # the dialog already confirmed)
        _code = """
            local win, fname = ...
            vim.api.nvim_win_call(win, function()
                vim.cmd.saveas({args = {fname}, bang = true})
            end)
        """
        await self.nvi.call("nvim_exec_lua", _code, [entry.win_id, filename])
        return True

    async def _quit(self):
        """Close the GUI after Neovim is down, prompting for each editor with unsaved changes.

        Walk the open editors: each modified one prompts Save / Discard / Cancel and is closed as
        it is answered (save -> write/saveas; discard -> mark the buffer unmodified so its changes
        are dropped). Everything is AWAITED so it completes in order before the final qall (else a
        fire-and-forget close would race qall, which would then still see the buffer modified ->
        E37). Cancel aborts the quit: already-closed editors stay closed, the rest stay open.
        """
        logger.debug("Start shutdown; resolving modified editors")
        for entry in self.grids.get_all_entries():  # snapshot: closing mutates the registry
            if entry.win_id is None or not entry.pane.modified:
                continue
            choice = await self._ask_close_modified(entry.filepath)
            if choice == "cancel":
                self._closing = 0  # abort: already-closed editors stay closed, the rest stay open
                return
            if choice == "save":
                if not await self._save_for_quit(entry):
                    self._closing = 0  # the save-as dialog was cancelled
                    return
            else:
                # discard: clear the modified flag so the buffer closes without writing (its
                # unsaved changes are simply dropped, the file on disk is left untouched)
                await self.nvi.call(
                    "nvim_call_function", "win_execute", [entry.win_id, "setlocal nomodified"])
            # close this editor's window now that its buffer is clean; the last remaining window
            # cannot be closed this way (E444) -> leave it for the qall below
            try:
                await self.nvi.call("nvim_win_close", entry.win_id, False)
            except NeovimError:
                pass

        # nothing modified is left; quit for real (this closes any remaining unmodified windows)
        logger.debug("Start shutdown, asking Neovim to quit")
        error = await self.nvi.quit()
        if error:
            dlg = QMessageBox(self)
            dlg.setIcon(QMessageBox.Icon.Warning)
            dlg.setWindowTitle("Neovim Error")
            dlg.setText(error)
            dlg.setStandardButtons(QMessageBox.StandardButton.Ok)

            # wait asyncly for the dialog to be closed
            closed = asyncio.Event()
            dlg.finished.connect(lambda _result: closed.set())
            dlg.open()
            await closed.wait()

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

    def is_closing(self):
        """Whether the application has started (or finished) its shutdown sequence."""
        return self._closing != 0

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
            self._swarm.close()
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

    # -- set of functions to interact with buffers/neovim

    def new_file(self):
        """Open a new empty unnamed buffer in a new tab."""
        self.nvi.future_request("nvim_command", "tabnew")

    def open_file_dialog(self):
        """Ask the user for a file and open it (in a new tab; see open_file)."""
        filename, _ = QFileDialog.getOpenFileName(self, "Open File", "", "")
        if filename:
            self.open_file(filename)

    def open_file(self, filename):
        """Open a file in a new tab, reusing the active tab only if it is an empty unnamed buffer.

        Using nvim_cmd with structured args lets Neovim escape the path (spaces, etc.) for us.
        """
        entry = self._active_entry()
        reuse = entry is not None and not entry.filepath and not entry.pane.modified
        cmd = "edit" if reuse else "tabedit"
        self.nvi.future_request("nvim_cmd", {"cmd": cmd, "args": [filename]}, {"output": False})

    def buffer_save(self):
        """Save the current buffer."""
        self.nvi.future_request("nvim_command", "write")


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
        "-V", "--version", action="store_true",
        help="Show Nysor version and quit.",
    )
    parser.add_argument(
        "path", action="store", nargs="*", default=None,
        help=(
            "Path to the file to edit or directory to open. It's optional, and can be indicated "
            "multiple times. Special value '-' can be passed to open 'standard input', but cannot "
            "be mixed with other regular paths."
        )
    )

    # parse arguments
    args = parser.parse_args()

    if args.version:
        print("Nysor", get_nysor_version())
        return 0

    if SPECIAL_STDIN_PATH in args.path:
        if len(args.path) == 1:
            # successful case of including the stdin path
            requested_paths = SPECIAL_STDIN_PATH
        else:
            raise ValueError("Cannot specify special '-' among other paths")
    else:
        requested_paths = [os.path.realpath(path) for path in args.path]

    # setup logging and create the app itself
    logsetup(args.loglevel)
    app = qasync.QApplication(sys.argv)

    # connect with async's event loop
    event_loop = qasync.QEventLoop(app)
    event_loop.set_debug(True)
    asyncio.set_event_loop(event_loop)
    app_close_event = asyncio.Event()
    app.aboutToQuit.connect(app_close_event.set)

    async def main():
        """Discover if this process will handle this path and start everything in that case."""
        nysor_version = get_nysor_version()
        logger.info("Starting Nysor {}", nysor_version)

        # FIXME.90: enable multiple paths!
        # if path not in (SPECIAL_STDIN_PATH, None):
        #    already_handled = await swarm.discover(event_loop, path)
        #    if already_handled:
        #        return

        # start and show GUI
        main_window = MainApp(nysor_version, event_loop, requested_paths, args.nvim)
        main_window.show()
        await app_close_event.wait()

    # go!
    with event_loop:
        event_loop.run_until_complete(main())
