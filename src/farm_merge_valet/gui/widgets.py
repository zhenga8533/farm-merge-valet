"""Small shared widget helpers used across GUI pages and editors."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QWidget


def secondary_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("secondary", True)
    return button


def set_validation_state(widget: QWidget, message: str = "") -> None:
    widget.setProperty("invalid", bool(message))
    widget.setToolTip(message)
    widget.setAccessibleDescription(message)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
