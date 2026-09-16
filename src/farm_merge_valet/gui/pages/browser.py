"""Managed-browser configuration page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from farm_merge_valet.browser import BrowserStatus
from farm_merge_valet.config import AppConfig
from farm_merge_valet.gui.components.configuration_header import ConfigurationHeader
from farm_merge_valet.gui.components.input_controls import (
    FocusAwareComboBox,
)
from farm_merge_valet.gui.components.status import StatusLabel
from farm_merge_valet.gui.components.widgets import (
    secondary_button,
    set_styled_property,
    set_validation_state,
)
from farm_merge_valet.gui.pages.base import (
    ConfigEdit,
    ConfigFormPage,
    disclosure_section,
    scrollable_sections,
    settings_section,
)
from farm_merge_valet.gui.services.catalog_freshness import (
    CatalogFreshness,
    CatalogFreshnessState,
    inspect_catalog_freshness,
)
from farm_merge_valet.integrations import (
    PORTALS,
    GamePortal,
    portal_definition,
)


class BrowserPage(ConfigFormPage):
    config_edited = Signal(object)
    refresh_requested = Signal()
    launch_requested = Signal()
    stop_requested = Signal()
    restart_requested = Signal()
    game_sync_requested = Signal()
    cache_clear_requested = Signal()
    reset_requested = Signal()

    def __init__(self, config: AppConfig) -> None:
        super().__init__(
            "Browser",
            "Manage the dedicated browser, game connection, and local asset cache.",
            config,
        )
        self.configuration_header = ConfigurationHeader("Reset browser configuration")
        self.configuration_header.reset_requested.connect(self.reset_requested)
        self.saved_label = self.configuration_header.status_label
        self.page_layout.addWidget(self.configuration_header)

        sections = scrollable_sections(self.page_layout)

        managed, managed_form = settings_section("Browser connection")
        self.status_label = StatusLabel("Not checked")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Managed browser status")
        self.browser_choice = FocusAwareComboBox()
        self.browser_choice.set_choices(
            (
                ("Auto-detect", "auto"),
                ("Google Chrome", "chrome"),
                ("Microsoft Edge", "edge"),
                ("Brave", "brave"),
                ("Chromium", "chromium"),
            )
        )
        self.browser_choice.set_current_value(config.browser)
        self.browser_choice.setAccessibleName("Preferred browser")
        self.browser_choice.currentIndexChanged.connect(
            lambda _index: self._request("browser", str(self.browser_choice.current_value()))
        )
        self._register_control(
            "browser",
            self.browser_choice,
            self.browser_choice.set_current_value,
        )
        self._add_form_row(managed_form, "Status", self.status_label)
        self.portal_choice = FocusAwareComboBox()
        self.portal_choice.set_choices(
            tuple((portal.display_name, portal.kind.value) for portal in PORTALS)
        )
        self.portal_choice.set_current_value(config.game_portal.value)
        self.portal_choice.setAccessibleName("Game platform")
        self.portal_choice.currentIndexChanged.connect(self._request_portal)
        self._register_control(
            "game_portal",
            self.portal_choice,
            lambda value: self._set_portal(GamePortal(str(value))),
        )
        self._set_portal(config.game_portal)
        self._add_form_row(managed_form, "Game platform", self.portal_choice)
        self._add_form_row(managed_form, "Preferred browser", self.browser_choice)
        self._add_toggle(managed_form, "Launch automatically when needed", "browser_auto_launch")
        self._add_toggle(managed_form, "Restart frozen game automatically", "auto_recover_game")
        self._add_int(
            managed_form,
            "Recovery attempts before stopping (0 = unlimited)",
            "max_game_recovery_attempts",
            0,
            100,
        )
        buttons = QHBoxLayout()
        self.browser_action_button = QPushButton("Launch browser")
        self.restart_button = secondary_button("Restart")
        self.refresh_button = secondary_button("Refresh")
        self.browser_action_button.setAccessibleName("Launch managed browser")
        self.restart_button.setAccessibleName("Restart managed browser")
        self.refresh_button.setAccessibleName("Refresh managed browser status")
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.browser_action_button.clicked.connect(self._request_browser_action)
        self.restart_button.clicked.connect(self.restart_requested)
        buttons.addWidget(self.browser_action_button)
        buttons.addWidget(self.restart_button)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch()
        self._add_form_row(managed_form, "Actions", buttons)
        browser_advanced, form = disclosure_section("Advanced connection")
        self._add_text(form, "Game page override", "game_url")
        self._add_path(form, "Browser executable", "browser_executable")
        self._add_path(form, "Browser profile directory", "browser_profile_dir")
        self._add_text(form, "Tab title filter", "window_title")
        self._add_int(form, "CDP port", "cdp_port", 1, 65535)
        self._add_toggle(
            form,
            "Close managed browser when quitting",
            "close_managed_browser_on_exit",
        )
        self.browser_advanced_section = browser_advanced
        managed_form.addRow(browser_advanced)
        sections.addWidget(managed)

        assets, assets_form = settings_section("Game data")
        freshness = inspect_catalog_freshness(config.catalog_dir / "catalog.json")
        self.game_sync_status_label = StatusLabel(freshness.message)
        self.game_sync_status_label.setWordWrap(True)
        self.game_sync_status_label.setAccessibleName("Game data and asset status")
        self._add_form_row(assets_form, "Status", self.game_sync_status_label)
        self.game_sync_button = QPushButton("Synchronize")
        self.game_sync_button.setAccessibleName("Synchronize game data and assets")
        self.game_sync_button.clicked.connect(self.game_sync_requested)
        game_sync_actions = QHBoxLayout()
        game_sync_actions.addWidget(self.game_sync_button)
        game_sync_actions.addStretch()
        self._add_form_row(assets_form, "Actions", game_sync_actions)
        assets_advanced, form = disclosure_section("Advanced storage")
        self._add_path(form, "Catalog directory", "catalog_dir", optional=False)
        self._add_path(form, "Atlas cache directory", "atlas_cache_dir", optional=False)
        self.cache_clear_button = secondary_button("Clear cache")
        self.cache_clear_button.setAccessibleName("Clear cached game data and assets")
        set_styled_property(self.cache_clear_button, "danger", True)
        self.cache_clear_button.clicked.connect(self.cache_clear_requested)
        cache_actions = QHBoxLayout()
        cache_actions.addWidget(self.cache_clear_button)
        cache_actions.addStretch()
        self._add_form_row(form, "Cache maintenance", cache_actions)
        self.assets_advanced_section = assets_advanced
        assets_form.addRow(assets_advanced)
        sections.addWidget(assets)
        sections.addStretch()
        self._browser_busy = False
        self._game_sync_busy = False
        self._runtime_active = False
        self._browser_status: BrowserStatus | None = None
        self._sync_action_states()

    def _request_browser_action(self) -> None:
        if self._browser_status is not None and self._browser_status.running:
            self.stop_requested.emit()
        else:
            self.launch_requested.emit()

    def _set_portal(self, portal: GamePortal) -> None:
        self.portal_choice.set_current_value(portal.value)

    def _request_portal(self, _index: int) -> None:
        portal = GamePortal(str(self.portal_choice.current_value()))
        definition = portal_definition(portal)
        self._set_portal(portal)
        self.config_edited.emit(
            ConfigEdit(
                {
                    "game_portal": portal,
                    "game_url": definition.canonical_url,
                    "window_title": "",
                },
                "game_portal",
                "browser",
            )
        )

    def set_browser_status(self, status: BrowserStatus) -> None:
        self._browser_status = status
        self._sync_action_states()

    @property
    def browser_status_known(self) -> bool:
        return self._browser_status is not None

    def set_game_sync_status(self, status: str) -> None:
        self.game_sync_status_label.set_status(status)

    def set_catalog_freshness(self, freshness: CatalogFreshness) -> None:
        self.game_sync_status_label.set_status(freshness.message)
        self.game_sync_button.setText(
            "Update" if freshness.state is CatalogFreshnessState.OUTDATED else "Synchronize"
        )

    def set_game_sync_busy(self, busy: bool) -> None:
        self._game_sync_busy = busy
        self._sync_action_states()

    def _sync_action_states(self) -> None:
        status = self._browser_status
        running = status is not None and status.running
        managed = status is not None and status.managed
        compatible = status is not None and status.compatible
        operation_busy = self._browser_busy or self._game_sync_busy
        if operation_busy:
            focused = QApplication.focusWidget()
            if focused in {
                self.refresh_button,
                self.browser_action_button,
                self.restart_button,
                self.game_sync_button,
                self.cache_clear_button,
            }:
                focused.clearFocus()
        self.browser_action_button.setText(
            "Stop browser" if running and managed else "Launch browser"
        )
        self.browser_action_button.setAccessibleName(
            "Stop managed browser" if running and managed else "Launch managed browser"
        )
        danger = running and managed
        set_styled_property(self.browser_action_button, "danger", danger)
        self.refresh_button.setEnabled(not operation_busy)
        self.browser_action_button.setEnabled(
            not operation_busy and not self._runtime_active and (not running or managed)
        )
        self.restart_button.setEnabled(
            not operation_busy and not self._runtime_active and running and managed and compatible
        )
        self.game_sync_button.setEnabled(not operation_busy and not self._runtime_active)
        self.cache_clear_button.setEnabled(not operation_busy and not self._runtime_active)

    def _request(self, field: str, value: object) -> None:
        self.config_edited.emit(ConfigEdit({field: value}, field, "browser"))

    def _add_text(self, form: QFormLayout, label: str, field: str) -> None:
        control = QLineEdit(str(getattr(self._config, field)))
        control.setAccessibleName(label)
        control.editingFinished.connect(
            lambda widget=control, name=field: self._request(name, widget.text().strip())
        )

        def set_value(value: object) -> None:
            control.setText(str(value) if value is not None else "")

        self._register_control(field, control, set_value)
        self._add_form_row(form, label, control)

    def _add_path(
        self, form: QFormLayout, label: str, field: str, *, optional: bool = True
    ) -> None:
        value = getattr(self._config, field)
        control = QLineEdit(str(value) if value is not None else "")
        control.setAccessibleName(label)
        browse = secondary_button("Browse")
        browse.setAccessibleName(f"Choose {label.lower()}")
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(control, 1)
        layout.addWidget(browse)

        def update() -> None:
            text = control.text().strip()
            self._request(field, Path(text) if text else (None if optional else value))

        def choose() -> None:
            current = control.text().strip()
            if field == "browser_executable":
                selected, _ = QFileDialog.getOpenFileName(self, f"Choose {label}", current)
            else:
                selected = QFileDialog.getExistingDirectory(self, f"Choose {label}", current)
            if selected:
                control.setText(selected)
                update()

        control.editingFinished.connect(update)
        browse.clicked.connect(choose)

        def set_value(current: object) -> None:
            control.setText(str(current) if current is not None else "")

        self._register_control(field, control, set_value)
        self._add_form_row(form, label, row)

    def apply_config(self, config: AppConfig) -> None:
        self._apply_registered_controls(config)
        for control in self.controls.values():
            set_validation_state(control)
        freshness = inspect_catalog_freshness(config.catalog_dir / "catalog.json")
        self.set_catalog_freshness(freshness)
        self.configuration_header.mark_saved()

    def mark_saving(self, field: str | None = None) -> None:
        self.configuration_header.mark_saving()
        if field is not None and field in self.controls:
            set_validation_state(self.controls[field])

    def show_validation_error(self, field: str | None, message: str, config: AppConfig) -> None:
        self.configuration_header.mark_error(f"Invalid: {message}")
        if field is None or field not in self.controls:
            return
        self.apply_config(config)
        self.configuration_header.mark_error(f"Invalid: {message}")
        control = self.controls[field]
        set_validation_state(control, message)
        control.setFocus()

    def set_busy(self, busy: bool) -> None:
        self._browser_busy = busy
        self._sync_action_states()

    def set_runtime_active(self, active: bool) -> None:
        self._runtime_active = active
        self._sync_action_states()
