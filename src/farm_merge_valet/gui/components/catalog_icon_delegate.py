"""Per-row icon sizing for mixed structure and item catalog trees."""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QTreeWidgetItem

from farm_merge_valet.gui.components.metrics import POLICY_ICON_SIZE

STRUCTURE_ICON_SIZE = QSize(100, 54)
CATALOG_ROW_ROLE = Qt.ItemDataRole.UserRole + 10


class CatalogRowRole(StrEnum):
    GROUP = "group"
    ITEM = "item"
    STRUCTURE = "structure"


def set_catalog_row_icon(
    item: QTreeWidgetItem,
    icon: QIcon | None,
    role: CatalogRowRole,
) -> None:
    """Assign a shared icon slot so peers align even when an asset is missing."""
    item.setData(0, CATALOG_ROW_ROLE, role.value)
    if role is CatalogRowRole.GROUP:
        item.setIcon(0, QIcon())
        return
    resolved = icon or QIcon()
    if resolved.isNull():
        size = STRUCTURE_ICON_SIZE if role is CatalogRowRole.STRUCTURE else POLICY_ICON_SIZE
        placeholder = QPixmap(size)
        placeholder.fill(Qt.GlobalColor.transparent)
        resolved = QIcon(placeholder)
    item.setIcon(0, resolved)


class CatalogIconDelegate(QStyledItemDelegate):
    """Use the larger media size for rows explicitly marked as structures."""

    def initStyleOption(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        super().initStyleOption(option, index)
        role = index.data(CATALOG_ROW_ROLE)
        if role == CatalogRowRole.GROUP.value:
            option.decorationSize = QSize()
        elif role == CatalogRowRole.STRUCTURE.value:
            option.decorationSize = STRUCTURE_ICON_SIZE
        else:
            option.decorationSize = POLICY_ICON_SIZE
