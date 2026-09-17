"""Shared presentation helpers for item, shop, and recipe policy views."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
)
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

from farm_merge_valet.gui.components.widgets import secondary_button

POLICY_SORT_ROLE = Qt.ItemDataRole.UserRole + 1
POLICY_SEARCH_ROLE = Qt.ItemDataRole.UserRole + 2
_SEARCH_DEBOUNCE_MS = 120


class PolicyTreeItem(QTreeWidgetItem):
    def __init__(self, values: tuple[str, ...], *, tier: int | None = None) -> None:
        super().__init__(values)
        self._tier_sort_key = (0, tier, "") if tier is not None else (1, 0, values[0].casefold())

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        tree = self.treeWidget()
        column = tree.sortColumn() if tree is not None else 0
        if column == 0:
            other_key = (
                other._tier_sort_key
                if isinstance(other, PolicyTreeItem)
                else (1, 0, other.text(0).casefold())
            )
            return self._tier_sort_key < other_key
        return _policy_sort_key(self, column) < _policy_sort_key(other, column)


def _policy_sort_key(item: QTreeWidgetItem, column: int) -> tuple[int, float | str]:
    value = item.data(column, POLICY_SORT_ROLE)
    if isinstance(value, bool):
        return 0, float(value)
    if isinstance(value, int | float):
        return 0, float(value)
    return 1, str(value if value is not None else item.text(column)).casefold()


def set_policy_widget(
    tree: QTreeWidget,
    item: QTreeWidgetItem,
    column: int,
    widget: QWidget,
    *,
    sort_value: bool | int | float | str,
    search_text: str | None = None,
) -> None:
    set_policy_value(item, column, sort_value, search_text=search_text)
    widget.ensurePolished()
    hint = widget.sizeHint()
    widget.setMinimumWidth(hint.width())
    item.setSizeHint(column, hint)
    tree.setItemWidget(item, column, widget)


def set_policy_value(
    item: QTreeWidgetItem,
    column: int,
    sort_value: bool | int | float | str,
    *,
    search_text: str | None = None,
) -> None:
    item.setText(column, "")
    item.setData(column, POLICY_SORT_ROLE, sort_value)
    item.setData(column, POLICY_SEARCH_ROLE, search_text if search_text is not None else sort_value)


def fit_policy_widget_column(tree: QTreeWidget, column: int, *, minimum: int) -> None:
    width = max(minimum, tree.columnWidth(column))
    pending = [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]
    while pending:
        item = pending.pop()
        if item is None:
            continue
        widget = tree.itemWidget(item, column)
        if widget is not None:
            width = max(width, widget.sizeHint().width() + 4)
        pending.extend(item.child(index) for index in range(item.childCount()))
    tree.header().resizeSection(column, width)


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
        self._filter_text = ""
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

    def apply_filter(self, text: str) -> None:
        self._filter_text = text
        filter_policy_tree(
            self._tree,
            text,
            indexed_descendants=self._indexed_descendants,
        )

    def restore_expanded(self, keys: set[Hashable]) -> None:
        for key in self._branches.keys() & keys:
            branch = self._branches[key]
            self.materialize(key)
            branch.item.setExpanded(True)

    def _item_expanded(self, item: QTreeWidgetItem) -> None:
        key = self._keys_by_item.get(id(item))
        if key is not None:
            self.materialize(key)
            self.apply_filter(self._filter_text)

    def _indexed_descendants(self, item: QTreeWidgetItem) -> str:
        key = self._keys_by_item.get(id(item))
        branch = self._branches.get(key) if key is not None else None
        return branch.searchable_text if branch is not None else ""


class PolicyCheckBox(QCheckBox):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)

    def sizeHint(self) -> QSize:
        text_width = self.fontMetrics().horizontalAdvance(self.text())
        return QSize(22 + (text_width + 8 if text_width else 0), 22)

    def _indicator_rect(self) -> QRect:
        box_size = 16
        return QRect(
            (22 - box_size) // 2,
            (self.height() - box_size) // 2,
            box_size,
            box_size,
        )

    def paintEvent(self, _event: QPaintEvent) -> None:
        app = QApplication.instance()
        palette = app.palette() if isinstance(app, QApplication) else self.palette()
        primary = palette.color(QPalette.ColorRole.Link)
        border = palette.color(QPalette.ColorRole.Light)
        surface = palette.color(QPalette.ColorRole.Base)
        text = palette.color(QPalette.ColorRole.Text)
        disabled = palette.color(QPalette.ColorRole.Midlight)
        if not self.isEnabled():
            primary = border = text = disabled

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = self._indicator_rect()
        checked = self.checkState() is not Qt.CheckState.Unchecked
        fill = primary if checked else surface
        keyboard_focus = self.property("keyboardFocus") is True
        outline = primary if checked or self.underMouse() or keyboard_focus else border
        painter.setPen(QPen(outline, 1.4))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 4, 4)

        if keyboard_focus:
            focus = QColor(palette.color(QPalette.ColorRole.Highlight))
            focus.setAlpha(190)
            painter.setPen(QPen(focus, 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 5, 5)

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
    filter_requested = Signal(str)

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
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(_SEARCH_DEBOUNCE_MS)
        self._filter_timer.timeout.connect(self._emit_filter)
        self.search.textChanged.connect(self._schedule_filter)
        self.search.returnPressed.connect(self._emit_filter)
        self.expansion_controls = PolicyTreeExpansionControls(tree, scope)
        layout.addWidget(self.search, 1)
        layout.addWidget(self.expansion_controls)

    def _schedule_filter(self, text: str) -> None:
        if text:
            self._filter_timer.start()
            return
        self._emit_filter()

    def _emit_filter(self) -> None:
        self._filter_timer.stop()
        self.filter_requested.emit(self.search.text())


def configure_policy_view(view: QAbstractItemView) -> None:
    view.setObjectName("policyView")
    view.setAlternatingRowColors(True)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.setTextElideMode(Qt.TextElideMode.ElideRight)
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


def policy_checkbox(
    value: bool | Qt.CheckState, accessible_name: str, *, tristate: bool = False
) -> PolicyCheckBox:
    """A configured toggle for a single policy cell, or (with `tristate=True`
    and a `Qt.CheckState` from `aggregate_check_state`) a group/family/parent
    row's aggregate toggle reflecting its children's shared value."""
    control = PolicyCheckBox()
    configure_policy_toggle(control)
    control.setTristate(tristate)
    if isinstance(value, Qt.CheckState):
        control.setCheckState(value)
    else:
        control.setChecked(value)
    control.setAccessibleName(accessible_name)
    control.setToolTip(accessible_name)
    return control


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


def filter_policy_tree(
    tree: QTreeWidget,
    text: str,
    *,
    indexed_descendants: Callable[[QTreeWidgetItem], str] | None = None,
) -> None:
    needle = text.casefold().strip()

    def update_visibility(item: QTreeWidgetItem, ancestor_matches: bool = False) -> bool:
        own_matches = policy_item_matches(tree, item, needle)
        indexed_matches = bool(
            needle and indexed_descendants is not None and needle in indexed_descendants(item)
        )
        branch_matches = ancestor_matches or own_matches
        descendant_matches = False
        for child_index in range(item.childCount()):
            child = item.child(child_index)
            if child is not None:
                descendant_matches = update_visibility(child, branch_matches) or descendant_matches
        visible = branch_matches or indexed_matches or descendant_matches
        item.setHidden(not visible)
        return visible

    for index in range(tree.topLevelItemCount()):
        parent = tree.topLevelItem(index)
        if parent is not None:
            update_visibility(parent)


def policy_item_matches(tree: QTreeWidget, item: QTreeWidgetItem, text: str) -> bool:
    needle = text.casefold().strip()
    searchable = " ".join(
        " ".join(
            (
                item.text(column),
                str(item.data(column, POLICY_SEARCH_ROLE) or ""),
            )
        )
        for column in range(tree.columnCount())
    )
    return needle in searchable.casefold()


def policy_cell(widget: QWidget, *, centered: bool = True) -> QWidget:
    container = QWidget()
    container.setProperty("policyCell", True)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(8, 0, 8, 0)
    if centered:
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(widget)
    return container


def policy_badge(text: str, *, tone: str | None = None) -> QWidget:
    return policy_badges((text,), tone=tone)


def policy_badges(texts: tuple[str, ...], *, tone: str | None = None) -> QWidget:
    labels = []
    for text in texts:
        label = QLabel(text)
        label.setObjectName("policyBadge")
        if tone is not None:
            label.setProperty("tone", tone)
        labels.append(label)
    container = QWidget()
    container.setProperty("policyCell", True)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(8, 0, 8, 0)
    layout.setSpacing(4)
    for label in labels:
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
