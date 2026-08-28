"""GUI-first Farm Merge Valet desktop application startup."""

from __future__ import annotations

import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from farm_merge_valet.config import ConfigStore
from farm_merge_valet.gui.controller import ApplicationController
from farm_merge_valet.gui.main_window import MainWindow as _MainWindow
from farm_merge_valet.gui.main_window import _app_icon
from farm_merge_valet.gui.theme import apply_theme
from farm_merge_valet.observability.logging import configure_logging, logging_sink


def run_application(store: ConfigStore | None = None) -> int:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv)
    app.setApplicationName("Farm Merge Valet")
    app.setWindowIcon(_app_icon())
    app.setQuitOnLastWindowClosed(False)
    config_store = store or ConfigStore()
    try:
        config = config_store.load()
    except (OSError, ValueError) as exc:
        answer = QMessageBox.critical(
            None,
            "Configuration could not be loaded",
            f"{exc}\n\nReset to safe defaults? The existing file will not be overwritten "
            "unless you choose Reset.",
            QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer is not QMessageBox.StandardButton.Reset:
            return 2
        config = config_store.reset()
    configure_logging(config.log_level)
    apply_theme(app, config.theme)
    controller = ApplicationController(config_store)
    window = _MainWindow(controller, eager_catalog_pages=False)
    signal.signal(signal.SIGINT, lambda *_args: window.quit_application())
    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(lambda: None)
    timer.start()
    with logging_sink(controller.log_handler):
        if config.start_minimized and window.tray.isVisible():
            window.hide()
        else:
            window.show()
        if config.overlay_visible:
            window.overlay.show()
        controller.start_hotkeys()
        if config.bot_autostart:
            controller.start_bot()
        app.setProperty("fmvEventLoopRunning", True)
        try:
            return app.exec()
        finally:
            app.setProperty("fmvEventLoopRunning", False)
