from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyleOptionComboBox,
    QStyleOptionSlider,
    QStyleOptionSpinBox,
)

from farm_merge_valet.gui.input_controls import (
    FocusAwareComboBox,
    FocusAwareDoubleSpinBox,
    FocusAwareSlider,
    FocusAwareSpinBox,
    SettingsToggle,
)
from farm_merge_valet.gui.policy_view import PolicyCheckBox
from farm_merge_valet.gui.theme import apply_theme


def _scroll_up(widget) -> None:
    position = QPointF(widget.rect().center())
    event = QWheelEvent(
        position,
        position,
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(widget, event)


def test_wheel_does_not_change_unfocused_inputs() -> None:
    app = QApplication.instance() or QApplication([])
    combo = FocusAwareComboBox()
    combo.addItems(("first", "second", "third"))
    combo.setCurrentIndex(1)
    integer = FocusAwareSpinBox()
    integer.setValue(10)
    decimal = FocusAwareDoubleSpinBox()
    decimal.setValue(2.5)
    slider = FocusAwareSlider(Qt.Orientation.Horizontal)
    slider.setValue(50)
    assert slider.minimumHeight() == 24

    for control, value in (
        (combo, combo.currentIndex()),
        (integer, integer.value()),
        (decimal, decimal.value()),
        (slider, slider.value()),
    ):
        assert control.focusPolicy() == Qt.FocusPolicy.StrongFocus
        control.clearFocus()
        _scroll_up(control)
        current = (
            control.currentIndex() if isinstance(control, FocusAwareComboBox) else control.value()
        )
        assert current == value

    app.processEvents()


def test_spin_buttons_have_stable_hit_targets_and_steps() -> None:
    app = QApplication.instance() or QApplication([])
    apply_theme(app, "light")
    control = FocusAwareDoubleSpinBox()
    control.setRange(0, 10)
    control.setDecimals(2)
    control.setSingleStep(0.05)
    control.setValue(1.5)
    control.resize(220, 34)
    control.show()
    app.processEvents()
    option = QStyleOptionSpinBox()
    control.initStyleOption(option)
    up = control.style().subControlRect(
        QStyle.ComplexControl.CC_SpinBox,
        option,
        QStyle.SubControl.SC_SpinBoxUp,
        control,
    )
    down = control.style().subControlRect(
        QStyle.ComplexControl.CC_SpinBox,
        option,
        QStyle.SubControl.SC_SpinBoxDown,
        control,
    )

    assert up.width() >= 24
    assert down.width() >= 24
    QTest.mouseClick(control, Qt.MouseButton.LeftButton, pos=up.center())
    assert control.value() == 1.55
    QTest.mouseClick(control, Qt.MouseButton.LeftButton, pos=down.center())
    assert control.value() == 1.5

    control.close()


def test_combo_choices_keep_display_labels_separate_from_values() -> None:
    app = QApplication.instance() or QApplication([])
    apply_theme(app, "light")
    control = FocusAwareComboBox()
    control.set_choices((("System default", "system"), ("Dark", "dark")))
    control.set_current_value("dark")
    control.resize(220, 34)
    control.show()
    app.processEvents()
    option = QStyleOptionComboBox()
    control.initStyleOption(option)
    arrow = control.style().subControlRect(
        QStyle.ComplexControl.CC_ComboBox,
        option,
        QStyle.SubControl.SC_ComboBoxArrow,
        control,
    )

    assert control.currentText() == "Dark"
    assert control.current_value() == "dark"
    assert arrow.width() >= 32

    control.close()


def test_settings_toggle_uses_switch_sizing_and_checkbox_behavior() -> None:
    app = QApplication.instance() or QApplication([])
    control = SettingsToggle()
    control.show()
    app.processEvents()

    assert control.sizeHint() == QSize(42, 24)
    assert control.minimumSize() == QSize(42, 24)
    assert control.maximumSize() == QSize(42, 24)
    assert not control.isChecked()
    QTest.mouseClick(control, Qt.MouseButton.LeftButton, pos=control.rect().center())
    assert control.isChecked()

    control.close()


def test_checked_policy_toggle_has_a_distinct_keyboard_focus_ring() -> None:
    app = QApplication.instance() or QApplication([])
    apply_theme(app, "light")
    control = PolicyCheckBox("Enabled")
    control.setChecked(True)
    control.resize(120, 30)
    control.show()
    app.processEvents()
    control.clearFocus()
    app.processEvents()
    unfocused = control.grab().toImage()

    control.setFocus(Qt.FocusReason.TabFocusReason)
    app.processEvents()
    focused = control.grab().toImage()

    assert control.hasFocus()
    assert focused != unfocused
    control.close()


@pytest.mark.parametrize("theme", ("light", "dark"))
def test_slider_handle_is_circular(theme: str) -> None:
    app = QApplication.instance() or QApplication([])
    apply_theme(app, theme)
    control = FocusAwareSlider(Qt.Orientation.Horizontal)
    control.setRange(0, 100)
    control.setValue(50)
    control.resize(400, 24)
    control.show()
    app.processEvents()
    option = QStyleOptionSlider()
    control.initStyleOption(option)
    handle = control.style().subControlRect(
        QStyle.ComplexControl.CC_Slider,
        option,
        QStyle.SubControl.SC_SliderHandle,
        control,
    )

    assert handle.size() == QSize(18, 18)

    control.close()
