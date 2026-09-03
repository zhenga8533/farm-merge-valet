"""Persistent marketplace catalog and auto-purchase policy page."""

from __future__ import annotations

import logging
from collections import defaultdict

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

from farm_merge_valet.catalog.marketplace import MARKETPLACE_ICON_ASSETS, marketplace_catalog
from farm_merge_valet.catalog.models import CatalogItem, ItemCatalog, load_item_catalog
from farm_merge_valet.config import AppConfig
from farm_merge_valet.core.marketplace import MarketplaceOffer
from farm_merge_valet.gui.components.bulk_header import BulkToggleHeader
from farm_merge_valet.gui.components.configuration_header import ConfigurationHeader
from farm_merge_valet.gui.components.policy_view import (
    PolicyCheckBox,
    PolicyTreeItem,
    PolicyTreeToolbar,
    aggregate_check_state,
    configure_policy_toggle,
    configure_policy_view,
    filter_policy_tree,
    fit_policy_widget_column,
    policy_badge,
    policy_cell,
    set_policy_widget,
)
from farm_merge_valet.gui.pages.base import AppPage, ConfigEdit
from farm_merge_valet.gui.services.assets import CatalogIconLoader

logger = logging.getLogger(__name__)

_ICON_SIZE = QSize(40, 40)
_SORT_COLUMNS = {"offer": 0, "cost": 1, "enabled": 2}
_REWARD_FAMILY_ALIASES = {
    "crates": frozenset({"crate"}),
    "gems": frozenset({"gem"}),
    "event_energy": frozenset({"event_energy", "time_limited_event_energy"}),
}


def _load_catalog(config: AppConfig) -> ItemCatalog | None:
    path = config.catalog_dir / "catalog.json"
    try:
        return load_item_catalog(path) if path.is_file() else None
    except (OSError, ValueError) as exc:
        logger.warning("Could not load marketplace icons from the item catalog: %s", exc)
        return None


class MarketplacePage(AppPage):
    config_edited = Signal(object)
    reset_requested = Signal()

    def __init__(
        self,
        config: AppConfig,
        *,
        icons: CatalogIconLoader | None = None,
    ) -> None:
        super().__init__(
            "Marketplace",
            "Choose exact flash candidates and genuine free claims to purchase automatically.",
        )
        self._config = config
        self._icons = icons or CatalogIconLoader(config.catalog_dir)
        self._catalog: ItemCatalog | None = None
        self._toggles: dict[str, PolicyCheckBox] = {}
        self.configuration_header = ConfigurationHeader("Reset marketplace policies")
        self.configuration_header.reset_requested.connect(self.reset_requested)
        self.saved_label = self.configuration_header.status_label
        self.page_layout.addWidget(self.configuration_header)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(("Offer", "Cost", ""))
        self.bulk_header = BulkToggleHeader({2: "Auto-purchase"}, self.tree)
        self.bulk_header.toggled.connect(self._set_all)
        self.tree.setHeader(self.bulk_header)
        configure_policy_view(self.tree)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(22)
        self.tree.setIconSize(_ICON_SIZE)
        self.tree.setUniformRowHeights(True)
        self.tree.setAccessibleName("Marketplace purchase policies")
        self.bulk_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 3):
            self.bulk_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.toolbar = PolicyTreeToolbar(
            self.tree,
            placeholder="Search marketplace offers…",
            accessible_name="Search marketplace offers",
            scope="marketplace groups",
        )
        self.toolbar.filter_requested.connect(lambda text: filter_policy_tree(self.tree, text))
        self.page_layout.addWidget(self.toolbar)
        self.page_layout.addWidget(self.tree, 1)
        self.populate()
        self._apply_sort_preference()
        self.bulk_header.sortIndicatorChanged.connect(self._sort_changed)

    def apply_config(self, config: AppConfig, *, refresh: bool = True) -> None:
        catalog_changed = config.catalog_dir != self._config.catalog_dir
        sort_changed = (
            config.marketplace_sort_column != self._config.marketplace_sort_column
            or config.marketplace_sort_descending != self._config.marketplace_sort_descending
        )
        self._config = config
        self._icons.set_catalog_dir(config.catalog_dir)
        if sort_changed:
            self._apply_sort_preference()
        if refresh or catalog_changed:
            self.populate()
        self.configuration_header.mark_saved()

    def mark_saving(self, _field: str | None = None) -> None:
        self.configuration_header.mark_saving()

    def mark_error(self, message: str) -> None:
        self.configuration_header.mark_error(message)

    def reload_catalog(self) -> None:
        self.populate()

    def reload_catalog_if_missing(self) -> None:
        if self._catalog is None:
            self.populate()

    def populate(self) -> None:
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        self._toggles = {}
        self._catalog = _load_catalog(self._config)
        grouped: dict[str, list[MarketplaceOffer]] = defaultdict(list)
        for offer in marketplace_catalog():
            grouped[offer.group].append(offer)
        for group, offers in grouped.items():
            parent = PolicyTreeItem((group, "", ""))
            parent.setSizeHint(0, QSize(0, 64))
            parent_font = parent.font(0)
            parent_font.setWeight(QFont.Weight.DemiBold)
            parent.setFont(0, parent_font)
            parent.setData(0, Qt.ItemDataRole.UserRole, ("group", group))
            self.tree.addTopLevelItem(parent)
            set_policy_widget(
                self.tree,
                parent,
                1,
                policy_badge("Group"),
                sort_value="Group",
                search_text="Group",
            )
            for offer in offers:
                self._add_offer(parent, offer)
            parent.setExpanded(group in {"Ingredients", "Free Claims"})
        self.tree.setSortingEnabled(True)
        fit_policy_widget_column(self.tree, 1, minimum=124)
        self.tree.sortByColumn(
            _SORT_COLUMNS[self._config.marketplace_sort_column],
            Qt.SortOrder.DescendingOrder
            if self._config.marketplace_sort_descending
            else Qt.SortOrder.AscendingOrder,
        )
        self._sync_bulk_header()
        filter_policy_tree(self.tree, self.toolbar.search.text())

    def _add_offer(self, parent: QTreeWidgetItem, offer: MarketplaceOffer) -> None:
        cost = (
            "Free"
            if offer.payment_type == "free"
            else f"{offer.payment_amount} {(offer.payment_key or '').title()}"
        )
        quantity = f" \u00d7{offer.reward_amount}" if offer.reward_amount > 1 else ""
        item = PolicyTreeItem((f"{offer.display_name}{quantity}", "", ""))
        item.setSizeHint(0, QSize(0, 64))
        marketplace_icon = MARKETPLACE_ICON_ASSETS.get(offer.offer_id)
        if marketplace_icon is not None:
            item.setIcon(0, self._icons.icon_for_path(marketplace_icon[1]))
        else:
            item.setIcon(0, self._icons.icon_for(self._catalog_item(offer.reward_key)))
        item.setData(0, Qt.ItemDataRole.UserRole, offer.policy_key)
        item.setToolTip(0, offer.policy_key)
        parent.addChild(item)
        set_policy_widget(
            self.tree,
            item,
            1,
            policy_badge(cost),
            sort_value=cost,
            search_text=cost,
        )
        toggle = PolicyCheckBox()
        configure_policy_toggle(toggle)
        toggle.setChecked(self._config.marketplace_policy_enabled(offer.policy_key))
        toggle.setAccessibleName(f"{offer.display_name}: auto-purchase")
        toggle.toggled.connect(
            lambda value, key=offer.policy_key: self._set_enabled(key, value)
        )
        set_policy_widget(
            self.tree, item, 2, policy_cell(toggle), sort_value=toggle.isChecked()
        )
        self._toggles[offer.policy_key] = toggle

    def _catalog_item(self, reward_key: str) -> CatalogItem | None:
        if self._catalog is None:
            return None
        exact = self._catalog.items.get(reward_key)
        if exact is not None:
            return exact
        family_ids = {reward_key, *_REWARD_FAMILY_ALIASES.get(reward_key, ())}
        candidates = [
            item for item in self._catalog.items.values() if item.family_id in family_ids
        ]
        if reward_key == "event_energy":
            candidates.extend(
                item
                for item in self._catalog.items.values()
                if "event" in item.game_id and "energy" in item.game_id
            )
        return min(
            {item.game_id: item for item in candidates}.values(),
            key=lambda item: (item.tier is None, item.tier or 0, item.game_id),
            default=None,
        )

    def _set_enabled(self, key: str, enabled: bool) -> None:
        values = dict(self._config.marketplace_policy_overrides)
        if enabled:
            values[key] = True
        else:
            values.pop(key, None)
        self._config = AppConfig.model_validate(
            self._config.model_copy(
                update={"marketplace_policy_overrides": values}
            ).model_dump()
        )
        self.config_edited.emit(
            ConfigEdit({"marketplace_policy_overrides": values}, source="marketplace")
        )
        self._sync_bulk_header()

    def _set_all(self, _column: int, enabled: bool) -> None:
        values = {offer.policy_key: True for offer in marketplace_catalog()} if enabled else {}
        self._config = AppConfig.model_validate(
            self._config.model_copy(
                update={"marketplace_policy_overrides": values}
            ).model_dump()
        )
        self.config_edited.emit(
            ConfigEdit({"marketplace_policy_overrides": values}, source="marketplace")
        )
        self.populate()

    def _sync_bulk_header(self) -> None:
        values = [self._config.marketplace_policy_enabled(key) for key in self._toggles]
        self.bulk_header.set_state(2, aggregate_check_state(values), enabled=bool(values))

    def _apply_sort_preference(self) -> None:
        self.bulk_header.setSortIndicator(
            _SORT_COLUMNS[self._config.marketplace_sort_column],
            Qt.SortOrder.DescendingOrder
            if self._config.marketplace_sort_descending
            else Qt.SortOrder.AscendingOrder,
        )

    def _sort_changed(self, column: int, order: Qt.SortOrder) -> None:
        key = next(name for name, value in _SORT_COLUMNS.items() if value == column)
        changes = {
            "marketplace_sort_column": key,
            "marketplace_sort_descending": order is Qt.SortOrder.DescendingOrder,
        }
        if all(getattr(self._config, name) == value for name, value in changes.items()):
            return
        self._config = AppConfig.model_validate(
            self._config.model_copy(update=changes).model_dump()
        )
        self.config_edited.emit(ConfigEdit(changes, source="marketplace"))
