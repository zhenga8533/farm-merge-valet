"""Application settings page."""

from __future__ import annotations

from pydantic import SecretStr
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from farm_merge_valet import __version__
from farm_merge_valet.config import AppConfig
from farm_merge_valet.gui.components.configuration_header import ConfigurationHeader
from farm_merge_valet.gui.components.hotkey_edit import HotkeyEdit
from farm_merge_valet.gui.components.input_controls import (
    FocusAwareComboBox,
    FocusAwareDoubleSpinBox,
    FocusAwareSlider,
)
from farm_merge_valet.gui.components.widgets import set_validation_state
from farm_merge_valet.gui.pages.base import (
    ConfigEdit,
    ConfigFormPage,
    disclosure_section,
    scrollable_sections,
    settings_section,
)


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

        sections = scrollable_sections(self.page_layout)

        automation, automation_form = settings_section("Automation")
        self._add_int(
            automation_form,
            "Merge-space reserve",
            "merge_empty_cell_reserve",
            0,
            50,
        )
        self._add_int(
            automation_form,
            "Fallback producer open cells",
            "producer_interact_min_empty_cells",
            1,
            50,
        )
        self._add_toggle(
            automation_form,
            "Automatically dismiss reward overlays",
            "auto_dismiss_overlays",
        )
        self._add_toggle(
            automation_form,
            "Automatically pop stored items",
            "auto_pop_storage_bubbles",
        )
        self._add_toggle(
            automation_form,
            "Automatically claim supply crates",
            "auto_claim_supply_crates",
        )
        self._add_toggle(
            automation_form,
            "Allow obstacle energy spending",
            "allow_obstacle_stage_starts",
        )
        self._add_toggle(
            automation_form,
            "Visit other farms automatically",
            "farm_visit_automation_enabled",
        )
        automation_advanced, form = disclosure_section("Advanced timing")
        self._add_float(form, "Idle polling (seconds)", "idle_wait_seconds", 0, 3600, 0.1)
        self._add_float(form, "Loop interval (seconds)", "loop_interval", 0.25, 60, 0.1)
        self._add_float(form, "Item delay minimum", "item_action_delay_min", 0, 60, 0.1)
        self._add_float(form, "Item delay maximum", "item_action_delay_max", 0, 60, 0.1)
        self._add_float(form, "Crate delay minimum", "crate_delay_min", 0, 5, 0.05)
        self._add_float(form, "Crate delay maximum", "crate_delay_max", 0, 5, 0.05)
        self.automation_advanced_section = automation_advanced
        automation_form.addRow(automation_advanced)
        sections.addWidget(automation)

        controls, form = settings_section("Controls and startup")
        self._add_hotkey(form, "Start / stop", "start_stop_hotkey")
        self._add_hotkey(form, "Pause / resume", "pause_hotkey")
        self._add_hotkey(form, "Quit application", "quit_hotkey")
        for field, label in (
            ("start_paused", "Start each bot run paused"),
            ("start_minimized", "Start minimized to tray"),
            ("bot_autostart", "Start bot with application"),
            ("close_to_tray", "Close window to tray"),
            ("close_managed_browser_on_exit", "Close managed browser when quitting"),
        ):
            self._add_toggle(form, label, field)
        sections.addWidget(controls)

        notifications, notifications_form = settings_section("Notifications")
        webhook = QLineEdit()
        webhook.setEchoMode(QLineEdit.EchoMode.Password)
        webhook.setPlaceholderText("Optional Discord webhook URL")
        webhook.setAccessibleName("Discord webhook")
        if config.discord_webhook_url:
            webhook.setText(config.discord_webhook_url.get_secret_value())
        webhook.editingFinished.connect(
            lambda: self._request("discord_webhook_url", webhook.text().strip() or None)
        )
        self._register_control(
            "discord_webhook_url",
            webhook,
            lambda value: webhook.setText(
                value.get_secret_value()
                if isinstance(value, SecretStr)
                else str(value) if value is not None else ""
            ),
        )
        self._add_form_row(notifications_form, "Discord webhook", webhook)

        notifications_advanced, form = disclosure_section("Advanced timing")
        self._add_float(form, "Webhook status interval", "webhook_status_interval", 0, 3600, 1)
        self._add_float(form, "Webhook summary interval", "webhook_summary_interval", 60, 86400, 1)
        self.notifications_advanced_section = notifications_advanced
        notifications_form.addRow(notifications_advanced)
        sections.addWidget(notifications)

        appearance, appearance_form = settings_section("Appearance")
        theme = FocusAwareComboBox()
        theme.set_choices((("System default", "system"), ("Dark", "dark"), ("Light", "light")))
        theme.set_current_value(config.theme)
        theme.setAccessibleName("Theme")
        theme.currentIndexChanged.connect(
            lambda _index: self._request("theme", str(theme.current_value()))
        )
        self._register_control("theme", theme, theme.set_current_value)
        self._add_form_row(appearance_form, "Theme", theme)

        appearance_advanced, form = disclosure_section("Advanced window behavior")
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
        appearance_form.addRow(appearance_advanced)
        sections.addWidget(appearance)

        application, form = settings_section("Application")
        version = QLabel(__version__)
        version.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        version.setAccessibleName("Application version")
        self._add_form_row(form, "Version", version)
        sections.addWidget(application)
        sections.addStretch()

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

        def set_value(value: object) -> None:
            control.setValue(float(str(value)))

        self._register_control(field, control, set_value)
        self._add_form_row(form, label, control)

    def _add_hotkey(self, form: QFormLayout, label: str, field: str) -> None:
        value = getattr(self._config, field)
        control = HotkeyEdit(value if isinstance(value, str) else None, f"{label} global hotkey")
        control.value_changed.connect(lambda hotkey, name=field: self._request(name, hotkey))
        control.recording_changed.connect(self.hotkey_recording_changed)

        def set_value(value: object) -> None:
            control.set_value(value if isinstance(value, str) else None)

        self._register_control(field, control, set_value)
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
        def set_opacity(value: object) -> None:
            percent = round(float(str(value)) * 100)
            control.setValue(percent)
            value_label.setText(f"{percent}%")

        self._register_control(field, control, set_opacity)
        self.opacity_labels[field] = value_label
        self._add_form_row(form, label, row)

    def show_validation_error(self, field: str | None, message: str, config: AppConfig) -> None:
        self.configuration_header.mark_error(f"Invalid: {message}")
        if field is None or field not in self.controls:
            return
        control = self.controls[field]
        if isinstance(control, HotkeyEdit):
            self._apply_registered_control(field, getattr(config, field))
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
        self._apply_registered_controls(config)
        for control in self.controls.values():
            if isinstance(control, HotkeyEdit):
                control.show_error("")
            else:
                set_validation_state(control)
        self.configuration_header.mark_saved()
