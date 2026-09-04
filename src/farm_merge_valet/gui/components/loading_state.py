"""Reusable indeterminate state for deferred local GUI work."""

from PySide6.QtCore import Qt

from farm_merge_valet.gui.components.progress import IndeterminateProgressBar
from farm_merge_valet.gui.components.state_panel import StatePanel


class LoadingState(StatePanel):
    def __init__(self, title: str, description: str) -> None:
        super().__init__(
            title,
            description,
            object_name="loadingState",
            maximum_width=520,
        )
        self.progress = IndeterminateProgressBar(title)
        self.content_layout.addSpacing(4)
        self.content_layout.addWidget(self.progress)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
