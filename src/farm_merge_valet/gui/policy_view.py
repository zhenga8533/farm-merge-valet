"""Shared presentation helpers for item, shop, and recipe policy views."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent, QPalette, QPen
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


@dataclass(frozen=True)
class _LazyPolicyBranch:
    item: QTreeWidgetItem
    materialize: Callable[[], None]
    searchable_text: str


class LazyPolicyBranches:
    """Materialize child rows only when a policy branch needs to be visible."""

    def __init__(self, tree: QTreeWidget) -> None:
        self._tree = tree
        self._branches: dict[Hashable, _LazyPolicyBranch] = {}
        self._keys_by_item: dict[int, Hashable] = {}
        self._materialized: set[Hashable] = set()
        tree.itemExpanded.connect(self._item_expanded)

    def reset(self) -> None:
        self._branches.clear()
        self._keys_by_item.clear()
        self._materialized.clear()

    def register(
        self,
        key: Hashable,
        item: QTreeWidgetItem,
        materialize: Callable[[], None],
        *,
        searchable_text: str,
    ) -> None:
        item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        self._branches[key] = _LazyPolicyBranch(item, materialize, searchable_text.casefold())
        self._keys_by_item[id(item)] = key

    def materialize(self, key: Hashable) -> None:
        branch = self._branches.get(key)
        if branch is None or key in self._materialized:
            return
        self._materialized.add(key)
        sorting_enabled = self._tree.isSortingEnabled()
        self._tree.setSortingEnabled(False)
        self._tree.setUpdatesEnabled(False)
        try:
            branch.materialize()
            branch.item.setChildIndicatorPolicy(
                QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless
            )
        finally:
            self._tree.setUpdatesEnabled(True)
            self._tree.setSortingEnabled(sorting_enabled)

    def materialize_matches(self, text: str) -> None:
        needle = text.casefold().strip()
        if not needle:
            return
        for key, branch in tuple(self._branches.items()):
            if key not in self._materialized and needle in branch.searchable_text:
                self.materialize(key)

    def restore_expanded(self, keys: set[Hashable]) -> None:
        for key in self._branches.keys() & keys:
            branch = self._branches[key]
            self.materialize(key)
            branch.item.setExpanded(True)

    def _item_expanded(self, item: QTreeWidgetItem) -> None:
        key = self._keys_by_item.get(id(item))
        if key is not None:
            self.materialize(key)


class PolicyCheckBox(QCheckBox):
    def sizeHint(self) -> QSize:
        text_width = self.fontMetrics().horizontalAdvance(self.text())
        return QSize(22 + (text_width + 8 if text_width else 0), 22)

    def paintEvent(self, _event: QPaintEvent) -> None:
        app = QApplication.instance()
        palette = app.palette() if isinstance(app, QApplication) else self.palette()
        primary = palette.color(QPalette.ColorRole.Link)
        border = palette.color(QPalette.ColorRole.Mid)
        surface = palette.color(QPalette.ColorRole.Base)
        text = palette.color(QPalette.ColorRole.Text)
        disabled = palette.color(QPalette.ColorRole.Midlight)
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


class _PolicyUnavailableLabel(QLabel):
    def paintEvent(self, _event: QPaintEvent) -> None:
        app = QApplication.instance()
        palette = app.palette() if isinstance(app, QApplication) else self.palette()
        painter = QPainter(self)
        painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
        painter.setFont(self.font())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


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


def expanded_policy_keys(tree: QTreeWidget) -> set[Hashable]:
    expanded: set[Hashable] = set()
    pending = [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]
    while pending:
        item = pending.pop()
        if item is None:
            continue
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if item.isExpanded() and isinstance(key, Hashable):
            expanded.add(key)
        pending.extend(item.child(index) for index in range(item.childCount()))
    return expanded


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


def policy_status(text: str, tone: str, tooltip: str) -> QWidget:
    label = QLabel(text)
    label.setObjectName("policyStatus")
    label.setProperty("tone", tone)
    label.setAccessibleName(tooltip)
    label.setToolTip(tooltip)
    return policy_cell(label)


def policy_unavailable(reason: str = "Not applicable to this item") -> QWidget:
    label = _PolicyUnavailableLabel("—")
    label.setObjectName("policyUnavailable")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setAccessibleName(reason)
    label.setToolTip(reason)
    return policy_cell(label)
