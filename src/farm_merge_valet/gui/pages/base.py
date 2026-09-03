"""Shared page structure and configuration form behavior."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from farm_merge_valet.config import AppConfig
from farm_merge_valet.gui.components.input_controls import FocusAwareSpinBox, SettingsToggle
from farm_merge_valet.gui.components.widgets import DisclosureSection


@dataclass(frozen=True)
class ConfigEdit:
    changes: dict[str, object]
    field: str | None = None
    source: str = "settings"


def settings_section(title: str) -> tuple[QGroupBox, QFormLayout]:
    section = QGroupBox(title)
    section.setMaximumWidth(900)
    form = QFormLayout(section)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setContentsMargins(14, 18, 14, 14)
    form.setSpacing(10)
    return section, form


def disclosure_section(
    title: str, *, expanded: bool = False
) -> tuple[DisclosureSection, QFormLayout]:
    section = DisclosureSection(title, expanded=expanded)
    section.setMaximumWidth(900)
    form = QFormLayout(section.content)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setContentsMargins(14, 14, 14, 14)
    form.setSpacing(10)
    return section, form


def scrollable_sections(page_layout: QVBoxLayout) -> QVBoxLayout:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    content = QWidget()
    sections = QVBoxLayout(content)
    sections.setContentsMargins(4, 4, 4, 4)
    sections.setSpacing(14)
    scroll.setWidget(content)
    page_layout.addWidget(scroll, 1)
    return sections


class AppPage(QWidget):
    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("appPage")
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.AlternateBase)
        self.page_layout = QVBoxLayout(self)
        self.page_layout.setContentsMargins(20, 18, 20, 20)
        self.page_layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        self.page_layout.addWidget(heading)
        if subtitle:
            description = QLabel(subtitle)
            description.setObjectName("pageSubtitle")
            description.setWordWrap(True)
            self.page_layout.addWidget(description)


class ConfigFormPage(AppPage):
    def __init__(
        self,
        title: str,
        subtitle: str,
        config: AppConfig,
        *,
        form_label_width: int = 210,
    ) -> None:
        super().__init__(title, subtitle)
        self._config = config
        self._form_label_width = form_label_width
        self.controls: dict[str, QWidget] = {}
        self._control_setters: dict[str, Callable[[object], None]] = {}

    def _request(self, field: str, value: object) -> None:
        raise NotImplementedError

    def _add_form_row(
        self,
        form: QFormLayout,
        text: str,
        field: QWidget | QLayout,
    ) -> None:
        label = QLabel(text)
        label.setFixedWidth(self._form_label_width)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if isinstance(field, QWidget):
            label.setBuddy(field)
        form.addRow(label, field)

    def _add_toggle(self, form: QFormLayout, label: str, field: str) -> None:
        control = SettingsToggle()
        control.setChecked(bool(getattr(self._config, field)))
        control.setAccessibleName(label)
        control.toggled.connect(lambda value, name=field: self._request(name, value))

        def set_value(value: object) -> None:
            control.setChecked(bool(value))

        self._register_control(field, control, set_value)
        self._add_form_row(form, label, control)

    def _add_int(
        self,
        form: QFormLayout,
        label: str,
        field: str,
        minimum: int,
        maximum: int,
    ) -> None:
        control = FocusAwareSpinBox()
        control.setRange(minimum, maximum)
        control.setValue(getattr(self._config, field))
        control.setAccessibleName(label)
        control.editingFinished.connect(
            lambda widget=control, name=field: self._request(name, widget.value())
        )

        def set_value(value: object) -> None:
            control.setValue(int(str(value)))

        self._register_control(field, control, set_value)
        self._add_form_row(form, label, control)

    def _register_control(
        self,
        field: str,
        control: QWidget,
        setter: Callable[[object], None],
    ) -> None:
        self.controls[field] = control
        self._control_setters[field] = setter

    def _apply_registered_controls(self, config: AppConfig) -> None:
        self._config = config
        for field in self.controls:
            self._apply_registered_control(field, getattr(config, field))

    def _apply_registered_control(self, field: str, value: object) -> None:
        control = self.controls[field]
        control.blockSignals(True)
        self._control_setters[field](value)
        control.blockSignals(False)
