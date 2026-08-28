"""Configuration, browser, appearance, and diagnostic GUI pages."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import SecretStr
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from farm_merge_valet import __version__
from farm_merge_valet.browser import BrowserStatus
from farm_merge_valet.config import AppConfig
from farm_merge_valet.gui.configuration_header import ConfigurationHeader
from farm_merge_valet.gui.hotkey_edit import HotkeyEdit
from farm_merge_valet.gui.input_controls import (
    FocusAwareComboBox,
    FocusAwareDoubleSpinBox,
    FocusAwareSlider,
)
from farm_merge_valet.gui.page_base import (
    AppPage,
    ConfigEdit,
    ConfigFormPage,
    disclosure_section,
    settings_section,
)
from farm_merge_valet.gui.widgets import secondary_button, set_validation_state


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

        managed, form = settings_section("Managed browser")
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
        self._add_form_row(form, "Status", self.status_label)
        self._add_form_row(form, "Preferred browser", self.browser_choice)
        self._add_toggle(form, "Launch automatically when needed", "browser_auto_launch")
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
        self._add_form_row(form, "Actions", buttons)
        sections.addWidget(managed)

        assets, form = settings_section("Game data and assets")
        self.game_sync_status_label = QLabel(self._game_sync_status(config))
        self.game_sync_status_label.setWordWrap(True)
        self.game_sync_status_label.setAccessibleName("Game data and asset status")
        self._add_form_row(form, "Status", self.game_sync_status_label)
        self.game_sync_button = QPushButton("Synchronize game data and assets")
        self.game_sync_button.clicked.connect(self.game_sync_requested)
        self._add_form_row(form, "Actions", self.game_sync_button)
        sections.addWidget(assets)

        advanced, form = disclosure_section("Advanced browser, connection, and storage")
        self._add_path(form, "Browser executable", "browser_executable")
        self._add_path(form, "Browser profile directory", "browser_profile_dir")
        self._add_text(form, "Game URL", "game_url")
        self._add_text(form, "Page target", "window_title")
        self._add_int(form, "CDP port", "cdp_port", 1, 65535)
        self._add_path(form, "Catalog directory", "catalog_dir", optional=False)
        self._add_path(form, "Atlas cache directory", "atlas_cache_dir", optional=False)
        self.advanced_section = advanced
        sections.addWidget(advanced)
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


class SettingsPage(ConfigFormPage):
    config_edited = Signal(object)
    reset_requested = Signal()
    reset_all_requested = Signal()
    hotkey_recording_changed = Signal(bool)
    _COUPLED_FIELDS = (
        ("item_action_delay_min", "item_action_delay_max"),
        ("crate_delay_min", "crate_delay_max"),
    )

    def __init__(self, config: AppConfig) -> None:
        super().__init__("Settings", "Changes validate and autosave automatically.", config)
        self.opacity_labels: dict[str, QLabel] = {}
        self.configuration_header = ConfigurationHeader("Reset settings")
        self.configuration_header.reset_requested.connect(self.reset_requested)
        self.saved_label = self.configuration_header.status_label
        reset_all = self.configuration_header.add_action("Reset all")
        reset_all.clicked.connect(lambda _checked=False: self.reset_all_requested.emit())
        self.reset_all_button = reset_all
        self.page_layout.addWidget(self.configuration_header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        sections = QVBoxLayout(content)
        sections.setContentsMargins(4, 4, 4, 4)
        sections.setSpacing(14)

        automation, form = settings_section("Automation")
        self._add_int(form, "Reserved empty cells", "merge_empty_cell_reserve", 0, 50)
        self._add_int(
            form,
            "Producer interaction open cells",
            "producer_interact_min_empty_cells",
            1,
            50,
        )
        sections.addWidget(automation)

        automation_advanced, form = disclosure_section("Advanced automation timing")
        self._add_float(form, "Idle polling (seconds)", "idle_wait_seconds", 0, 3600, 0.1)
        self._add_float(form, "Loop interval (seconds)", "loop_interval", 0.01, 60, 0.1)
        self._add_float(form, "Item delay minimum", "item_action_delay_min", 0, 60, 0.1)
        self._add_float(form, "Item delay maximum", "item_action_delay_max", 0, 60, 0.1)
        self._add_float(form, "Crate delay minimum", "crate_delay_min", 0, 5, 0.05)
        self._add_float(form, "Crate delay maximum", "crate_delay_max", 0, 5, 0.05)
        self.automation_advanced_section = automation_advanced
        sections.addWidget(automation_advanced)

        controls, form = settings_section("Controls and startup")
        self._add_hotkey(form, "Start / stop", "start_stop_hotkey")
        self._add_hotkey(form, "Pause / resume", "pause_hotkey")
        self._add_hotkey(form, "Quit application", "quit_hotkey")
        for field, label in (
            ("start_paused", "Start automation paused"),
            ("start_minimized", "Start minimized to tray"),
            ("bot_autostart", "Start bot with application"),
            ("close_to_tray", "Close window to tray"),
        ):
            self._add_toggle(form, label, field)
        sections.addWidget(controls)

        notifications, form = settings_section("Notifications")
        webhook = QLineEdit()
        webhook.setEchoMode(QLineEdit.EchoMode.Password)
        webhook.setPlaceholderText("Optional Discord webhook URL")
        webhook.setAccessibleName("Discord webhook")
        if config.discord_webhook_url:
            webhook.setText(config.discord_webhook_url.get_secret_value())
        webhook.editingFinished.connect(
            lambda: self._request("discord_webhook_url", webhook.text().strip() or None)
        )
        self.controls["discord_webhook_url"] = webhook
        self._add_form_row(form, "Discord webhook", webhook)
        sections.addWidget(notifications)

        notifications_advanced, form = disclosure_section("Advanced notification timing")
        self._add_float(form, "Webhook status interval", "webhook_status_interval", 0, 3600, 1)
        self._add_float(form, "Webhook summary interval", "webhook_summary_interval", 60, 86400, 1)
        self.notifications_advanced_section = notifications_advanced
        sections.addWidget(notifications_advanced)

        appearance, form = settings_section("Appearance")
        theme = FocusAwareComboBox()
        theme.set_choices((("System default", "system"), ("Dark", "dark"), ("Light", "light")))
        theme.set_current_value(config.theme)
        theme.setAccessibleName("Theme")
        theme.currentIndexChanged.connect(
            lambda _index: self._request("theme", str(theme.current_value()))
        )
        self.controls["theme"] = theme
        self._add_form_row(form, "Theme", theme)
        sections.addWidget(appearance)

        appearance_advanced, form = disclosure_section("Advanced window appearance")
        for field, label in (
            ("main_always_on_top", "Dashboard always on top"),
            ("overlay_always_on_top", "Overlay always on top"),
            ("overlay_click_through", "Overlay click-through while inactive"),
        ):
            self._add_toggle(form, label, field)
        self._add_opacity(form, "Dashboard inactive opacity", "main_unfocused_opacity")
        self._add_opacity(form, "Dashboard focused opacity", "main_focused_opacity")
        self._add_opacity(form, "Overlay inactive opacity", "overlay_unfocused_opacity")
        self._add_opacity(form, "Overlay focused opacity", "overlay_focused_opacity")
        self.appearance_advanced_section = appearance_advanced
        sections.addWidget(appearance_advanced)

        application, form = settings_section("Application")
        version = QLabel(__version__)
        version.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        version.setAccessibleName("Application version")
        self._add_form_row(form, "Version", version)
        sections.addWidget(application)
        sections.addStretch()
        scroll.setWidget(content)
        self.page_layout.addWidget(scroll, 1)

    def _request(self, field: str, value: object) -> None:
        changes = {field: value}
        for coupled_fields in self._COUPLED_FIELDS:
            if field not in coupled_fields:
                continue
            changes = {}
            for name in coupled_fields:
                control = self.controls[name]
                if isinstance(control, QDoubleSpinBox):
                    changes[name] = float(control.value())
            break
        self.config_edited.emit(ConfigEdit(changes, field))

    def _add_float(
        self,
        form: QFormLayout,
        label: str,
        field: str,
        minimum: float,
        maximum: float,
        step: float,
    ) -> None:
        control = FocusAwareDoubleSpinBox()
        control.setRange(minimum, maximum)
        control.setDecimals(2)
        control.setSingleStep(step)
        control.setKeyboardTracking(False)
        control.setValue(getattr(self._config, field))
        control.setAccessibleName(label)
        control.editingFinished.connect(
            lambda widget=control, name=field: self._request(name, widget.value())
        )
        self.controls[field] = control
        self._add_form_row(form, label, control)

    def _add_hotkey(self, form: QFormLayout, label: str, field: str) -> None:
        value = getattr(self._config, field)
        control = HotkeyEdit(value if isinstance(value, str) else None, f"{label} global hotkey")
        control.value_changed.connect(lambda hotkey, name=field: self._request(name, hotkey))
        control.recording_changed.connect(self.hotkey_recording_changed)
        self.controls[field] = control
        self._add_form_row(form, label, control)

    def _add_opacity(self, form: QFormLayout, label: str, field: str) -> None:
        control = FocusAwareSlider(Qt.Orientation.Horizontal)
        control.setRange(25, 100)
        control.setValue(round(getattr(self._config, field) * 100))
        control.setAccessibleName(label)
        value_label = QLabel(f"{control.value()}%")
        value_label.setMinimumWidth(42)
        value_label.setAccessibleName(f"{label} value")
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(control, 1)
        layout.addWidget(value_label)

        def update(value: int) -> None:
            value_label.setText(f"{value}%")
            self._request(field, value / 100)

        control.valueChanged.connect(update)
        self.controls[field] = control
        self.opacity_labels[field] = value_label
        self._add_form_row(form, label, row)

    def show_validation_error(self, field: str | None, message: str, config: AppConfig) -> None:
        self.configuration_header.mark_error(f"Invalid: {message}")
        if field is None or field not in self.controls:
            return
        control = self.controls[field]
        if isinstance(control, HotkeyEdit):
            self._set_control_value(field, control, getattr(config, field))
            control.show_error(message)
            control.setFocus()
            return
        for name in self._related_validation_fields(field):
            set_validation_state(self.controls[name], message)
        control.setFocus()

    def mark_saving(self, field: str | None = None) -> None:
        self.configuration_header.mark_saving()
        if field is not None and field in self.controls:
            for name in self._related_validation_fields(field):
                control = self.controls[name]
                if isinstance(control, HotkeyEdit):
                    control.show_error("")
                else:
                    set_validation_state(control)

    def _related_validation_fields(self, field: str) -> tuple[str, ...]:
        return next(
            (coupled for coupled in self._COUPLED_FIELDS if field in coupled),
            (field,),
        )

    def apply_config(self, config: AppConfig) -> None:
        self._config = config
        for field, control in self.controls.items():
            self._set_control_value(field, control, getattr(config, field))
            if isinstance(control, HotkeyEdit):
                control.show_error("")
            else:
                set_validation_state(control)
        self.configuration_header.mark_saved()

    def _set_control_value(self, field: str, control: QWidget, value: object) -> None:
        control.blockSignals(True)
        if isinstance(control, HotkeyEdit):
            control.set_value(value if isinstance(value, str) else None)
        elif isinstance(control, QCheckBox):
            control.setChecked(bool(value))
        elif isinstance(control, QSpinBox):
            control.setValue(int(str(value)))
        elif isinstance(control, QDoubleSpinBox):
            control.setValue(float(str(value)))
        elif isinstance(control, QSlider):
            percent = round(float(str(value)) * 100)
            control.setValue(percent)
            self.opacity_labels[field].setText(f"{percent}%")
        elif isinstance(control, FocusAwareComboBox):
            control.set_current_value(value)
        elif isinstance(control, QLineEdit):
            if field == "discord_webhook_url" and isinstance(value, SecretStr):
                value = value.get_secret_value()
            control.setText(str(value) if value is not None else "")
        control.blockSignals(False)


class LogsPage(AppPage):
    log_level_changed = Signal(str)
    export_requested = Signal()

    def __init__(self, log_level: str) -> None:
        super().__init__("Logs", "Structured application events from the shared logging pipeline.")
        toolbar = QHBoxLayout()
        self.filter = FocusAwareComboBox()
        self.filter.set_choices(
            (("Debug", "DEBUG"), ("Info", "INFO"), ("Warning", "WARNING"), ("Error", "ERROR"))
        )
        self.filter.set_current_value(log_level)
        self.filter.setAccessibleName("Minimum log level")
        self.filter.currentIndexChanged.connect(
            lambda _index: self.log_level_changed.emit(str(self.filter.current_value()))
        )
        clear = secondary_button("Clear")
        clear.clicked.connect(self.clear)
        self.export_button = QPushButton("Export diagnostics…")
        self.export_button.clicked.connect(self.export_requested)
        toolbar.addWidget(QLabel("Minimum level"))
        toolbar.addWidget(self.filter)
        toolbar.addStretch()
        self.export_status = QLabel()
        self.export_status.setObjectName("saveStatus")
        toolbar.addWidget(self.export_status)
        toolbar.addWidget(clear)
        toolbar.addWidget(self.export_button)
        self.page_layout.addLayout(toolbar)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(2000)
        self.view.setFont(QFont("Cascadia Mono, Consolas, monospace", 10))
        self.view.setAccessibleName("Application logs")
        self.page_layout.addWidget(self.view, 1)

    def append(self, timestamp: str, level: str, message: str, levelno: int) -> None:
        minimum = getattr(logging, str(self.filter.current_value()), logging.INFO)
        if levelno >= minimum:
            self.view.appendPlainText(f"[{timestamp}] {level:<8} {message}")

    def clear(self) -> None:
        self.view.clear()

    def set_export_busy(self, busy: bool) -> None:
        self.export_button.setEnabled(not busy)
        self.export_status.setText("Exporting…" if busy else self.export_status.text())

    def set_export_result(self, output: str) -> None:
        self.export_status.setText("Exported")
        self.export_status.setToolTip(output)

    def set_export_error(self, message: str) -> None:
        self.export_status.setText("Export failed")
        self.export_status.setToolTip(message)
