"""Release notes dialog for an available application update."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class UpdateNotesDialog(QDialog):
    def __init__(
        self,
        version: str,
        current_version: str,
        notes_markdown: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"What's new in Farm Merge Valet {version}")
        self.resize(560, 480)
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"You have version {current_version}. These notes cover every newer release."
        )
        summary.setObjectName("settingsHint")
        summary.setWordWrap(True)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setMarkdown(notes_markdown)
        self.notes.setAccessibleName("Release notes")
        buttons = QDialogButtonBox()
        self.download_button = buttons.addButton("Download", QDialogButtonBox.ButtonRole.AcceptRole)
        close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close_button.setProperty("secondary", True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(summary)
        layout.addWidget(self.notes, 1)
        layout.addWidget(buttons)
