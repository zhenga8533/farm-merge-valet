"""Per-row icon sizing for mixed structure and item catalog trees."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QSize, Qt
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

from farm_merge_valet.gui.components.metrics import POLICY_ICON_SIZE

STRUCTURE_ICON_SIZE = QSize(100, 54)


class CatalogIconDelegate(QStyledItemDelegate):
    """Use the larger media size for rows explicitly marked as structures."""

    def initStyleOption(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        super().initStyleOption(option, index)
        identity = index.data(Qt.ItemDataRole.UserRole)
        option.decorationSize = (
            STRUCTURE_ICON_SIZE
            if isinstance(identity, tuple) and identity and identity[0] in {"shop", "building"}
            else POLICY_ICON_SIZE
        )
