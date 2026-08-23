"""A semi-transparent overlay window showing live bot logs.

Qt owns the main thread while the blocking bot loop runs on a worker thread.

`gui_overlay_mode` keeps the window always-on-top *and* click-through by
default -- ordinary clicks land on whatever's behind it (the game),
rather than accidentally landing on the overlay -- but only while it
isn't the active window. Once it's given focus some other way (alt-tab,
clicking its taskbar entry), clicks on it work normally again (drag,
resize, scroll the log) until it loses focus, at which point it goes
back to click-through. This needs the raw `WS_EX_TRANSPARENT` extended
window style toggled directly via ctypes rather than Qt's own
`Qt.WindowType.WindowTransparentForInput` flag, since changing that flag
through Qt requires hiding and re-showing the window (see
`setWindowFlags`'s docs), which would steal/disrupt focus on every single
activation change -- exactly the moments this needs to react to.

Positioned on top of whatever is on screen, including the game window that
`capture_region` reads. If it overlaps the game, overlay pixels become part of
each capture and can corrupt fixed-UI template matching. Keep it clear of the
game window.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMainWindow, QPlainTextEdit, QTabWidget

from farm_merge_valet.config import settings

# Cap the log widget's content so a long-running session doesn't grow
# unbounded memory -- oldest lines are dropped once exceeded.
_MAX_LOG_LINES = 2000

# Win32 extended-window-style bits -- see module docstring for why this is
# toggled directly instead of through Qt's own window-flags API.
_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020


def _set_click_through(hwnd: int, enabled: bool) -> None:
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    if enabled:
        style |= _WS_EX_LAYERED | _WS_EX_TRANSPARENT
    else:
        style &= ~_WS_EX_TRANSPARENT
    user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)


class _LogBridge(QObject):
    """Marshals log records from whatever thread logged them onto the Qt
    GUI thread -- Qt widgets can only be touched from the thread that owns
    them, but a cross-thread signal emission queues onto that thread
    safely."""

    new_line = Signal(str)


class _AppBridge(QObject):
    bot_finished = Signal()


class QtLogHandler(logging.Handler):
    """A `logging.Handler` that forwards formatted records to the overlay
    window's log tab, via `_LogBridge` for thread safety."""

    def __init__(self, bridge: _LogBridge) -> None:
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        self._bridge.new_line.emit(message)


class OverlayWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Farm Merge Valet")
        self._apply_overlay_mode(settings.gui_overlay_mode)
        # Per-pixel alpha (not just the whole-window `setWindowOpacity`
        # below) needs this attribute explicitly -- without it, a
        # frameless window's "transparent" areas render as an opaque
        # placeholder color instead of actually compositing through to
        # the desktop, confirmed live.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(settings.gui_opacity)

        tabs = QTabWidget()
        tabs.setStyleSheet(
            "QTabWidget::pane { border: none; background: transparent; }"
            "QTabBar::tab { background: #202020; color: #cccccc; padding: 6px 16px; }"
            "QTabBar::tab:selected { background: #303030; color: white; }"
        )
        self.setCentralWidget(tabs)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(_MAX_LOG_LINES)
        self.log_view.setFont(QFont("Cascadia Mono", 10))
        self.log_view.setStyleSheet(
            "QPlainTextEdit {"
            " background-color: rgba(18, 18, 18, 235);"
            " color: #e0e0e0;"
            " border: none;"
            " padding: 6px;"
            "}"
        )
        tabs.addTab(self.log_view, "Logs")

        self.resize(640, 360)
        self._overlay_mode = settings.gui_overlay_mode

    def _apply_overlay_mode(self, enabled: bool) -> None:
        """`enabled` keeps the window always-on-top. The window otherwise
        always keeps normal titlebar/resize/minimize/maximize/close
        controls and stays in the taskbar/alt-tab list regardless of this
        setting -- see `_update_click_through` for the separate,
        focus-driven click-through behavior overlay mode also enables.
        """
        flags = Qt.WindowType.Window
        if enabled:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def _update_click_through(self) -> None:
        """Click-through while inactive, normally clickable once given
        focus (alt-tab, taskbar) -- see module docstring. A no-op outside
        overlay mode, and before the native window handle exists yet."""
        if not self._overlay_mode:
            return
        hwnd = int(self.winId())
        _set_click_through(hwnd, not self.isActiveWindow())

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange:
            self._update_click_through()

    def append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)


def run_overlay(run_bot: Callable[[], None], stop_bot: Callable[[], None]) -> int:
    """Run Qt on the main thread and the bot loop on a worker thread.

    Closing the overlay requests a bot stop; a bot-side quit request closes
    the overlay when the worker exits.
    """
    app = QApplication.instance() or QApplication([])
    window = OverlayWindow()

    log_bridge = _LogBridge()
    log_bridge.new_line.connect(window.append_log)
    handler = QtLogHandler(log_bridge)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%H:%M:%S"))
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    app_bridge = _AppBridge()
    app_bridge.bot_finished.connect(app.quit)
    app.aboutToQuit.connect(stop_bot)

    def worker() -> None:
        try:
            run_bot()
        finally:
            app_bridge.bot_finished.emit()

    thread = threading.Thread(target=worker, daemon=True, name="fmv-bot")

    window.show()
    window._update_click_through()  # set the initial (inactive) state
    thread.start()
    try:
        return app.exec()
    finally:
        stop_bot()
        thread.join(timeout=5)
        root_logger.removeHandler(handler)
