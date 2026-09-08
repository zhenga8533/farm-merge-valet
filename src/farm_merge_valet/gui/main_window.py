"""GUI-first Farm Merge Valet desktop application."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QSystemTrayIcon,
    QWidget,
)

from farm_merge_valet.config import AppConfig
from farm_merge_valet.config.hotkeys import display_hotkey
from farm_merge_valet.gui.branding import app_icon
from farm_merge_valet.gui.config_sections import SECTION_FIELDS, ConfigSection, reset_config_section
from farm_merge_valet.gui.controller import (
    ApplicationController,
    ApplicationState,
    ApplicationStatus,
)
from farm_merge_valet.gui.overlay import CompactOverlay
from farm_merge_valet.gui.pages import (
    BrowserPage,
    BuildingsPage,
    DashboardPage,
    ItemsPage,
    LogsPage,
    MarketplacePage,
    SettingsPage,
    ShopsPage,
)
from farm_merge_valet.gui.pages.base import ConfigEdit
from farm_merge_valet.gui.services.assets import CatalogIconLoader
from farm_merge_valet.gui.services.config_saver import ConfigSaver
from farm_merge_valet.gui.services.log_export import save_visible_log
from farm_merge_valet.gui.theme import apply_theme, refresh_widget_theme
from farm_merge_valet.observability.logging import configure_logging

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
_HOTKEY_FIELDS = {"start_stop_hotkey", "pause_hotkey", "quit_hotkey"}
_VIEW_FIELDS = set(SECTION_FIELDS[ConfigSection.VIEW])
_ITEM_VIEW_FIELDS = {field for field in _VIEW_FIELDS if field.startswith("items_")}
_SHOP_VIEW_FIELDS = {field for field in _VIEW_FIELDS if field.startswith("shops_")}
_ITEM_FIELDS = set(SECTION_FIELDS[ConfigSection.ITEMS]) | _ITEM_VIEW_FIELDS | {"catalog_dir"}
_ITEM_POLICY_FIELDS = _ITEM_FIELDS - {"items_sort_column", "items_sort_descending"}
_SHOP_FIELDS = set(SECTION_FIELDS[ConfigSection.SHOPS]) | _SHOP_VIEW_FIELDS | {"catalog_dir"}
_SHOP_POLICY_FIELDS = _SHOP_FIELDS - {"shops_sort_column", "shops_sort_descending"}
_BUILDING_VIEW_FIELDS = {field for field in _VIEW_FIELDS if field.startswith("buildings_")}
_BUILDING_FIELDS = (
    set(SECTION_FIELDS[ConfigSection.BUILDINGS]) | _BUILDING_VIEW_FIELDS | {"catalog_dir"}
)
_BUILDING_POLICY_FIELDS = _BUILDING_FIELDS - {
    "buildings_sort_column",
    "buildings_sort_descending",
}
_MARKETPLACE_VIEW_FIELDS = {
    field for field in _VIEW_FIELDS if field.startswith("marketplace_")
}
_MARKETPLACE_FIELDS = (
    set(SECTION_FIELDS[ConfigSection.MARKETPLACE]) | _MARKETPLACE_VIEW_FIELDS
)
_BROWSER_FIELDS = set(SECTION_FIELDS[ConfigSection.BROWSER])
_SETTINGS_FIELDS = set(SECTION_FIELDS[ConfigSection.SETTINGS])


def _menu_action_text(label: str, hotkey: str | None) -> str:
    if hotkey is None:
        return label
    return f"{label}\t{display_hotkey(hotkey)}"


class MainWindow(QMainWindow):
    _NAVIGATION = (
        "Dashboard", "Items", "Shops", "Buildings", "Marketplace", "Browser", "Settings", "Logs"
    )

    def __init__(
        self,
        controller: ApplicationController,
        *,
        eager_catalog_pages: bool = True,
    ) -> None:
        super().__init__()
        self.controller = controller
        self._draft = controller.config
        self._persisted_config = controller.config
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setQuitOnLastWindowClosed(False)
            if app.property("fmvTheme") != self._draft.theme:
                apply_theme(app, self._draft.theme)
        self._really_quit = False
        self._pending_save_sections: set[str] = set()
        self._configured_log_level = controller.config.log_level
        self._config_saver = ConfigSaver(controller.store, self)
        self._config_saver.saved.connect(self._config_save_completed)
        self._config_saver.failed.connect(self._config_save_failed)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self._save_config_async)
        self.setWindowTitle("Farm Merge Valet")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1000, 560)
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
        self._catalog_icons = CatalogIconLoader(self._draft.catalog_dir)
        self.items_page = ItemsPage(
            self._draft,
            icons=self._catalog_icons,
            populate_immediately=eager_catalog_pages,
        )
        self.shops_page = ShopsPage(
            self._draft,
            icons=self._catalog_icons,
            populate_immediately=eager_catalog_pages,
        )
        self.buildings_page = BuildingsPage(self._draft, icons=self._catalog_icons)
        self.marketplace_page = MarketplacePage(self._draft, icons=self._catalog_icons)
        self.browser_page = BrowserPage(self._draft)
        self.settings_page = SettingsPage(self._draft)
        self.logs_page = LogsPage(self._draft.log_level)
        for page in (
            self.dashboard_page,
            self.items_page,
            self.shops_page,
            self.buildings_page,
            self.marketplace_page,
            self.browser_page,
            self.settings_page,
            self.logs_page,
        ):
            self.pages.addWidget(page)
        self.navigation.currentRowChanged.connect(self._set_current_page)
        self.navigation.setCurrentRow(0)
        self.overlay = CompactOverlay(self._draft)
        self._connect_pages()
        self._setup_tray()
        controller.status_changed.connect(self._status_changed)
        controller.log_received.connect(self._append_log)
        controller.config_changed.connect(self._config_changed)
        controller.error.connect(self._show_error)
        controller.application_quit_requested.connect(self.quit_application)
        controller.browser_operation_changed.connect(self._browser_operation_changed)
        controller.browser_status_changed.connect(self.browser_page.status_label.setText)
        controller.browser_state_changed.connect(self.browser_page.set_browser_status)
        controller.game_sync_operation_changed.connect(self._game_sync_operation_changed)
        controller.game_sync_status_changed.connect(self._game_sync_status_changed)
        controller.catalog_setup_failed.connect(self._catalog_setup_failed)
        controller.catalog_refreshed.connect(self._catalog_metadata_refreshed)
        controller.assets_refreshed.connect(self._catalog_assets_refreshed)
        controller.upgrade_progress_changed.connect(self.items_page.set_upgrade_progress)
        controller.building_repairs_changed.connect(self.shops_page.set_building_repairs)
        controller.building_repairs_changed.connect(self.buildings_page.set_building_repairs)
        controller.shutdown_complete.connect(self._finish_quit)
        self._status_changed(controller.status)
        self.dashboard_page.set_overlay_visible(self._draft.overlay_visible)
        self._apply_hotkey_hints(self._draft)
        self._apply_appearance()

    def _set_current_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        page = self.pages.currentWidget()
        if page is self.items_page:
            self.items_page.ensure_populated(deferred=True)
        elif page is self.shops_page:
            self.shops_page.ensure_populated(deferred=True)
        if page is self.browser_page and not self.browser_page.browser_status_known:
            self.controller.refresh_browser()
        app = QApplication.instance()
        if (
            isinstance(page, QWidget)
            and isinstance(app, QApplication)
            and page.property("fmvTheme") != app.property("fmvTheme")
        ):
            refresh_widget_theme(page, str(app.property("fmvTheme")))

    def _connect_pages(self) -> None:
        self.dashboard_page.run_requested.connect(self.controller.toggle_running)
        self.dashboard_page.pause_requested.connect(self.controller.toggle_pause)
        self.dashboard_page.overlay_requested.connect(self._toggle_overlay)
        self.overlay.run_requested.connect(self.controller.toggle_running)
        self.overlay.pause_requested.connect(self.controller.toggle_pause)
        self.overlay.close_requested.connect(lambda: self._set_overlay_visible(False))
        self.items_page.config_edited.connect(self._queue_edit)
        self.items_page.catalog_setup_requested.connect(self.controller.setup_catalog)
        self.items_page.reset_requested.connect(self._reset_item_policies)
        self.shops_page.config_edited.connect(self._queue_edit)
        self.shops_page.catalog_setup_requested.connect(self.controller.setup_catalog)
        self.shops_page.reset_requested.connect(self._reset_shop_policies)
        self.buildings_page.config_edited.connect(self._queue_edit)
        self.buildings_page.catalog_setup_requested.connect(self.controller.setup_catalog)
        self.buildings_page.reset_requested.connect(self._reset_building_policies)
        self.marketplace_page.config_edited.connect(self._queue_edit)
        self.marketplace_page.reset_requested.connect(self._reset_marketplace_policies)
        self.browser_page.config_edited.connect(self._queue_edit)
        self.browser_page.refresh_requested.connect(self.controller.refresh_browser)
        self.browser_page.launch_requested.connect(self.controller.launch_browser)
        self.browser_page.stop_requested.connect(self._confirm_browser_stop)
        self.browser_page.restart_requested.connect(self._confirm_browser_restart)
        self.browser_page.game_sync_requested.connect(self.controller.synchronize_game_data)
        self.browser_page.reset_requested.connect(self._reset_browser_configuration)
        self.settings_page.config_edited.connect(self._queue_edit)
        self.settings_page.hotkey_recording_changed.connect(self.controller.set_hotkey_recording)
        self.settings_page.reset_requested.connect(self._reset_general_settings)
        self.settings_page.reset_all_requested.connect(self._reset_all_settings)
        self.logs_page.log_level_changed.connect(lambda value: self._queue_config(log_level=value))
        self.logs_page.save_requested.connect(self._save_logs)

    def _confirm_browser_stop(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Stop managed browser?",
                "This closes only the verified Farm Merge Valet browser profile. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.controller.stop_browser()

    def _confirm_browser_restart(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Restart managed browser?",
                "This closes only the verified Farm Merge Valet browser profile. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.controller.restart_browser()

    def _confirm_reset(self, title: str, message: str) -> bool:
        return (
            QMessageBox.question(
                self,
                title,
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Yes
        )

    def _reset_item_policies(self) -> None:
        self._reset_section(
            ConfigSection.ITEMS,
            "Reset item policies?",
            "Restore recommended item behavior and remove all custom item choices?",
        )

    def _reset_shop_policies(self) -> None:
        self._reset_section(
            ConfigSection.SHOPS,
            "Reset shop policies?",
            "Restore recommended shop and recipe behavior and remove all custom choices?",
        )

    def _reset_marketplace_policies(self) -> None:
        self._reset_section(
            ConfigSection.MARKETPLACE,
            "Reset marketplace policies?",
            "Disable every marketplace auto-purchase policy?",
        )

    def _reset_building_policies(self) -> None:
        self._reset_section(
            ConfigSection.BUILDINGS,
            "Reset building policies?",
            "Restore recommended repair priorities and enable every building?",
        )

    def _reset_browser_configuration(self) -> None:
        self._reset_section(
            ConfigSection.BROWSER,
            "Reset browser configuration?",
            "Restore browser, game connection, and asset-cache configuration?",
        )

    def _reset_general_settings(self) -> None:
        self._reset_section(
            ConfigSection.SETTINGS,
            "Reset settings?",
            "Restore automation, shortcut, startup, notification, and appearance settings? "
            "Browser, item, and shop configuration will be preserved.",
        )

    def _reset_section(self, section: ConfigSection, title: str, message: str) -> None:
        if not self._confirm_reset(title, message):
            return
        if self._replace_config(reset_config_section(self._draft, section)):
            headers = {
                ConfigSection.ITEMS: self.items_page.configuration_header,
                ConfigSection.SHOPS: self.shops_page.configuration_header,
                ConfigSection.BUILDINGS: self.buildings_page.configuration_header,
                ConfigSection.MARKETPLACE: self.marketplace_page.configuration_header,
                ConfigSection.BROWSER: self.browser_page.configuration_header,
                ConfigSection.SETTINGS: self.settings_page.configuration_header,
            }
            headers[section].mark_reset()

    def _reset_all_settings(self) -> None:
        if self._confirm_reset(
            "Reset everything?",
            "Restore every Farm Merge Valet setting and remove all item, shop, building, "
            "and recipe overrides?",
        ):
            if self._replace_config(AppConfig()):
                self.settings_page.configuration_header.mark_reset()

    def _replace_config(self, config: AppConfig) -> bool:
        self._save_timer.stop()
        try:
            self._config_saver.save_now(config)
        except (OSError, ValueError) as exc:
            self._show_error(f"Could not reset settings: {exc}")
            return False
        return True

    def _queue_edit(self, edit: ConfigEdit) -> None:
        editor: (
            ItemsPage
            | ShopsPage
            | BuildingsPage
            | MarketplacePage
            | BrowserPage
            | SettingsPage
        )
        if edit.source == "items":
            editor = self.items_page
        elif edit.source == "shops":
            editor = self.shops_page
        elif edit.source == "buildings":
            editor = self.buildings_page
        elif edit.source == "marketplace":
            editor = self.marketplace_page
        elif edit.source == "browser":
            editor = self.browser_page
        else:
            editor = self.settings_page
        try:
            candidate = self._draft.model_copy(update=edit.changes)
            validated = AppConfig.model_validate(candidate.model_dump())
        except ValidationError as exc:
            message = exc.errors()[0]["msg"]
            if isinstance(editor, (BrowserPage, SettingsPage)):
                editor.show_validation_error(edit.field, message, self._draft)
            else:
                editor.mark_error(f"Invalid: {message}")
            return
        if validated == self._draft:
            editor.configuration_header.mark_saved()
            return
        self._draft = validated
        editor.mark_saving(edit.field)
        self._pending_save_sections.add(edit.source)
        if self._config_saver.busy:
            self._config_saver.request(self._draft)
        self._save_timer.start()
        if _APPEARANCE_FIELDS.intersection(edit.changes):
            self.overlay.apply_config(self._draft)
            self._apply_appearance()

    def _queue_config(self, **changes: object) -> None:
        field = next(iter(changes)) if len(changes) == 1 else None
        self._queue_edit(ConfigEdit(changes, field))

    def _flush_config(self) -> None:
        try:
            self._config_saver.save_now(self._draft)
        except (OSError, ValueError) as exc:
            self._config_save_failed(str(exc))

    def _save_config_async(self) -> None:
        self._config_saver.request(self._draft)

    def _config_save_completed(self, config: AppConfig) -> None:
        headers = {
            "items": self.items_page.configuration_header,
            "shops": self.shops_page.configuration_header,
            "buildings": self.buildings_page.configuration_header,
            "marketplace": self.marketplace_page.configuration_header,
            "browser": self.browser_page.configuration_header,
            "settings": self.settings_page.configuration_header,
        }
        for section in self._pending_save_sections:
            headers[section].mark_saved()
        self._pending_save_sections.clear()
        if config.log_level != self._configured_log_level:
            configure_logging(config.log_level)
            self._configured_log_level = config.log_level

    def _config_save_failed(self, message: str) -> None:
        headers = {
            "items": self.items_page.configuration_header,
            "shops": self.shops_page.configuration_header,
            "buildings": self.buildings_page.configuration_header,
            "marketplace": self.marketplace_page.configuration_header,
            "browser": self.browser_page.configuration_header,
            "settings": self.settings_page.configuration_header,
        }
        targets = self._pending_save_sections or set(headers)
        for section in targets:
            headers[section].mark_error("Save failed")
        self._show_error(f"Could not save settings: {message}")

    def _config_changed(self, config: AppConfig) -> None:
        previous = self._persisted_config
        changed_fields = {
            field
            for field in AppConfig.model_fields
            if getattr(previous, field) != getattr(config, field)
        }
        if not changed_fields:
            return
        self._persisted_config = config
        self._draft = config
        if changed_fields & _ITEM_FIELDS:
            self.items_page.apply_config(
                config,
                refresh=bool(changed_fields & _ITEM_POLICY_FIELDS),
            )
        if changed_fields & _SHOP_FIELDS:
            self.shops_page.apply_config(
                config,
                refresh=bool(changed_fields & _SHOP_POLICY_FIELDS),
            )
        if changed_fields & _BUILDING_FIELDS:
            self.buildings_page.apply_config(
                config,
                refresh=bool(changed_fields & _BUILDING_POLICY_FIELDS),
            )
        if changed_fields & _MARKETPLACE_FIELDS:
            self.marketplace_page.apply_config(config)
        if changed_fields & _SETTINGS_FIELDS:
            self.settings_page.apply_config(config)
        if "log_level" in changed_fields:
            self.logs_page.apply_log_level(config.log_level)
        if changed_fields & _BROWSER_FIELDS:
            self.browser_page.apply_config(config)
        if changed_fields & _APPEARANCE_FIELDS:
            self.overlay.apply_config(config)
            self._apply_appearance()
        if changed_fields & _HOTKEY_FIELDS:
            self._apply_hotkey_hints(config)

    def _apply_hotkey_hints(self, config: AppConfig) -> None:
        self.dashboard_page.set_hotkeys(config.start_stop_hotkey, config.pause_hotkey)
        self.overlay.set_hotkeys(config.start_stop_hotkey, config.pause_hotkey)
        if hasattr(self, "run_action"):
            self.run_action.setText(_menu_action_text("Start bot", config.start_stop_hotkey))
            self.quit_action.setText(_menu_action_text("Quit", config.quit_hotkey))
            self._status_changed(self.controller.status)

    def _apply_appearance(self) -> None:
        app = QApplication.instance()
        theme_changed = False
        if isinstance(app, QApplication) and app.property("fmvTheme") != self._draft.theme:
            apply_theme(app, self._draft.theme)
            theme_changed = True
            current_page = self.pages.currentWidget()
            if isinstance(current_page, QWidget):
                refresh_widget_theme(current_page, self._draft.theme)
            refresh_widget_theme(self.navigation, self._draft.theme)
            refresh_widget_theme(self.overlay, self._draft.theme)
        if theme_changed:
            self.logs_page.refresh_presentation()
            self.overlay.refresh_log_presentation()
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
        if hasattr(self, "run_action"):
            active = status.state.active
            enabled = active and status.state is not ApplicationState.STOPPING
            run_label = "Stop bot" if active else "Start bot"
            self.run_action.setText(_menu_action_text(run_label, self._draft.start_stop_hotkey))
            self.run_action.setEnabled(status.state is not ApplicationState.STOPPING)
            self.pause_action.setEnabled(enabled)
            pause_label = (
                "Resume bot"
                if status.state in {ApplicationState.PAUSED, ApplicationState.RESUMING}
                else "Pause bot"
            )
            self.pause_action.setText(_menu_action_text(pause_label, self._draft.pause_hotkey))
            self.browser_page.set_runtime_active(active)

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
        self.tray = QSystemTrayIcon(app_icon(), self)
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        self.tray.setContextMenu(menu)
        show_action = QAction("Show dashboard", self)
        self.run_action = QAction("Start bot", self)
        self.pause_action = QAction("Pause bot", self)
        overlay_action = QAction("Toggle compact overlay", self)
        self.quit_action = QAction("Quit", self)
        show_action.triggered.connect(self._show_dashboard)
        self.run_action.triggered.connect(self.controller.toggle_running)
        self.pause_action.triggered.connect(self.controller.toggle_pause)
        overlay_action.triggered.connect(self._toggle_overlay)
        self.quit_action.triggered.connect(self.quit_application)
        menu.addActions((show_action, self.run_action, self.pause_action, overlay_action))
        menu.addSeparator()
        menu.addAction(self.quit_action)
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

    def _game_sync_operation_changed(self, busy: bool) -> None:
        self.browser_page.set_game_sync_busy(busy)
        self.browser_page.set_runtime_active(self.controller.status.state.active)
        for page in self._catalog_pages():
            page.set_catalog_setup_busy(busy)

    def _game_sync_status_changed(self, message: str) -> None:
        self.browser_page.set_game_sync_status(message)
        for page in self._catalog_pages():
            page.set_catalog_setup_status(message)

    def _catalog_setup_failed(self, message: str) -> None:
        for page in self._catalog_pages():
            page.set_catalog_setup_status(message, error=True)

    def _catalog_pages(self) -> tuple[ItemsPage, ShopsPage, BuildingsPage]:
        return self.items_page, self.shops_page, self.buildings_page

    def _catalog_metadata_refreshed(self, *_args: object) -> None:
        self._catalog_icons.clear()
        for page in self._catalog_pages():
            page.reload_catalog_if_missing()
        self.marketplace_page.reload_catalog_if_missing()

    def _catalog_assets_refreshed(self, *_args: object) -> None:
        self._catalog_icons.clear()
        for page in self._catalog_pages():
            page.reload_catalog()
        self.marketplace_page.reload_catalog()

    def _save_logs(self) -> None:
        output, _filter = QFileDialog.getSaveFileName(
            self,
            "Save logs",
            "farm-merge-valet.log",
            "Log files (*.log);;Text files (*.txt)",
        )
        if not output:
            return
        try:
            saved = save_visible_log(Path(output), self.logs_page.view.toPlainText())
        except OSError as exc:
            self.logs_page.set_save_error(str(exc))
            self._show_error(str(exc))
        else:
            self.logs_page.set_save_result(str(saved))

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
        self._save_timer.stop()
        try:
            self._config_saver.shutdown(self._draft)
        except (OSError, ValueError) as exc:
            self._config_save_failed(str(exc))
        self.setEnabled(False)
        self.overlay.setEnabled(False)
        self.tray.setToolTip("Farm Merge Valet — Shutting down")
        self.controller.shutdown()

    def _finish_quit(self) -> None:
        self.tray.hide()
        self.overlay.hide()
        self.overlay.deleteLater()
        self.close()
        self.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app = QApplication.instance()
        if isinstance(app, QApplication) and app.property("fmvEventLoopRunning") is True:
            app.quit()
