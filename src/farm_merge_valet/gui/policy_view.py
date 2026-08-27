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
    QLineEdit,
    QTableWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from farm_merge_valet.gui.widgets import secondary_button


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

        self.collapse_button = secondary_button("Collapse all")
        self.expand_button = secondary_button("Expand all")

        self.collapse_button.setAccessibleName(f"Collapse all {scope}")
        self.expand_button.setAccessibleName(f"Expand all {scope}")
        self.collapse_button.clicked.connect(tree.collapseAll)
        self.expand_button.clicked.connect(tree.expandAll)
        layout.addWidget(self.collapse_button)
        layout.addWidget(self.expand_button)


class PolicyTreeToolbar(QWidget):
    def __init__(
        self,
        tree: QTreeWidget,
        *,
        placeholder: str,
        accessible_name: str,
        scope: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(placeholder)
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName(accessible_name)
        self.expansion_controls = PolicyTreeExpansionControls(tree, scope)
        layout.addWidget(self.search, 1)
        layout.addWidget(self.expansion_controls)


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


def aggregate_check_state(values: list[bool]) -> Qt.CheckState:
    return (
        Qt.CheckState.Checked
        if values and all(values)
        else Qt.CheckState.Unchecked
        if not values or not any(values)
        else Qt.CheckState.PartiallyChecked
    )


def filter_policy_tree(tree: QTreeWidget, text: str) -> None:
    needle = text.casefold().strip()

    def update_visibility(item: QTreeWidgetItem, ancestor_matches: bool = False) -> bool:
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
