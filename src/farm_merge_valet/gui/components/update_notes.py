"""Release notes dialog for the running version and available updates."""

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
        title: str,
        summary: str,
        notes_markdown: str,
        *,
        offer_download: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 480)
        layout = QVBoxLayout(self)
        summary_label = QLabel(summary)
        summary_label.setObjectName("settingsHint")
        summary_label.setWordWrap(True)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setMarkdown(notes_markdown)
        self.notes.setAccessibleName("Release notes")
        buttons = QDialogButtonBox()
        self.download_button = (
            buttons.addButton("Download", QDialogButtonBox.ButtonRole.AcceptRole)
            if offer_download
            else None
        )
        close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close_button.setProperty("secondary", True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(summary_label)
        layout.addWidget(self.notes, 1)
        layout.addWidget(buttons)
