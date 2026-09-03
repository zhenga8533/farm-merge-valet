"""State-aware action button with a visually distinct shortcut keycap."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

from farm_merge_valet.config.hotkeys import display_hotkey
from farm_merge_valet.gui.components.widgets import set_styled_property


class ActionButton(QPushButton):
    def __init__(self, action: str, *, secondary: bool = False) -> None:
        super().__init__()
        self.action_text = action
        self.hotkey: str | None = None
        self.setProperty("shortcutButton", True)
        self.setProperty("secondary", secondary)
        self.setProperty("danger", False)

        self.action_label = QLabel(action)
        self.action_label.setObjectName("actionButtonLabel")
        self.shortcut_label = QLabel()
        self.shortcut_label.setObjectName("shortcutKeycap")
        self.shortcut_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.shortcut_label.setContentsMargins(5, 1, 5, 1)
        self.shortcut_label.setMinimumHeight(20)
        for label in (self.action_label, self.shortcut_label):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 7, 14, 7)
        layout.setSpacing(10)
        layout.addWidget(self.action_label)
        layout.addWidget(self.shortcut_label)
        self.set_action(action)

    def set_action(self, action: str, hotkey: str | None = None, *, danger: bool = False) -> None:
        self.action_text = action
        self.hotkey = hotkey
        self.action_label.setText(action)
        self.shortcut_label.setText(display_hotkey(hotkey) if hotkey is not None else "")
        self.shortcut_label.setVisible(hotkey is not None)
        description = (
            f"{action}. Global hotkey: {display_hotkey(hotkey)}"
            if hotkey is not None
            else f"{action}. No global hotkey."
        )
        self.setAccessibleName(description)
        self.setToolTip(description)
        set_styled_property(self, "danger", danger)
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        width = 28 + self.action_label.sizeHint().width()
        if self.hotkey is not None:
            width += 10 + self.shortcut_label.sizeHint().width()
        height = 14 + max(
            self.action_label.sizeHint().height(),
            self.shortcut_label.sizeHint().height() if self.hotkey is not None else 0,
        )
        return QSize(max(72, width), max(34, height))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()
