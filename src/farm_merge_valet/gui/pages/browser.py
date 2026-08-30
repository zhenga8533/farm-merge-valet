"""Managed-browser configuration page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from farm_merge_valet.browser import BrowserStatus
from farm_merge_valet.config import AppConfig
from farm_merge_valet.gui.components.configuration_header import ConfigurationHeader
from farm_merge_valet.gui.components.input_controls import (
    FocusAwareComboBox,
)
from farm_merge_valet.gui.components.widgets import secondary_button, set_validation_state
from farm_merge_valet.gui.pages.base import (
    ConfigEdit,
    ConfigFormPage,
    disclosure_section,
    settings_section,
)


class BrowserPage(ConfigFormPage):
    config_edited = Signal(object)
    refresh_requested = Signal()
    launch_requested = Signal()
    stop_requested = Signal()
    restart_requested = Signal()
    game_sync_requested = Signal()
    reset_requested = Signal()

    def __init__(self, config: AppConfig) -> None:
        super().__init__(
            "Managed browser",
            "Manage the dedicated browser, game connection, and local asset cache.",
            config,
        )
        self.configuration_header = ConfigurationHeader("Reset browser configuration")
        self.configuration_header.reset_requested.connect(self.reset_requested)
        self.saved_label = self.configuration_header.status_label
        self.page_layout.addWidget(self.configuration_header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        sections = QVBoxLayout(content)
        sections.setContentsMargins(4, 4, 4, 4)
        sections.setSpacing(14)

        managed, managed_form = settings_section("Managed browser")
        self.status_label = QLabel("Not checked")
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
        self.controls["browser"] = self.browser_choice
        self._add_form_row(managed_form, "Status", self.status_label)
        self._add_form_row(managed_form, "Preferred browser", self.browser_choice)
        self._add_toggle(managed_form, "Launch automatically when needed", "browser_auto_launch")
        buttons = QHBoxLayout()
        self.refresh_button = secondary_button("Refresh status")
        self.browser_action_button = QPushButton("Launch managed browser")
        self.restart_button = QPushButton("Restart managed browser")
        self.restart_button.setProperty("secondary", True)
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.browser_action_button.clicked.connect(self._request_browser_action)
        self.restart_button.clicked.connect(self.restart_requested)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.browser_action_button)
        buttons.addWidget(self.restart_button)
        buttons.addStretch()
        self._add_form_row(managed_form, "Actions", buttons)
        browser_advanced, form = disclosure_section("Advanced connection")
        self._add_path(form, "Browser executable", "browser_executable")
        self._add_path(form, "Browser profile directory", "browser_profile_dir")
        self._add_text(form, "Game URL", "game_url")
        self._add_text(form, "Page target", "window_title")
        self._add_int(form, "CDP port", "cdp_port", 1, 65535)
        self.browser_advanced_section = browser_advanced
        managed_form.addRow(browser_advanced)
        sections.addWidget(managed)

        assets, assets_form = settings_section("Game data and assets")
        self.game_sync_status_label = QLabel(self._game_sync_status(config))
        self.game_sync_status_label.setWordWrap(True)
        self.game_sync_status_label.setAccessibleName("Game data and asset status")
        self._add_form_row(assets_form, "Status", self.game_sync_status_label)
        self.game_sync_button = QPushButton("Synchronize game data and assets")
        self.game_sync_button.clicked.connect(self.game_sync_requested)
        game_sync_actions = QHBoxLayout()
        game_sync_actions.addWidget(self.game_sync_button)
        game_sync_actions.addStretch()
        self._add_form_row(assets_form, "Actions", game_sync_actions)
        assets_advanced, form = disclosure_section("Advanced storage")
        self._add_path(form, "Catalog directory", "catalog_dir", optional=False)
        self._add_path(form, "Atlas cache directory", "atlas_cache_dir", optional=False)
        self.assets_advanced_section = assets_advanced
        assets_form.addRow(assets_advanced)
        sections.addWidget(assets)
        sections.addStretch()
        scroll.setWidget(content)
        self.page_layout.addWidget(scroll, 1)
        self._browser_busy = False
        self._game_sync_busy = False
        self._runtime_active = False
        self._browser_status: BrowserStatus | None = None
        self._sync_action_states()

    @staticmethod
    def _game_sync_status(config: AppConfig) -> str:
        catalog = config.catalog_dir / "catalog.json"
        if not catalog.is_file():
            return "No compiled catalog found"
        return "Compiled catalog available"

    def _request_browser_action(self) -> None:
        if self._browser_status is not None and self._browser_status.running:
            self.stop_requested.emit()
        else:
            self.launch_requested.emit()

    def set_browser_status(self, status: BrowserStatus) -> None:
        self._browser_status = status
        self._sync_action_states()

    @property
    def browser_status_known(self) -> bool:
        return self._browser_status is not None

    def set_game_sync_status(self, status: str) -> None:
        self.game_sync_status_label.setText(status)

    def set_game_sync_busy(self, busy: bool) -> None:
        self._game_sync_busy = busy
        self._sync_action_states()

    def _sync_action_states(self) -> None:
        status = self._browser_status
        running = status is not None and status.running
        managed = status is not None and status.managed
        compatible = status is not None and status.compatible
        operation_busy = self._browser_busy or self._game_sync_busy
        self.browser_action_button.setText(
            "Stop managed browser" if running and managed else "Launch managed browser"
        )
        danger = running and managed
        if self.browser_action_button.property("danger") != danger:
            self.browser_action_button.setProperty("danger", danger)
            self.browser_action_button.style().unpolish(self.browser_action_button)
            self.browser_action_button.style().polish(self.browser_action_button)
        self.refresh_button.setEnabled(not operation_busy)
        self.browser_action_button.setEnabled(
            not operation_busy and not self._runtime_active and (not running or managed)
        )
        self.restart_button.setEnabled(
            not operation_busy and not self._runtime_active and running and managed and compatible
        )
        self.game_sync_button.setEnabled(not operation_busy and not self._runtime_active)

    def _request(self, field: str, value: object) -> None:
        self.config_edited.emit(ConfigEdit({field: value}, field, "browser"))

    def _add_text(self, form: QFormLayout, label: str, field: str) -> None:
        control = QLineEdit(str(getattr(self._config, field)))
        control.setAccessibleName(label)
        control.editingFinished.connect(
            lambda widget=control, name=field: self._request(name, widget.text().strip())
        )
        self.controls[field] = control
        self._add_form_row(form, label, control)

    def _add_path(
        self, form: QFormLayout, label: str, field: str, *, optional: bool = True
    ) -> None:
        value = getattr(self._config, field)
        control = QLineEdit(str(value) if value is not None else "")
        control.setAccessibleName(label)
        browse = secondary_button("Browse…")
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
        self.controls[field] = control
        self._add_form_row(form, label, row)

    def apply_config(self, config: AppConfig) -> None:
        self._config = config
        for field, control in self.controls.items():
            control.blockSignals(True)
            value = getattr(config, field)
            if isinstance(control, FocusAwareComboBox):
                control.set_current_value(value)
            elif isinstance(control, QCheckBox):
                control.setChecked(bool(value))
            elif isinstance(control, QSpinBox):
                control.setValue(int(value))
            elif isinstance(control, QLineEdit):
                control.setText(str(value) if value is not None else "")
            control.blockSignals(False)
            set_validation_state(control)
        self.game_sync_status_label.setText(self._game_sync_status(config))
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
