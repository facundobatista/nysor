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
    QTabBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt, QEvent, QSize, QTimer
from PyQt6.QtGui import QIcon, QAction


from nysor import swarm
from nysor.logtools import log_notdone, logsetup, LOG_LEVELS
from nysor.nvim_interface import NvimInterface, NeovimExecutableNotFound, NeovimError
from nysor.nvim_notifications import NvimNotifications, registry
from nysor.text_display import TextDisplay, MIN_COLS_ROWS
from nysor.utils import call_async, AsyncQMessageBox

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

# what we show as a "name" if the editor still has no name ;)
UNNAMED_NAME = "[No Name]"

# the character that prefixes titles to indicate the buffer is modified
MODIFIED_INDICATOR = "●"


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
    """Build a menu from one shared definition, filtered by scope.

    A single MENU serves the three places a menu appears; each entry declares the SET of scopes it
    belongs to: SCOPE_MAIN (the main window's menu bar), SCOPE_DETACHED (a detached window's menu
    bar), SCOPE_TAB (a tab's right-click context menu). One MainMenu is tied to its `host` window
    and one scope: `attach_bar()` builds the two menu bars, `build_popup()` the popup. When a menu
    closes, focus returns to its host window's editor.

    Entry-scoped actions (Save, Save As, Reload, Detach, Re-attach, Close) act on a `target` -- a
    callable returning the GridEntry the menu operates on: the main bar targets its current tab,
    a detached bar targets its own pane, a context menu targets the right-clicked tab. The `app`
    (a MainApp) is where those operations live. So the same handler serves every scope; only the
    target differs.
    """

    SCOPE_MAIN = 1
    SCOPE_DETACHED = 2
    SCOPE_TAB = 3
    SCOPE_OUTSIDE_TAB = 4

    # (label, handler-suffix, scope); (None, None, None) is a separator
    MENU = {
        "&File": [
            ("&New", "file__new", {SCOPE_MAIN, SCOPE_OUTSIDE_TAB}),
            ("&Open", "file__open", {SCOPE_MAIN, SCOPE_OUTSIDE_TAB}),
            (None, None, None),
            ("&Save", "file__save", {SCOPE_MAIN, SCOPE_DETACHED, SCOPE_TAB}),
            ("S&ave as...", "file__save_as", {SCOPE_MAIN, SCOPE_DETACHED, SCOPE_TAB}),
            (None, None, None),
            ("&Reload", "file__reload", {SCOPE_MAIN, SCOPE_DETACHED, SCOPE_TAB}),
            # Detach/Re-attach share this slot: the main window only ever shows a tab (-> Detach),
            # a detached window only ever shows its own pane (-> Re-attach); never both at once
            ("&Detach", "window__detach", {SCOPE_MAIN, SCOPE_TAB}),
            ("R&e-attach", "window__reattach", {SCOPE_DETACHED}),
            ("&Close", "file__close_tab", {SCOPE_MAIN, SCOPE_DETACHED, SCOPE_TAB}),
            ("E&xit", "file__exit", {SCOPE_MAIN}),
        ],
        "&Debug": [
            ("Run a blocking call", "debug__blocking_call", {SCOPE_MAIN}),
            ("Run an async task", "debug__async_task", {SCOPE_MAIN}),
        ],
        "&Help": [
            ("Open &project page", "help__open_project_page", {SCOPE_MAIN, SCOPE_DETACHED}),
            ("Create a new &issue", "help__create_issue", {SCOPE_MAIN, SCOPE_DETACHED}),
            (None, None, None),
            ("&About Nysor", "help__about", {SCOPE_MAIN, SCOPE_DETACHED}),
        ],
    }

    def __init__(self, app, host, scope, target):
        self._app = app  # the MainApp, where the entry-scoped operations live
        self._host = host  # the QMainWindow this menu belongs to (its focus returns here on close)
        self._scope = scope
        self._target = target  # callable -> the GridEntry the entry-scoped actions act on
        self.actions = {}

    def attach_bar(self):
        """Build a persistent menu bar on this menu's host window (SCOPE_MAIN / SCOPE_DETACHED)."""
        menu_bar = self._host.menuBar()
        for title, options in self.MENU.items():
            entries = [entry for entry in options if self._in_scope(entry)]
            if not entries:
                # every real entry was filtered out -> do not add an empty menu
                continue
            menu = menu_bar.addMenu(title)
            self._fill(menu, entries)
        self.apply_enable_state()

    def build_popup(self):
        """Build a transient context menu (SCOPE_TAB) on the host window; the caller exec()s it."""
        menu = QMenu(self._host)
        # only the File entries carry SCOPE_TAB
        entries = [entry for entry in self.MENU["&File"] if self._in_scope(entry)]
        self._fill(menu, entries)
        self.apply_enable_state()
        return menu

    def _in_scope(self, entry):
        """Whether a MENU entry (or a separator, scopes=None) belongs to this menu's scope."""
        _label, _name, scopes = entry
        return scopes is None or self._scope in scopes

    def _restore_editor_focus(self):
        """Return focus to the active editor once the closing menu is gone.

        Targets the ACTIVE editor (not the host window): a plain dismiss leaves it on the host's
        editor, but Detach/Re-attach/Close move or destroy that editor, so focus must follow it.
        Deferred with a zero timer so it runs after Qt's focus handling (and the action) settle.
        """
        QTimer.singleShot(0, self._app.focus_active_editor)

    def _fill(self, menu, entries):
        """Add entries to a menu, dropping leading/trailing/double separators left by filtering."""
        have_action = False  # a real action seen since the last separator
        pending_sep = False
        for label, name, _scopes in entries:
            if name is None:
                if have_action:
                    pending_sep, have_action = True, False
                continue
            if pending_sep:
                menu.addSeparator()
                pending_sep = False
            action = QAction(label, menu)
            action.triggered.connect(getattr(self, f"_on__{name}"))
            self.actions[name] = action
            menu.addAction(action)
            have_action = True

        # when closing the menu (e.g. via Esc); ensure the focus comes back to the window's
        # editor so typing reaches Neovim again
        menu.aboutToHide.connect(self._restore_editor_focus)

    def apply_enable_state(self):
        """Enable/disable the state-dependent items for this menu's current target editor."""
        entry = self._target()
        modified = entry is not None and entry.pane.modified
        if "file__save" in self.actions:
            self.actions["file__save"].setEnabled(modified)
        if "file__reload" in self.actions:
            # Reload reverts to the saved file, so only for a modified, named buffer
            named = entry is not None and bool(entry.filepath)
            self.actions["file__reload"].setEnabled(modified and named)
        if "file__open" in self.actions:
            self.actions["file__open"].setEnabled(not modified)
        if "window__detach" in self.actions:
            # detaching the main window's only tab would leave its strip empty
            self.actions["window__detach"].setEnabled(self._app.tabs.count() > 1)

    def _log_action(func):
        """Log the action indicate by the user."""
        def _f(self):
            logger.debug("User triggered menu action {!r}", func.__name__)
            return func(self)
        return _f

    @_log_action
    def _on__file__new(self):
        """Open a new empty tab."""
        self._app.new_file()

    @_log_action
    def _on__file__open(self):
        """Open a file (deduplicated across instances; see MainApp.open_file_dialog)."""
        self._app.open_file_dialog()

    @_log_action
    def _on__file__save(self):
        """Save the target editor, asking for a name if it does not have one yet."""
        self._app.save_entry(self._target())

    @_log_action
    def _on__file__save_as(self):
        """Save the target editor to a new file."""
        self._app.save_entry_as(self._target())

    @_log_action
    def _on__file__reload(self):
        """Revert the target editor to the saved file, dropping unsaved changes (asks first)."""
        call_async(self._app.reload, self._target())

    @_log_action
    def _on__window__detach(self):
        """Pull the target tab out into its own detached window."""
        self._app.detach_tab(self._target())

    @_log_action
    def _on__window__reattach(self):
        """Move the target (detached) editor back into the main window as a tab."""
        self._app.reattach_pane(self._target())

    @_log_action
    def _on__file__close_tab(self):
        """Close the target editor (its Neovim window)."""
        call_async(self._app.close_tab, self._target())

    @_log_action
    def _on__file__exit(self):
        """Exit the application."""
        self._app.close_gui()

    @_log_action
    def _on__debug__blocking_call(self):
        """Test an action triggered by the GUI; this is a test/dev helper."""
        logger.info("Code run in a standard function run from the GUI.")

    @_log_action
    def _on__debug__async_task(self):
        """Run an async task; this is a test/dev helper."""

        async def async_task():
            result = await self._app.nvi.call("nvim_list_uis")
            logger.info("Code run in an async task, listing UIs from Neovim: {}", result)

        asyncio.create_task(async_task())

    @_log_action
    def _on__help__open_project_page(self):
        """Open the project page in the browser."""
        webbrowser.open("https://github.com/facundobatista/nysor")

    @_log_action
    def _on__help__create_issue(self):
        """Open the issue tracker in the browser."""
        dialog = CreateIssueDialog(self._host)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.get_values()
        open_new_issue_page(values["title"], values["description"])

    @_log_action
    def _on__help__about(self):
        """Show the About dialog."""
        msg = _ABOUT_TEXT.format(version=self._app.nysor_version)
        dlg = QMessageBox(self._host)
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

        # get the lengths of the lines currently shown, for THIS pane's own buffer -- getbufline(0)
        # is the *alternate* buffer, not ours, so with more than one buffer it read the wrong one
        grid = registry.get_grid_by_pane(self)
        entry = registry.get_entry_by_grid(grid)
        buf = entry.bufnr if entry is not None else None
        if buf is None:
            return  # we don't know this window's buffer yet; leave the horizontal scroll bar as is
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


class EditorTabBar(QTabBar):
    """The tab strip's bar: drag to reorder (setMovable), or drag a tab out to detach it.

    Reorder is safe because everything is keyed by pane, not tab index (the QTabWidget owns order).
    Detach is decided on release: we remember the pane pressed on and, if the mouse lets go outside
    the main window, we ask to detach that pane. Holding the pane object (not an index) makes this
    robust to the reordering that happens mid-drag.
    """

    def __init__(self, pane_at, detach):
        super().__init__()
        self.setMovable(True)  # drag within the bar reorders tabs
        self._pane_at = pane_at  # index -> pane (None if the index is invalid)
        self._detach = detach  # pane -> pull it out into its own window
        self._pressed_pane = None

    def sizeHint(self):
        """Span the full width so the strip reaches past its tabs into empty, draggable space.

        Without this the bar is only as wide as its tabs, so dragging a tab rightwards pulls it off
        the bar's own rect and it gets clipped/hidden. The extra width never stretches the tabs
        (setExpanding stays False); it is just reachable empty space (right-clicked for New/Open).
        """
        hint = super().sizeHint()
        parent = self.parent()
        if parent is None:
            width = hint.width()
        else:
            width = max(hint.width(), parent.width())
        return QSize(width, hint.height())

    def mousePressEvent(self, event):
        """Remember which pane a left-drag starts on, so a pull-off can detach the right one."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed_pane = self._pane_at(self.tabAt(event.position().toPoint()))
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """Let the bar finish its reorder; then, if released outside the main window, detach."""
        super().mouseReleaseEvent(event)

        if self._pressed_pane is None:
            # if we were not dragging a pane there's nothing to do
            return

        pane = self._pressed_pane
        self._pressed_pane = None

        # check if we're "dropping the pane" outside the window
        pos = event.globalPosition().toPoint()
        inside_window = self.window().frameGeometry().contains(pos)

        if not inside_window:
            entry = registry.get_entry_by_pane(pane)
            self._detach(entry)


class DetachedWindow(QMainWindow):
    """An editor pane pulled out of the tab strip into its own OS window.

    This is a purely-Qt move: the EditorPane is reparented here, no Neovim windows/buffers change.
    Because Neovim (under multigrid) only draws the current tabpage, only the focused editor is
    live; the others (tabs or detached windows) show their last content until focused again.
    Focusing this window makes its pane the active one (via the same win_gotoid used for tabs); the
    X button closes its buffer (with an unsaved-changes prompt), while the menu's Re-attach moves
    the pane back into a tab. See MainMenu for the shared menu definition.
    """

    def __init__(self, app, pane):
        super().__init__()
        self._app = app
        self._pane = pane
        # a detached window's menu bar (host=self), acting on its OWN pane (not the active editor)
        self._menu = MainMenu(
            app, self, MainMenu.SCOPE_DETACHED, lambda: registry.get_entry_by_pane(self._pane))
        self._menu.attach_bar()
        self.setCentralWidget(pane)
        pane.show()  # removeTab hid the pane; setCentralWidget does not re-show it on its own

    def changeEvent(self, event):
        """When this window gains focus, make its pane the active editor."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._app.activate_pane(self._pane)

    def closeEvent(self, event):
        """Close this window's buffer (prompting on unsaved changes); Re-attach keeps it instead.

        We never let Qt destroy the window here: closing the buffer's Neovim window is what tears
        the detached window down for real (via the resulting grid_destroy). The exception is a
        whole app quit, where the window is expected to just go.
        """
        if self._app.is_closing():
            event.accept()  # the whole app is going down; let this window close
            return
        event.ignore()
        self._app.close_pane(self._pane)


class MainApp(QMainWindow):
    """The main application window."""

    def __init__(self, version, loop, paths_to_open, nvim_exec_path):
        super().__init__()
        self.setWindowIcon(QIcon("nysor/imgs/icon-1024.png"))
        self.nysor_version = version

        self._closing = 0
        # a pane kept aside while its buffer is reopened, to re-attach to the new window
        self._reattach_pending = None
        # panes pulled out of the tab strip into their own OS window (pane -> DetachedWindow)
        self._detached = {}
        # set while detaching so removing the tab does not bounce activation to a sibling tab
        self._suppress_tab_activation = False
        # the grid Neovim currently has as its active window (the only one we may resize); tracked
        # from win_pos, updated BEFORE building its tab so a relayout mid-build cannot resize the
        # previously-active (now hidden) window
        self._active_grid = None
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
        # custom bar: drag to reorder, or pull a tab off the strip to detach it into its own window
        self.tabs.setTabBar(EditorTabBar(self.tabs.widget, self.detach_tab))
        # show each file name in full (no eliding, so nothing is squeezed); tabs take their natural
        # width, and when they overflow the bar shows scroll buttons instead of shrinking them. The
        # full path is in each tab's tooltip (see _refresh_tab_label).
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.tabBar().setExpanding(False)
        self.main_layout.addWidget(self.tabs, stretch=1)

        self._unclaimed_pane = EditorPane(self)
        self.tabs.addTab(self._unclaimed_pane, UNNAMED_NAME)
        self.text_display = self._unclaimed_pane.text_display

        # the main window's menu bar (app and host are both self), acting on the current tab (must
        # be created after we have tabs so the menu options can be enabled/disabled)
        self._menu = MainMenu(
            self, self, MainMenu.SCOPE_MAIN,
            lambda: registry.get_entry_by_pane(self.tabs.currentWidget()))
        self._menu.attach_bar()

        # switching a tab from the GUI must move Neovim (see _on_tab_changed)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # the bar spans the whole header row, so a single context handler covers it: right-clicking
        # a tab offers per-tab File actions, the empty area to their right offers New/Open (see
        # _show_tab_menu)
        tab_bar = self.tabs.tabBar()
        tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(self._show_tab_menu)

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
        """Refresh every window's menu enable-state to match its editor.

        The main bar follows its current tab; each detached bar follows its own pane (frozen while
        unfocused, so re-applying it is a no-op for the inactive ones) -- never the global active.
        """
        self._menu.apply_enable_state()
        for window in self._detached.values():
            window._menu.apply_enable_state()

    def focus_active_editor(self):
        """Give keyboard focus back to the active editor (the one Neovim currently has current).

        This follows the ACTIVE editor, not any particular window: after a menu action that moves
        the editor (Detach) or destroys its window (Re-attach, Close), focus must go where the
        editor went; for a plain menu dismiss the active editor is already the host's, so it stays.
        """
        if self.text_display is not None:
            self.text_display.setFocus()

    @staticmethod
    def _window_title(filepath, modified=False):
        """Build a window title: '{name} ({dir, ~-collapsed}) - Nysor' (or '[No Name] - Nysor')."""
        if not filepath:
            title = f"{UNNAMED_NAME} - Nysor"
        else:
            name = os.path.basename(filepath)
            basedir = os.path.dirname(filepath)
            home = os.path.expanduser("~")
            if basedir == home:
                basedir = "~"
            elif basedir.startswith(home + os.sep):
                basedir = "~" + basedir[len(home):]
            title = f"{name} ({basedir}) - Nysor"
        return f"{MODIFIED_INDICATOR} {title}" if modified else title

    def _refresh_main_title(self):
        """Set the main window title to its CURRENT tab's file (full info; see _window_title).

        It follows the shown tab, not the globally-active editor: while you work in a detached
        window, the main window keeps showing (and titling) its own current tab.
        """
        pane = self.tabs.currentWidget()
        grid = registry.get_grid_by_pane(pane) if pane is not None else None
        entry = registry.get_entry_by_grid(grid)
        path = entry.filepath if entry is not None else None
        modified = entry is not None and entry.pane.modified
        self.setWindowTitle(self._window_title(path, modified))

    def _refresh_tab_label(self, grid_id):
        """Rebuild a window grid's label (tab text or detached-window title) from its filepath."""
        entry = registry.get_entry_by_grid(grid_id)
        if entry is None:
            logger.warning("_refresh_tab_label for a grid with no entry: {}", grid_id)
            return
        window = self._detached.get(entry.pane)
        if window is not None:
            window.setWindowTitle(self._window_title(entry.filepath, entry.pane.modified))
            return
        # tab text stays short (basename + modified marker); the tab bar elides long names and the
        # tooltip carries the full path
        name = os.path.basename(entry.filepath) if entry.filepath else UNNAMED_NAME
        if entry.pane.modified:
            name = f"{MODIFIED_INDICATOR} {name}"
        index = self.tabs.indexOf(entry.pane)
        if index != -1:
            self.tabs.setTabText(index, name)
            self.tabs.setTabToolTip(index, entry.filepath or UNNAMED_NAME)
        self._refresh_main_title()  # the current tab's filepath may have changed

    def refresh_tab(self, grid_id):
        """Update a window grid's tab after its buffer/filepath changed, enforcing one-per-buffer.

        One-buffer-one-tab (the in-process analogue of the swarm's cross-process path check): if
        another grid already owns this buffer, collapse this duplicate window into it -- switch to
        the existing tab and close the redundant Neovim window.
        """
        self._refresh_tab_label(grid_id)
        entry = registry.get_entry_by_grid(grid_id)
        if entry is None or entry.bufnr is None:
            return
        owner = registry.get_grid_by_buffer(entry.bufnr)
        if owner is not None and owner != grid_id:
            self._collapse_duplicate(grid_id, owner)

    def _collapse_duplicate(self, dup_grid, existing_grid):
        """Go to the tab that already owns the buffer and close the redundant window."""
        dup = registry.get_entry_by_grid(dup_grid)
        existing = registry.get_entry_by_grid(existing_grid)
        # mark the duplicate's pane as closing right away, so switching to the existing tab (which
        # may fire its own refresh) does not try to collapse in the other direction
        dup.pane.closed = True
        if existing is not None and existing.win_id is not None:
            self.nvi.future_request("nvim_call_function", "win_gotoid", [existing.win_id])
        if dup.win_id is not None:
            self.nvi.future_request("nvim_win_close", dup.win_id, False)

    def set_tab_modified(self, grid_id, modified):
        """Set a window grid's modified state (label marker; menu if it is the active tab)."""
        entry = registry.get_entry_by_grid(grid_id)
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

        The mode setting (e.g. insert -> a bar cursor) belongs to Neovim's current
        window. New tabs pick up the remembered mode on build; a window picks it up
        again when it becomes active.
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
            self.tabs.addTab(pane, UNNAMED_NAME)
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
        shown_elsewhere = bufnr is not None and registry.get_grid_by_buffer(bufnr) is not None
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
        name = os.path.basename(filepath) if filepath else UNNAMED_NAME
        dlg = QMessageBox(self)
        dlg.setIcon(QMessageBox.Icon.Information)
        dlg.setWindowTitle("Close aborted")
        dlg.setText(
            f"{name!r} has unsaved changes, so its tab was kept open.\n"
            "Save it (or use a Neovim command like :w / :q!) before closing."
        )
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        dlg.open()  # non-blocking; just informational

    def close_pane(self, pane):
        """Close a specific editor pane's buffer, prompting on unsaved changes (see close_tab).

        Used by a detached window's X button, which must target its own pane rather than whatever
        editor happens to be active.
        """
        call_async(self.close_tab, registry.get_entry_by_pane(pane))

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
        if self.tabs.count() <= 1 and entry.pane not in self._detached:
            # this is the main window's last tab, but windows are still detached elsewhere:
            # closing it would leave the main window with an empty strip
            dlg = QMessageBox(self)
            dlg.setIcon(QMessageBox.Icon.Information)
            dlg.setWindowTitle("Cannot close")
            dlg.setText(
                "This is the main window's last tab, and other windows are still detached.\n"
                "Re-attach or close them first."
            )
            dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
            dlg.open()  # non-blocking; just informational
            return

        if entry.pane.modified:
            # parent the prompt to the pane's own window so closing it returns activation there
            window = self._detached.get(entry.pane, self)
            choice = await self._ask_close_modified(window, entry.filepath)
            if choice == "cancel":
                QTimer.singleShot(0, self.focus_active_editor)  # aborted; back to the editor
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

    async def _ask_close_modified(self, parent, filepath):
        """Ask the user how to close a tab with unsaved changes; return save/discard/cancel."""
        name = os.path.basename(filepath) if filepath else UNNAMED_NAME
        dlg = AsyncQMessageBox(parent)
        dlg.setIcon(QMessageBox.Icon.Warning)
        dlg.setWindowTitle("Unsaved changes")
        dlg.setText(f"{name!r} has unsaved changes.")
        dlg.setInformativeText("Save it before closing?")
        save_btn = dlg.addButton(QMessageBox.StandardButton.Save)
        discard_btn = dlg.addButton(QMessageBox.StandardButton.Discard)
        dlg.addButton(QMessageBox.StandardButton.Cancel)
        dlg.setDefaultButton(save_btn)
        await dlg.wait()

        clicked = dlg.clickedButton()
        if clicked is save_btn:
            return "save"
        if clicked is discard_btn:
            return "discard"
        return "cancel"

    async def reload(self, entry):
        """Revert a buffer to the file on disk, dropping unsaved changes (asks first).

        The Reload menu item is enabled only for a modified, named buffer, so we trust that the
        entry exists, has a filepath and a window. If not, this rightfully blows up.
        """
        # parent the prompt to the pane's own window so closing it returns activation there
        window = self._detached.get(entry.pane, self)
        proceed = await self._ask_reload(window, entry.filepath)
        QTimer.singleShot(0, self.focus_active_editor)  # give focus back to the editor after it
        if not proceed:
            return
        # ':edit!' reloads the file, dropping the unsaved changes; run it in the pane's own window
        # so it targets the right buffer even if focus has moved on
        self.nvi.future_request("nvim_call_function", "win_execute", [entry.win_id, "edit!"])

    async def _ask_reload(self, parent, filepath):
        """Ask the user to confirm discarding unsaved changes; return True to proceed."""
        name = os.path.basename(filepath)
        dlg = AsyncQMessageBox(parent)
        dlg.setIcon(QMessageBox.Icon.Warning)
        dlg.setWindowTitle("Reload")
        dlg.setText(f"{name!r} has unsaved changes.")
        dlg.setInformativeText("Discard them and revert to the saved file?")
        discard_btn = dlg.addButton(QMessageBox.StandardButton.Discard)
        cancel_btn = dlg.addButton(QMessageBox.StandardButton.Cancel)
        dlg.setDefaultButton(cancel_btn)  # default to the safe choice for a destructive action
        await dlg.wait()

        return dlg.clickedButton() is discard_btn

    def _show_new_open_menu(self, global_pos):
        """Show a New/Open context menu (the empty part of the tab-bar row was right-clicked)."""
        menu = QMenu(self)
        new_act = menu.addAction("New")
        open_act = menu.addAction("Open...")
        chosen = menu.exec(global_pos)

        if chosen is new_act:
            self.new_file()
        elif chosen is open_act:
            self.open_file_dialog()

    def _show_tab_menu(self, pos):
        """Context menu: per-tab File actions on a tab, New/Open on the empty strip area."""
        tab_bar = self.tabs.tabBar()
        index = tab_bar.tabAt(pos)
        if index == -1:
            self._show_new_open_menu(tab_bar.mapToGlobal(pos))
            return
        entry = registry.get_entry_by_pane(self.tabs.widget(index))
        # a transient menu targeting THIS tab; the QAction handlers run via their triggered signal
        popup = MainMenu(self, self, MainMenu.SCOPE_TAB, lambda: entry)
        popup.build_popup().exec(tab_bar.mapToGlobal(pos))

    def save_entry(self, entry):
        """Write a tab's buffer; if it has no filename yet, ask for one."""
        if entry is None or entry.win_id is None:
            return
        if entry.filepath:
            self.nvi.future_request("nvim_call_function", "win_execute", [entry.win_id, "write"])
        else:
            self.save_entry_as(entry)

    def save_entry_as(self, entry):
        """Save a tab's buffer to a user-chosen filename.

        QFileDialog already confirms overwriting an existing file, so a returned filename means
        the user picked a fresh name or approved the overwrite; we then write with 'saveas!' (the
        bang only stops Neovim from refusing the -already approved- existing file).
        """
        if entry is None or entry.win_id is None:
            return
        # parent the dialog to the pane's own window (the detached one, if any) so that closing it
        # returns activation there, not to the main window
        window = self._detached.get(entry.pane, self)
        filename, _ = QFileDialog.getSaveFileName(window, "Save File", "", "")
        # returning from the dialog leaves focus on its parent window's frame; hand it back to the
        # active editor (deferred, so it runs after Qt finishes re-activating the window)
        QTimer.singleShot(0, self.focus_active_editor)
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

    def mark_active_grid(self, grid_id):
        """Record which grid Neovim just made current (called from win_pos, before its tab exists).

        This is set early -- before build_editor_tab, whose font setup relayouts the window and can
        fire a resizeEvent on the previously-active tab -- so resize_editor_grid never tries to
        resize a window that Neovim already moved off (which would error 'Invalid window handle').
        """
        self._active_grid = grid_id

    def set_active_editor(self, grid_id):
        """Bring the given window grid's pane to the front and focus it (Neovim -> GUI).

        The pane may live in a tab (select it) or in a detached window (raise it). text_display is
        updated *before* bringing it forward, so the activation this triggers is recognized as
        already-active by activate_pane and does not bounce back to Neovim.
        """
        entry = registry.get_entry_by_grid(grid_id)
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
        grid = registry.get_grid_by_pane(pane)
        entry = registry.get_entry_by_grid(grid)
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
        if self.tabs.count() <= 1:
            # detaching always empties one tab out of the main window's strip; with only one tab
            # left that empties it entirely, regardless of how many other windows are detached
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

    def reattach_pane(self, entry):
        """Move a detached pane back into the tab strip and drop its window."""
        pane = entry.pane
        window = self._detached.pop(pane, None)
        if window is None:
            return
        window.takeCentralWidget()  # release the pane from the window without deleting it
        index = self.tabs.addTab(pane, UNNAMED_NAME)
        window.deleteLater()
        grid = registry.get_grid_by_pane(pane)
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

        Only the window Neovim currently has active is resized: it is the one in the current
        tabpage, the only one Neovim lets us resize (try_resize_grid on a window in a hidden
        tabpage errors 'Invalid window handle'). We compare against `_active_grid` (tracked from
        win_pos), NOT text_display, because during a switch text_display lags Neovim briefly and a
        relayout in that gap would otherwise resize the just-hidden window. Others re-pin when they
        become active (see set_active_editor).
        """
        if display.pane is None or display.font_size is None:
            return
        grid = registry.get_grid_by_pane(display.pane)
        if grid is None or grid != self._active_grid:
            return  # not the current tabpage's window -> would be rejected; re-pins on activation
        font_size = display.font_size
        cols = max(MIN_COLS_ROWS, int(display.width() / font_size.width))
        rows = max(MIN_COLS_ROWS, int(display.height() / font_size.height))
        if (cols, rows) == display._last_grid_size:
            return  # nothing changed at grid granularity; skip the redundant resize
        display._last_grid_size = (cols, rows)
        self.nvi.future_request("nvim_ui_try_resize_grid", grid, cols, rows)

    def _path_discover_cb(self, path):
        """Swarm callback: if we have the `path`, reveal its tab/window.

        Return True if we have it.
        """
        return self._reveal_path(path)

    def _reveal_path(self, path):
        """Bring the editor showing `path` to the front; return True if we have it, else False.

        The editor may be a tab in the main window (select it and raise the main window) or a
        detached window (raise that one).
        """
        entry = registry.get_entry_by_path(path)
        if entry is None:
            return False
        window = self._detached.get(entry.pane)
        if window is not None:
            self._force_to_front(window)
        else:
            # in the registry and not detached -> it must be a tab; a missing index is a broken
            # invariant, not something to skip silently
            index = self.tabs.indexOf(entry.pane)
            assert index != -1, "a registered, non-detached pane must be a tab"
            self.tabs.setCurrentIndex(index)
            self._force_to_front(self)
        return True

    def _force_to_front(self, window):
        """Raise a window and grab focus even from another app (the WindowStaysOnTop dance)."""
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        window.show()
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        window.show()
        window.raise_()
        window.activateWindow()

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
        for entry in registry.get_all_entries():  # snapshot: closing mutates the registry
            if entry.win_id is None or not entry.pane.modified:
                continue
            pane_window_parent = self._detached.get(entry.pane, self)
            choice = await self._ask_close_modified(pane_window_parent, entry.filepath)
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
            dlg = AsyncQMessageBox(self)
            dlg.setIcon(QMessageBox.Icon.Warning)
            dlg.setWindowTitle("Neovim Error")
            dlg.setText(error)
            dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
            await dlg.wait()

            self._closing = 0  # reset
            return

        self._closing = 2  # allows final close
        logger.debug("Start shutdown, done")
        self._close_detached_windows()
        self.close()

    def _close_detached_windows(self):
        """Close every detached window.

        They are independent top-level windows, not children of the main one, so Qt does not
        close them along with it on its own.
        """
        for window in list(self._detached.values()):
            window.close()

    def _quit_callback(self):
        """Close the GUI because of nvim interface request."""
        if self._closing == 0:
            # only if it was not initiated internally
            logger.debug("Shutdown requested by nvim interface")
            self._closing = 2
            self._close_detached_windows()
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
        """Pick a file and open it, deduplicating locally first, then across instances.

        The pick and the local check are synchronous (if the user cancels or we already show the
        file, we never touch the async path); we only go async to ask the swarm when we actually
        need to -- i.e. the file is not open here.
        """
        filename, _ = QFileDialog.getOpenFileName(self, "Open File", "", "")
        if not filename:
            return
        filename = os.path.realpath(filename)  # normalize so path matching is consistent
        if self._reveal_path(filename):
            return  # already open here -> just revealed its tab/window; no swarm round-trip
        call_async(self._open_if_free_in_swarm, filename)

    async def _open_if_free_in_swarm(self, filename):
        """Open `filename` here unless another Nysor already has it (then just tell the user).

        Opens in a new tab, reusing the active tab only if it is an empty unnamed buffer. nvim_cmd
        with structured args lets Neovim escape the path (spaces, etc.) for us.
        """
        if await swarm.discover(asyncio.get_running_loop(), filename):
            await self._show_open_elsewhere(filename)
            return

        if self.text_display is None:
            reuse = False
        else:
            entry = registry.get_entry_by_pane(self.text_display.pane)
            reuse = not entry.filepath and not entry.pane.modified

        cmd = "edit" if reuse else "tabedit"
        await self.nvi.call("nvim_cmd", {"cmd": cmd, "args": [filename]}, {"output": False})

    async def _show_open_elsewhere(self, filepath):
        """Tell the user the file is already open in another Nysor instance (non-blocking)."""
        name = os.path.basename(filepath)
        dlg = AsyncQMessageBox(self)
        dlg.setIcon(QMessageBox.Icon.Information)
        dlg.setWindowTitle("Already open")
        dlg.setText(f"{name!r} is already open in another Nysor instance.")
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        await dlg.wait()


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
        # avoid duplicates in the given paths but keep order (args.path will be short almost
        # always, we can find in the list, no need for more advanced algos)
        requested_paths = []
        for path in args.path:
            if path not in requested_paths:
                requested_paths.append(path)

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
        """Start Nysor, opening only the requested paths not already handled elsewhere."""
        nysor_version = get_nysor_version()
        logger.info("Starting Nysor {}", nysor_version)

        if requested_paths != SPECIAL_STDIN_PATH and requested_paths:
            # ask the swarm about each path (concurrently) and
            # keep only the ones no other Nysor is already showing
            coros = (swarm.discover(event_loop, path) for path in requested_paths)
            handled = await asyncio.gather(*coros)
            paths_to_open = []
            for path, others in zip(requested_paths, handled):
                if others:
                    logger.info(
                        "Path handled in other Nysor instance (not opening here): {}",
                        path
                    )
                else:
                    paths_to_open.append(path)
            if not paths_to_open:
                # every requested path is already open elsewhere -> this instance does not start
                logger.info("All requested paths handled by other Nysor instances; not starting")
                return
        else:
            paths_to_open = requested_paths

        # start and show GUI
        main_window = MainApp(nysor_version, event_loop, paths_to_open, args.nvim)
        main_window.show()
        await app_close_event.wait()

    # go!
    with event_loop:
        event_loop.run_until_complete(main())
