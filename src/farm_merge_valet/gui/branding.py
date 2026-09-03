"""Application-owned branding assets."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap


def app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#238636"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(18, 16, 18, 32)
    painter.drawEllipse(30, 16, 18, 32)
    painter.end()
    return QIcon(pixmap)
