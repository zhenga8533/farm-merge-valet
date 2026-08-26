"""Shared presentation helpers for item, shop, and recipe policy views."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTreeWidget,
    QWidget,
)


class PolicyCheckBox(QCheckBox):
    def sizeHint(self) -> QSize:
        text_width = self.fontMetrics().horizontalAdvance(self.text())
        return QSize(22 + (text_width + 8 if text_width else 0), 22)

    def paintEvent(self, _event: QPaintEvent) -> None:
        app = QApplication.instance()
        primary = QColor(str(app.property("policyPrimary"))) if app else QColor("#1f883d")
        border = QColor(str(app.property("policyBorder"))) if app else QColor("#8c959f")
        surface = QColor(str(app.property("policyInput"))) if app else QColor("#ffffff")
        text = QColor(str(app.property("policyText"))) if app else QColor("#1f2328")
        disabled = QColor(str(app.property("policyDisabled"))) if app else QColor("#afb8c1")
        if not self.isEnabled():
            primary = border = text = disabled

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box_size = 16
        box_x = 1
        box_y = (self.height() - box_size) // 2
        box = QRect(box_x, box_y, box_size, box_size)
        checked = self.checkState() is not Qt.CheckState.Unchecked
        fill = primary if checked else surface
        outline = primary if checked or self.underMouse() or self.hasFocus() else border
        painter.setPen(QPen(outline, 1.4))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 4, 4)

        mark_pen = QPen(QColor("#ffffff"), 2.0)
        mark_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        mark_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(mark_pen)
        if self.checkState() is Qt.CheckState.Checked:
            path = QPainterPath()
            path.moveTo(box.left() + 4, box.center().y())
            path.lineTo(box.left() + 7, box.bottom() - 4)
            path.lineTo(box.right() - 3, box.top() + 4)
            painter.drawPath(path)
        elif self.checkState() is Qt.CheckState.PartiallyChecked:
            painter.drawLine(box.left() + 4, box.center().y(), box.right() - 4, box.center().y())

        if self.text():
            painter.setPen(text)
            text_rect = self.rect().adjusted(25, 0, 0, 0)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self.text(),
            )


class PolicyTreeExpansionControls(QWidget):
    def __init__(self, tree: QTreeWidget, scope: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.collapse_button = QPushButton("Collapse all")
        self.expand_button = QPushButton("Expand all")
        for button in (self.collapse_button, self.expand_button):
            button.setProperty("secondary", True)

        self.collapse_button.setAccessibleName(f"Collapse all {scope}")
        self.expand_button.setAccessibleName(f"Expand all {scope}")
        self.collapse_button.clicked.connect(tree.collapseAll)
        self.expand_button.clicked.connect(tree.expandAll)
        layout.addWidget(self.collapse_button)
        layout.addWidget(self.expand_button)


def configure_policy_view(view: QAbstractItemView) -> None:
    view.setObjectName("policyView")
    view.setAlternatingRowColors(True)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.setTextElideMode(Qt.TextElideMode.ElideRight)
    view.setStyleSheet("outline: 0;")
    if isinstance(view, QTableWidget):
        view.setShowGrid(False)
        view.verticalHeader().setVisible(False)


def configure_policy_toggle(control: QCheckBox) -> None:
    control.setProperty("policyToggle", True)
    control.setCursor(Qt.CursorShape.PointingHandCursor)
    control.setMinimumSize(18, 18)


def filter_policy_tree(tree: QTreeWidget, text: str) -> None:
    needle = text.casefold().strip()

    def update_visibility(item, ancestor_matches: bool = False) -> bool:
        own_matches = (
            needle in " ".join(item.text(column) for column in range(tree.columnCount())).casefold()
        )
        branch_matches = ancestor_matches or own_matches
        descendant_matches = False
        for child_index in range(item.childCount()):
            child = item.child(child_index)
            if child is not None:
                descendant_matches = update_visibility(child, branch_matches) or descendant_matches
        visible = branch_matches or descendant_matches
        item.setHidden(not visible)
        if needle and descendant_matches:
            item.setExpanded(True)
        return visible

    for index in range(tree.topLevelItemCount()):
        parent = tree.topLevelItem(index)
        if parent is not None:
            update_visibility(parent)


def policy_cell(widget: QWidget, *, centered: bool = True) -> QWidget:
    container = QWidget()
    container.setProperty("policyCell", True)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(8, 0, 8, 0)
    if centered:
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(widget)
    return container


def policy_badge(text: str) -> QWidget:
    label = QLabel(text)
    label.setObjectName("policyBadge")
    container = QWidget()
    container.setProperty("policyCell", True)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(8, 0, 8, 0)
    layout.addWidget(label)
    layout.addStretch()
    return container


def policy_unavailable(reason: str = "Not applicable to this item") -> QWidget:
    label = QLabel("—")
    label.setObjectName("policyUnavailable")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setAccessibleName(reason)
    label.setToolTip(reason)
    return policy_cell(label)
