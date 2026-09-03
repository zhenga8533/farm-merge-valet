"""Shared construction for sortable policy trees."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QTreeWidget

from farm_merge_valet.gui.components.bulk_header import BulkToggleHeader
from farm_merge_valet.gui.components.metrics import POLICY_TREE_INDENTATION
from farm_merge_valet.gui.components.policy_view import (
    PolicyTreeToolbar,
    configure_policy_view,
)


@dataclass(frozen=True)
class PolicyTreeScaffold:
    tree: QTreeWidget
    header: BulkToggleHeader
    toolbar: PolicyTreeToolbar


def create_policy_tree(
    *,
    header_labels: tuple[str, ...],
    bulk_labels: Mapping[int, str],
    accessible_name: str,
    search_placeholder: str,
    search_accessible_name: str,
    scope: str,
    icon_size: QSize,
    minimum_section_size: int | None = None,
) -> PolicyTreeScaffold:
    tree = QTreeWidget()
    tree.setHeaderLabels(header_labels)
    header = BulkToggleHeader(bulk_labels, tree)
    tree.setHeader(header)
    configure_policy_view(tree)
    tree.setRootIsDecorated(True)
    tree.setIndentation(POLICY_TREE_INDENTATION)
    tree.setIconSize(icon_size)
    tree.setUniformRowHeights(True)
    tree.setAccessibleName(accessible_name)
    if minimum_section_size is not None:
        header.setMinimumSectionSize(minimum_section_size)
    toolbar = PolicyTreeToolbar(
        tree,
        placeholder=search_placeholder,
        accessible_name=search_accessible_name,
        scope=scope,
    )
    return PolicyTreeScaffold(tree, header, toolbar)
