"""Reusable keyboard shortcut recorder for global application hotkeys."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QKeyCombination, Qt, Signal
from PySide6.QtGui import QHideEvent, QKeyEvent, QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

from farm_merge_valet.hotkeys import display_hotkey, normalize_hotkey

_MODIFIER_KEYS = {
    Qt.Key.Key_Control,
    Qt.Key.Key_Shift,
    Qt.Key.Key_Alt,
    Qt.Key.Key_Meta,
    Qt.Key.Key_AltGr,
}


class HotkeyEdit(QWidget):
    value_changed = Signal(object)
    recording_changed = Signal(bool)

    def __init__(self, value: str | None, accessible_name: str) -> None:
        super().__init__()
        self._value = value
        self._pending_value: str | None = None
        self._recording = False
        self.display = QLineEdit()
        self.display.setReadOnly(True)
        self.display.setAccessibleName(accessible_name)
        self.record_button = QPushButton("Record")
        self.record_button.setProperty("secondary", True)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setProperty("secondary", True)
        self.record_button.clicked.connect(self._toggle_recording)
        self.clear_button.clicked.connect(self.clear)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.display, 1)
        layout.addWidget(self.record_button)
        layout.addWidget(self.clear_button)
        self.set_value(value)
        self.setFocusProxy(self.record_button)

    @property
    def value(self) -> str | None:
        return self._value

    @property
    def recording(self) -> bool:
        return self._recording

    def set_value(self, value: str | None) -> None:
        self._value = value
        self.display.setText(display_hotkey(value))
        self._set_error("")

    def show_error(self, message: str) -> None:
        self._set_error(message)

    def clear(self) -> None:
        self._finish_recording()
        if self._value is not None:
            self.set_value(None)
            self.value_changed.emit(None)

    def _toggle_recording(self) -> None:
        if self._recording:
            self._finish_recording()
            return
        self._recording = True
        self._pending_value = None
        self.record_button.setText("Cancel")
        self.display.setText("Press shortcut…")
        self._set_error("")
        self.recording_changed.emit(True)
        self.grabKeyboard()
        self.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _finish_recording(self) -> None:
        if not self._recording:
            return
        self._recording = False
        self._pending_value = None
        self.releaseKeyboard()
        self.record_button.setText("Record")
        self.display.setText(display_hotkey(self._value))
        self.recording_changed.emit(False)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._recording:
            super().keyPressEvent(event)
            return
        event.accept()
        if event.isAutoRepeat() or event.key() in _MODIFIER_KEYS:
            return
        if event.key() == Qt.Key.Key_Escape:
            self._finish_recording()
            return
        if event.key() in {Qt.Key.Key_Backspace, Qt.Key.Key_Delete} and not event.modifiers():
            self.clear()
            return
        sequence = QKeySequence(QKeyCombination(event.modifiers(), Qt.Key(event.key()))).toString(
            QKeySequence.SequenceFormat.PortableText
        )
        try:
            value = normalize_hotkey(sequence)
        except ValueError as exc:
            self._set_error(str(exc))
            self.display.setText("Try another shortcut…")
            return
        self._pending_value = value
        self.display.setText(display_hotkey(value))

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if not self._recording or self._pending_value is None:
            super().keyReleaseEvent(event)
            return
        event.accept()
        if event.isAutoRepeat() or event.key() in _MODIFIER_KEYS:
            return
        value = self._pending_value
        self._value = value
        self._finish_recording()
        self.value_changed.emit(value)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.EnabledChange:
            self.record_button.setEnabled(self.isEnabled())
            self.clear_button.setEnabled(self.isEnabled())
        return super().event(event)

    def hideEvent(self, event: QHideEvent) -> None:
        self._finish_recording()
        super().hideEvent(event)

    def _set_error(self, message: str) -> None:
        invalid = bool(message)
        self.display.setProperty("invalid", invalid)
        self.display.setToolTip(message)
        self.display.setAccessibleDescription(message)
        self.display.style().unpolish(self.display)
        self.display.style().polish(self.display)
