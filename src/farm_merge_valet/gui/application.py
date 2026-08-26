"""GUI-first Farm Merge Valet desktop application."""

from __future__ import annotations

import signal
import sys

from pydantic import ValidationError
from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.gui.controller import (
    ApplicationController,
    ApplicationState,
    ApplicationStatus,
)
from farm_merge_valet.gui.pages import (
    BrowserPage,
    ConfigEdit,
    DashboardPage,
    ItemsPage,
    LogsPage,
    SettingsPage,
    ShopsPage,
    secondary_button,
)
from farm_merge_valet.gui.theme import apply_theme
from farm_merge_valet.logging_setup import configure_logging, logging_sink

_APPEARANCE_FIELDS = {
    "theme",
    "main_always_on_top",
    "main_focused_opacity",
    "main_unfocused_opacity",
    "overlay_always_on_top",
    "overlay_click_through",
    "overlay_focused_opacity",
    "overlay_unfocused_opacity",
}


def _app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#238636"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(18, 16, 18, 32)
    painter.drawEllipse(30, 16, 18, 32)
    painter.end()
    return QIcon(pixmap)


class CompactOverlay(QMainWindow):
    start_requested = Signal()
    pause_requested = Signal()
    stop_requested = Signal()
    close_requested = Signal()

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self.setWindowTitle("Farm Merge Valet · Compact")
        self.status = QLabel("Stopped")
        self.status.setObjectName("overlayStatus")
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(30)
        self.logs.setAccessibleName("Recent application activity")
        self.start_button = QPushButton("Start")
        self.pause_button = secondary_button("Pause")
        self.stop_button = secondary_button("Stop")
        self.start_button.clicked.connect(self.start_requested)
        self.pause_button.clicked.connect(self.pause_requested)
        self.stop_button.clicked.connect(self.stop_requested)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self.status)
        layout.addWidget(self.logs, 1)
        controls = QHBoxLayout()
        controls.addWidget(self.start_button)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.stop_button)
        controls.addStretch()
        layout.addLayout(controls)
        self.setCentralWidget(root)
        self.resize(480, 260)
        self.set_status(ApplicationStatus())
        self.apply_config(config)

    def apply_config(self, config: AppConfig) -> None:
        self._config = config
        always_on_top = Qt.WindowType.WindowStaysOnTopHint
        if bool(self.windowFlags() & always_on_top) != config.overlay_always_on_top:
            visible = self.isVisible()
            self.setWindowFlag(always_on_top, config.overlay_always_on_top)
            if visible:
                self.show()
        self._apply_focus_state()

    def set_status(self, status: ApplicationStatus) -> None:
        self.status.setText(f"{status.mode} · {status.phase}")
        active = status.state.active
        enabled = active and status.state is not ApplicationState.STOPPING
        self.start_button.setEnabled(not active)
        self.pause_button.setEnabled(enabled)
        self.stop_button.setEnabled(enabled)
        self.pause_button.setText(
            "Resume"
            if status.state in {ApplicationState.PAUSED, ApplicationState.RESUMING}
            else "Pause"
        )

    def _apply_focus_state(self) -> None:
        self.setWindowOpacity(
            self._config.overlay_focused_opacity
            if self.isActiveWindow()
            else self._config.overlay_unfocused_opacity
        )
        if sys.platform == "win32":
            from farm_merge_valet.gui.native_window import set_click_through

            set_click_through(
                int(self.winId()),
                self._config.overlay_click_through and not self.isActiveWindow(),
            )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange:
            self._apply_focus_state()

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.hide()
        self.close_requested.emit()

    def append_log(self, timestamp: str, level: str, message: str, _levelno: int) -> None:
        self.logs.appendPlainText(f"[{timestamp}] {level:<8} {message}")


class MainWindow(QMainWindow):
    _NAVIGATION = ("Dashboard", "Items", "Shops", "Browser", "Settings", "Logs")

    def __init__(self, controller: ApplicationController) -> None:
        super().__init__()
        self.controller = controller
        self._draft = controller.config
        self._really_quit = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self._flush_config)
        self.setWindowTitle("Farm Merge Valet")
        self.setWindowIcon(_app_icon())
        self.setMinimumSize(820, 560)
        self.resize(1100, 740)

        root = QWidget()
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(176)
        self.navigation.addItems(self._NAVIGATION)
        self.navigation.setAccessibleName("Application sections")
        self.pages = QStackedWidget()
        shell.addWidget(self.navigation)
        shell.addWidget(self.pages, 1)
        self.setCentralWidget(root)

        self.dashboard_page = DashboardPage()
        self.items_page = ItemsPage(self._draft)
        self.shops_page = ShopsPage(self._draft)
        self.browser_page = BrowserPage(self._draft)
        self.settings_page = SettingsPage(self._draft)
        self.logs_page = LogsPage(self._draft.log_level)
        for page in (
            self.dashboard_page,
            self.items_page,
            self.shops_page,
            self.browser_page,
            self.settings_page,
            self.logs_page,
        ):
            self.pages.addWidget(page)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self._publish_compatibility_handles()

        self.overlay = CompactOverlay(self._draft)
        self._connect_pages()
        self._setup_tray()
        controller.status_changed.connect(self._status_changed)
        controller.log_received.connect(self._append_log)
        controller.config_changed.connect(self._config_changed)
        controller.error.connect(self._show_error)
        controller.application_quit_requested.connect(self.quit_application)
        controller.browser_operation_changed.connect(self._browser_operation_changed)
        controller.browser_status_changed.connect(self.browser_status_label.setText)
        controller.shutdown_complete.connect(self._finish_quit)
        self._status_changed(controller.status)
        self.dashboard_page.set_overlay_visible(self._draft.overlay_visible)
        self._apply_appearance()

    def _publish_compatibility_handles(self) -> None:
        self.item_table = self.items_page.table
        self.item_search = self.items_page.search
        self.shop_tree = self.shops_page.tree
        self.browser_status_label = self.browser_page.status_label
        self.browser_choice = self.browser_page.browser_choice
        self.saved_label = self.settings_page.saved_label
        self.log_view = self.logs_page.view
        self.log_filter = self.logs_page.filter
        self.start_button = self.dashboard_page.start_button
        self.pause_button = self.dashboard_page.pause_button
        self.stop_button = self.dashboard_page.stop_button
        self.overlay_button = self.dashboard_page.overlay_button
        self.mode_value = self.dashboard_page.mode_value
        self.browser_value = self.dashboard_page.browser_value
        self.runtime_value = self.dashboard_page.runtime_value
        self.phase_value = self.dashboard_page.phase_value
        self.activity_value = self.dashboard_page.activity_value

    def _connect_pages(self) -> None:
        self.dashboard_page.start_requested.connect(self.controller.start_bot)
        self.dashboard_page.pause_requested.connect(self.controller.toggle_pause)
        self.dashboard_page.stop_requested.connect(self.controller.stop_bot)
        self.dashboard_page.overlay_requested.connect(self._toggle_overlay)
        self.overlay.start_requested.connect(self.controller.start_bot)
        self.overlay.pause_requested.connect(self.controller.toggle_pause)
        self.overlay.stop_requested.connect(self.controller.stop_bot)
        self.overlay.close_requested.connect(lambda: self._set_overlay_visible(False))
        self.items_page.config_edited.connect(self._queue_edit)
        self.items_page.reset_requested.connect(self._reset_item_policies)
        self.shops_page.config_edited.connect(self._queue_edit)
        self.shops_page.reset_requested.connect(self._reset_shop_policies)
        self.browser_page.config_edited.connect(self._queue_edit)
        self.browser_page.refresh_requested.connect(self.controller.refresh_browser)
        self.browser_page.restart_requested.connect(self._confirm_browser_restart)
        self.settings_page.config_edited.connect(self._queue_edit)
        self.settings_page.reset_requested.connect(self._reset_general_settings)
        self.settings_page.reset_all_requested.connect(self._reset_all_settings)
        self.logs_page.log_level_changed.connect(lambda value: self._queue_config(log_level=value))

    def _confirm_browser_restart(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Restart managed browser?",
                "This closes only the verified Farm Merge Valet browser profile. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            is QMessageBox.StandardButton.Yes
        ):
            self.controller.restart_browser()

    def _refresh_browser(self) -> None:
        self.controller.refresh_browser()

    def _confirm_reset(self, title: str, message: str) -> bool:
        return (
            QMessageBox.question(
                self,
                title,
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            is QMessageBox.StandardButton.Yes
        )

    def _reset_item_policies(self) -> None:
        if not self._confirm_reset(
            "Reset item policies?",
            "Restore item, category, and global item defaults and remove all item overrides?",
        ):
            return
        defaults = AppConfig()
        self._replace_config(
            self._draft.model_copy(
                update={
                    "item_policy_defaults": defaults.item_policy_defaults,
                    "item_category_defaults": defaults.item_category_defaults,
                    "item_default_overrides": defaults.item_default_overrides,
                    "item_policy_overrides": {},
                }
            )
        )

    def _reset_shop_policies(self) -> None:
        if not self._confirm_reset(
            "Reset shop policies?",
            "Restore shop and recipe defaults and remove all shop and recipe overrides?",
        ):
            return
        defaults = AppConfig()
        self._replace_config(
            self._draft.model_copy(
                update={
                    "shop_default_enabled": defaults.shop_default_enabled,
                    "recipe_default_enabled": defaults.recipe_default_enabled,
                    "shop_overrides": {},
                    "recipe_overrides": {},
                }
            )
        )

    def _reset_general_settings(self) -> None:
        if not self._confirm_reset(
            "Reset settings?",
            "Restore browser, automation timing, notification, and appearance settings? "
            "Item and shop policies will be preserved.",
        ):
            return
        policy_fields = {
            "item_policy_defaults",
            "item_category_defaults",
            "item_default_overrides",
            "item_policy_overrides",
            "shop_default_enabled",
            "recipe_default_enabled",
            "shop_overrides",
            "recipe_overrides",
        }
        defaults = AppConfig()
        changes = {
            field: getattr(defaults, field)
            for field in AppConfig.model_fields
            if field != "schema_version" and field not in policy_fields
        }
        self._replace_config(self._draft.model_copy(update=changes))

    def _reset_all_settings(self) -> None:
        if self._confirm_reset(
            "Reset everything?",
            "Restore every Farm Merge Valet setting and remove all item, shop, and recipe "
            "overrides?",
        ):
            self._replace_config(AppConfig())

    def _replace_config(self, config: AppConfig, _page_names: set[str] | None = None) -> None:
        self._save_timer.stop()
        try:
            self.controller.store.replace(config)
        except (OSError, ValueError) as exc:
            self._show_error(f"Could not reset settings: {exc}")

    def _reset_item(self, key: str) -> None:
        self.items_page.reset_item(key)

    def _reset_shop_item(self, kind: str, key: str) -> None:
        self.shops_page.reset_item(kind, key)

    def _queue_edit(self, edit: ConfigEdit) -> None:
        try:
            candidate = self._draft.model_copy(update=edit.changes)
            self._draft = AppConfig.model_validate(candidate.model_dump())
        except ValidationError as exc:
            self.settings_page.show_validation_error(
                edit.field,
                exc.errors()[0]["msg"],
                self._draft,
            )
            return
        self.settings_page.mark_saving(edit.field)
        self._save_timer.start()
        if _APPEARANCE_FIELDS.intersection(edit.changes):
            self.overlay.apply_config(self._draft)
            self._apply_appearance()

    def _queue_config(self, **changes: object) -> None:
        field = next(iter(changes)) if len(changes) == 1 else None
        self._queue_edit(ConfigEdit(changes, field))

    def _flush_config(self) -> None:
        try:
            self.controller.store.replace(self._draft)
        except (OSError, ValueError) as exc:
            self.saved_label.setText("Save failed")
            self._show_error(f"Could not save settings: {exc}")
            return
        configure_logging(self._draft.log_level)

    def _config_changed(self, config: AppConfig) -> None:
        previous = self._draft
        self._draft = config
        item_fields = (
            "catalog_dir",
            "item_policy_defaults",
            "item_category_defaults",
            "item_default_overrides",
            "item_policy_overrides",
            "prefer_merge_five",
        )
        shop_fields = (
            "catalog_dir",
            "shop_default_enabled",
            "recipe_default_enabled",
            "shop_overrides",
            "recipe_overrides",
        )
        self.items_page.apply_config(
            config,
            refresh=any(
                getattr(previous, field) != getattr(config, field) for field in item_fields
            ),
        )
        self.shops_page.apply_config(
            config,
            refresh=any(
                getattr(previous, field) != getattr(config, field) for field in shop_fields
            ),
        )
        self.settings_page.apply_config(config)
        self.overlay.apply_config(config)
        self._apply_appearance()

    def _apply_appearance(self) -> None:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app, self._draft.theme)
        always_on_top = Qt.WindowType.WindowStaysOnTopHint
        if bool(self.windowFlags() & always_on_top) != self._draft.main_always_on_top:
            visible = self.isVisible()
            self.setWindowFlag(always_on_top, self._draft.main_always_on_top)
            if visible:
                self.show()
        self._apply_main_opacity()

    def _apply_main_opacity(self) -> None:
        self.setWindowOpacity(
            self._draft.main_focused_opacity
            if self.isActiveWindow()
            else self._draft.main_unfocused_opacity
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange:
            self._apply_main_opacity()

    def _status_changed(self, status: ApplicationStatus) -> None:
        self.dashboard_page.set_status(status)
        self.overlay.set_status(status)
        if hasattr(self, "tray"):
            self.tray.setToolTip(f"Farm Merge Valet — {status.mode}")
        if hasattr(self, "start_action"):
            active = status.state.active
            enabled = active and status.state is not ApplicationState.STOPPING
            self.start_action.setEnabled(not active)
            self.pause_action.setEnabled(enabled)
            self.pause_action.setText(
                "Resume bot"
                if status.state in {ApplicationState.PAUSED, ApplicationState.RESUMING}
                else "Pause bot"
            )
            self.stop_action.setEnabled(enabled)
            self.browser_page.set_runtime_active(active)

    def _running_changed(self, _running: bool, _paused: bool) -> None:
        self._status_changed(self.controller.status)

    def _append_log(self, timestamp: str, level: str, message: str, levelno: int) -> None:
        self.logs_page.append(timestamp, level, message, levelno)
        self.overlay.append_log(timestamp, level, message, levelno)

    def _set_overlay_visible(self, visible: bool) -> None:
        self.overlay.setVisible(visible)
        self.dashboard_page.set_overlay_visible(visible)
        self._queue_config(overlay_visible=visible)

    def _toggle_overlay(self) -> None:
        self._set_overlay_visible(not self.overlay.isVisible())

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(_app_icon(), self)
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        self.tray.setContextMenu(menu)
        show_action = QAction("Show dashboard", self)
        self.start_action = QAction("Start bot", self)
        self.pause_action = QAction("Pause bot", self)
        self.stop_action = QAction("Stop bot", self)
        overlay_action = QAction("Toggle compact overlay", self)
        quit_action = QAction("Quit", self)
        show_action.triggered.connect(self._show_dashboard)
        self.start_action.triggered.connect(self.controller.start_bot)
        self.pause_action.triggered.connect(self.controller.toggle_pause)
        self.stop_action.triggered.connect(self.controller.stop_bot)
        overlay_action.triggered.connect(self._toggle_overlay)
        quit_action.triggered.connect(self.quit_application)
        menu.addActions(
            (show_action, self.start_action, self.pause_action, self.stop_action, overlay_action)
        )
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.activated.connect(self._tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self._show_dashboard()

    def _browser_operation_changed(self, busy: bool) -> None:
        self.browser_page.set_busy(busy)
        self.browser_page.set_runtime_active(self.controller.status.state.active)

    def _show_dashboard(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Farm Merge Valet", message)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._really_quit:
            event.accept()
            return
        if self._draft.close_to_tray and self.tray.isVisible():
            event.ignore()
            self.hide()
            return
        event.ignore()
        self.quit_application()

    def quit_application(self) -> None:
        if self._really_quit:
            return
        self._really_quit = True
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._flush_config()
        self.setEnabled(False)
        self.overlay.setEnabled(False)
        self.tray.setToolTip("Farm Merge Valet — Shutting down")
        self.controller.shutdown()

    def _finish_quit(self) -> None:
        self.tray.hide()
        QApplication.quit()


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
    window = MainWindow(controller)
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
        if config.bot_autostart:
            controller.start_bot()
        return app.exec()
