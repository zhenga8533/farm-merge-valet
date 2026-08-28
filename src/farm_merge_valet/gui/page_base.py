"""Shared page structure and configuration form behavior."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLayout,
    QVBoxLayout,
    QWidget,
)

from farm_merge_valet.config import AppConfig
from farm_merge_valet.gui.input_controls import FocusAwareSpinBox, SettingsToggle
from farm_merge_valet.gui.widgets import DisclosureSection


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
        self.controls[field] = control
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
        self.controls[field] = control
        self._add_form_row(form, label, control)
