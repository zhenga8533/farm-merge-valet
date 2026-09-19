"""Persistent marketplace catalog and auto-purchase policy page."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHeaderView, QTreeWidgetItem, QWidget

from farm_merge_valet.catalog.models import CatalogItem, ItemCatalog
from farm_merge_valet.config import AppConfig
from farm_merge_valet.core.marketplace import MarketplaceOffer
from farm_merge_valet.gui.components.catalog_icon_delegate import (
    CatalogRowRole,
    set_catalog_row_icon,
)
from farm_merge_valet.gui.components.configuration_header import ConfigurationHeader
from farm_merge_valet.gui.components.incremental_work import IncrementalPopulation
from farm_merge_valet.gui.components.metrics import (
    POLICY_ICON_SIZE,
    POLICY_MEDIA_BADGE_COLUMN_WIDTH,
    POLICY_MEDIA_ROW_HEIGHT,
)
from farm_merge_valet.gui.components.policy_tree import create_policy_tree
from farm_merge_valet.gui.components.policy_view import (
    PolicyCheckBox,
    PolicyTreeItem,
    aggregate_check_state,
    apply_policy_override,
    expanded_policy_keys,
    filter_policy_tree,
    fit_policy_widget_column,
    policy_badge,
    policy_cell,
    policy_checkbox,
    set_policy_value,
    set_policy_widget,
)
from farm_merge_valet.gui.pages.base import CatalogTreePage, ConfigEdit
from farm_merge_valet.gui.services.assets import CatalogIconLoader
from farm_merge_valet.gui.services.catalog import load_gui_catalog

_SORT_COLUMNS = {"offer": 0, "cost": 1, "enabled": 2}


class MarketplacePage(CatalogTreePage):
    config_edited = Signal(object)
    reset_requested = Signal()

    def __init__(
        self,
        config: AppConfig,
        *,
        icons: CatalogIconLoader | None = None,
        populate_immediately: bool = True,
    ) -> None:
        super().__init__(
            "Marketplace",
            "Choose exact flash candidates and genuine free claims to purchase automatically.",
        )
        self._config = config
        self._icons = icons or CatalogIconLoader(config.catalog_dir)
        self._catalog: ItemCatalog | None = None
        self._toggles: dict[str, PolicyCheckBox] = {}
        self._tree_items: dict[str, QTreeWidgetItem] = {}
        self._group_toggles: dict[str, PolicyCheckBox] = {}
        self._group_policy_keys: dict[str, tuple[str, ...]] = {}
        self._group_items: dict[str, QTreeWidgetItem] = {}
        self._family_toggles: dict[tuple[str, str], PolicyCheckBox] = {}
        self._family_policy_keys: dict[tuple[str, str], tuple[str, ...]] = {}
        self._family_items: dict[tuple[str, str], QTreeWidgetItem] = {}
        self._has_populated_catalog = False
        self._population = IncrementalPopulation(self)
        self.configuration_header = ConfigurationHeader("Reset marketplace policies")
        self.configuration_header.reset_requested.connect(self.reset_requested)
        self.saved_label = self.configuration_header.status_label
        self.page_layout.addWidget(self.configuration_header)
        self._init_catalog_scaffold(
            "Loading marketplace…",
            "Preparing locally cached offers, item families, and controls.",
        )
        scaffold = create_policy_tree(
            header_labels=("Offer", "Cost", ""),
            bulk_labels={2: "Auto-purchase"},
            accessible_name="Marketplace purchase policies",
            search_placeholder="Search marketplace offers\u2026",
            search_accessible_name="Search marketplace offers",
            scope="marketplace groups",
            icon_size=POLICY_ICON_SIZE,
        )
        self.tree = scaffold.tree
        self.bulk_header = scaffold.header
        self.bulk_header.toggled.connect(self._set_all)
        self.bulk_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 3):
            self.bulk_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.toolbar = scaffold.toolbar
        self.toolbar.filter_requested.connect(self._filter)
        self.page_layout.addWidget(self.toolbar)
        self.page_layout.addWidget(self.tree, 1)
        if populate_immediately:
            self.populate()
        self._apply_sort_preference()
        self.bulk_header.sortIndicatorChanged.connect(self._sort_changed)

    def _catalog_content_widgets(self) -> tuple[QWidget, ...]:
        return (self.toolbar, self.tree)

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
        if refresh and self._populated:
            catalog_missing = self._catalog is None or not self._catalog.marketplace_offers
            if catalog_changed or catalog_missing:
                self.populate()
            else:
                self._sync_controls()
        self.configuration_header.mark_saved()

    def mark_saving(self, _field: str | None = None) -> None:
        self.configuration_header.mark_saving()

    def mark_error(self, message: str) -> None:
        self.configuration_header.mark_error(message)

    def reload_catalog(self) -> None:
        if self._populated:
            self.populate()

    def reload_catalog_if_missing(self) -> None:
        if self._populated and (self._catalog is None or not self._catalog.marketplace_offers):
            self.populate()

    def populate(self) -> None:
        self._population.run_now(self._populate_steps)

    def _populate_steps(self) -> Iterator[None]:
        self._populated = True
        expanded = expanded_policy_keys(self.tree)
        expand_groups_by_default = not self._has_populated_catalog
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        self._toggles = {}
        self._tree_items = {}
        self._group_toggles = {}
        self._group_policy_keys = {}
        self._group_items = {}
        self._family_toggles = {}
        self._family_policy_keys = {}
        self._family_items = {}
        self._catalog = load_gui_catalog(self._config)
        grouped: dict[str, list[MarketplaceOffer]] = defaultdict(list)
        if self._catalog is None or not self._catalog.marketplace_offers:
            self._show_catalog_onboarding()
            return
        for offer in self._catalog.marketplace_offers:
            grouped[offer.group].append(offer)
        for group, offers in grouped.items():
            families: dict[tuple[str, str], list[MarketplaceOffer]] = defaultdict(list)
            for offer in offers:
                family_key, family_name = self._offer_family(offer)
                families[(family_key, family_name)].append(offer)
            group_label = self._group_label(group, families)
            parent = PolicyTreeItem((group_label, "", ""))
            parent.setSizeHint(0, QSize(0, POLICY_MEDIA_ROW_HEIGHT))
            parent_font = parent.font(0)
            parent_font.setWeight(QFont.Weight.DemiBold)
            parent.setFont(0, parent_font)
            parent.setData(0, Qt.ItemDataRole.UserRole, ("group", group))
            set_catalog_row_icon(parent, None, CatalogRowRole.GROUP)
            self.tree.addTopLevelItem(parent)
            set_policy_widget(
                self.tree,
                parent,
                1,
                policy_badge("Group"),
                sort_value="Group",
                search_text="Group",
            )
            if group == "Free Claims":
                for offer in offers:
                    self._add_offer(parent, offer, nested=False)
            else:
                for (family_key, family_name), family_offers in families.items():
                    if len(family_offers) == 1:
                        self._add_offer(parent, family_offers[0], nested=False)
                    else:
                        self._add_family(
                            parent,
                            group,
                            family_key,
                            family_name,
                            family_offers,
                            expanded,
                        )
            policy_keys = tuple(offer.policy_key for offer in offers)
            state = aggregate_check_state(
                [self._config.marketplace_policy_enabled(key) for key in policy_keys]
            )
            group_toggle = policy_checkbox(
                state, f"{group_label}: auto-purchase for all offers", tristate=True
            )
            group_toggle.clicked.connect(
                lambda checked, keys=policy_keys: self._set_group(keys, checked)
            )
            set_policy_widget(
                self.tree,
                parent,
                2,
                policy_cell(group_toggle),
                sort_value=state.value,
            )
            self._group_toggles[group] = group_toggle
            self._group_policy_keys[group] = policy_keys
            self._group_items[group] = parent
            parent.setExpanded(expand_groups_by_default or ("group", group) in expanded)
            yield
        self.tree.setSortingEnabled(True)
        fit_policy_widget_column(
            self.tree,
            1,
            minimum=POLICY_MEDIA_BADGE_COLUMN_WIDTH,
        )
        self.tree.sortByColumn(
            _SORT_COLUMNS[self._config.marketplace_sort_column],
            Qt.SortOrder.DescendingOrder
            if self._config.marketplace_sort_descending
            else Qt.SortOrder.AscendingOrder,
        )
        self._filter(self.toolbar.search.text())
        self._has_populated_catalog = True
        self._show_catalog_content()

    def _add_family(
        self,
        parent: QTreeWidgetItem,
        group: str,
        family_key: str,
        family_name: str,
        offers: list[MarketplaceOffer],
        expanded: set[object],
    ) -> None:
        family = PolicyTreeItem((family_name, "", ""))
        family.setData(0, Qt.ItemDataRole.UserRole, ("family", group, family_key))
        family.setSizeHint(0, QSize(0, POLICY_MEDIA_ROW_HEIGHT))
        representative = min(
            (
                item
                for offer in offers
                if (item := self._catalog_item(offer.reward_key)) is not None
            ),
            key=lambda item: (item.tier is None, item.tier or 0, item.game_id),
            default=None,
        )
        set_catalog_row_icon(family, self._icons.icon_for(representative), CatalogRowRole.ITEM)
        if representative is not None:
            family.setToolTip(0, f"{family_name}\nRepresentative: {representative.game_id}")
        parent.addChild(family)
        set_policy_widget(
            self.tree,
            family,
            1,
            policy_badge("Family"),
            sort_value="Family",
            search_text=family_name,
        )
        for offer in offers:
            self._add_offer(family, offer, nested=True)
        policy_keys = tuple(offer.policy_key for offer in offers)
        state = aggregate_check_state(
            [self._config.marketplace_policy_enabled(key) for key in policy_keys]
        )
        toggle = policy_checkbox(
            state, f"{family_name}: auto-purchase for all offers", tristate=True
        )
        toggle.clicked.connect(lambda checked, keys=policy_keys: self._set_group(keys, checked))
        set_policy_widget(self.tree, family, 2, policy_cell(toggle), sort_value=state.value)
        identity = (group, family_key)
        self._family_toggles[identity] = toggle
        self._family_policy_keys[identity] = policy_keys
        self._family_items[identity] = family
        family.setExpanded(("family", group, family_key) in expanded)

    def _add_offer(self, parent: QTreeWidgetItem, offer: MarketplaceOffer, *, nested: bool) -> None:
        cost = (
            "Free"
            if offer.payment_type == "free"
            else f"{offer.payment_amount} {(offer.payment_key or '').title()}"
        )
        cost_tone = "free" if offer.payment_type == "free" else offer.payment_key
        quantity = f" \u00d7{offer.reward_amount}" if offer.reward_amount > 1 else ""
        catalog_item = self._catalog_item(offer.reward_key)
        label = offer.display_name
        if catalog_item is not None:
            label = (
                catalog_item.variant_label
                if nested and catalog_item.variant_label
                else (
                    f"Tier {catalog_item.tier}"
                    if nested and catalog_item.tier is not None
                    else catalog_item.display_name
                )
            )
        item = PolicyTreeItem((f"{label}{quantity}", "", ""))
        item.setSizeHint(0, QSize(0, POLICY_MEDIA_ROW_HEIGHT))
        set_catalog_row_icon(item, self._icons.icon_for(catalog_item), CatalogRowRole.ITEM)
        item.setData(0, Qt.ItemDataRole.UserRole, offer.policy_key)
        item.setToolTip(0, offer.policy_key)
        parent.addChild(item)
        set_policy_widget(
            self.tree,
            item,
            1,
            policy_badge(cost, tone=cost_tone),
            sort_value=cost,
            search_text=cost,
        )
        toggle = policy_checkbox(
            self._config.marketplace_policy_enabled(offer.policy_key),
            f"{offer.display_name}: auto-purchase",
        )
        toggle.toggled.connect(lambda value, key=offer.policy_key: self._set_enabled(key, value))
        set_policy_widget(self.tree, item, 2, policy_cell(toggle), sort_value=toggle.isChecked())
        self._toggles[offer.policy_key] = toggle
        self._tree_items[offer.policy_key] = item

    def _offer_family(self, offer: MarketplaceOffer) -> tuple[str, str]:
        item = self._catalog_item(offer.reward_key)
        if item is None:
            return offer.reward_key, offer.display_name
        return item.group_id or item.family_id, item.group_name or item.display_name

    def _group_label(
        self, group: str, families: dict[tuple[str, str], list[MarketplaceOffer]]
    ) -> str:
        catalog_items = [
            item
            for offers in families.values()
            for offer in offers
            if (item := self._catalog_item(offer.reward_key)) is not None
        ]
        if catalog_items and all(item.category == "structures" for item in catalog_items):
            return "Buildings"
        family_names = {family_name for _, family_name in families}
        if group in family_names and len(family_names) > 1:
            others = sorted(family_names - {group})
            return " / ".join((group, *others))
        return group

    def _catalog_item(self, reward_key: str) -> CatalogItem | None:
        if self._catalog is None:
            return None
        exact = self._catalog.items.get(reward_key)
        if exact is not None:
            return exact
        family_ids = {reward_key}
        if reward_key.endswith("s"):
            family_ids.add(reward_key[:-1])
        candidates = [item for item in self._catalog.items.values() if item.family_id in family_ids]
        return min(
            {item.game_id: item for item in candidates}.values(),
            key=lambda item: (item.tier is None, item.tier or 0, item.game_id),
            default=None,
        )

    def _emit(self, **changes: object) -> None:
        self._config = AppConfig.model_validate(
            self._config.model_copy(update=changes).model_dump()
        )
        self.config_edited.emit(ConfigEdit(changes, source="marketplace"))

    def _set_enabled(self, key: str, enabled: bool) -> None:
        values = dict(self._config.marketplace_policy_overrides)
        apply_policy_override(
            values, key, enabled, self._config.marketplace_policy_default_enabled(key)
        )
        self._emit(marketplace_policy_overrides=values)
        self._sync_controls()

    def _filter(self, text: str) -> None:
        filter_policy_tree(self.tree, text)
        self._sync_bulk_header()

    def _bulk_policy_keys(self) -> list[str]:
        if not self.toolbar.search.text().strip():
            return list(self._toggles)
        filter_policy_tree(self.tree, self.toolbar.search.text())
        return [key for key, item in self._tree_items.items() if not item.isHidden()]

    def _set_group(self, policy_keys: tuple[str, ...], enabled: bool) -> None:
        values = dict(self._config.marketplace_policy_overrides)
        for key in policy_keys:
            apply_policy_override(
                values, key, enabled, self._config.marketplace_policy_default_enabled(key)
            )
        self._emit(marketplace_policy_overrides=values)
        self._sync_controls()

    def _set_all(self, _column: int, enabled: bool) -> None:
        values = dict(self._config.marketplace_policy_overrides)
        for key in self._bulk_policy_keys():
            apply_policy_override(
                values, key, enabled, self._config.marketplace_policy_default_enabled(key)
            )
        self._emit(marketplace_policy_overrides=values)
        self._sync_controls()

    def _sync_controls(self) -> None:
        for key, toggle in self._toggles.items():
            enabled = self._config.marketplace_policy_enabled(key)
            toggle.blockSignals(True)
            toggle.setChecked(enabled)
            toggle.blockSignals(False)
            set_policy_value(self._tree_items[key], 2, enabled)
        for group, toggle in self._group_toggles.items():
            state = aggregate_check_state(
                [
                    self._config.marketplace_policy_enabled(key)
                    for key in self._group_policy_keys[group]
                ]
            )
            toggle.blockSignals(True)
            toggle.setCheckState(state)
            toggle.blockSignals(False)
            set_policy_value(self._group_items[group], 2, state.value)
        for family, toggle in self._family_toggles.items():
            state = aggregate_check_state(
                [
                    self._config.marketplace_policy_enabled(key)
                    for key in self._family_policy_keys[family]
                ]
            )
            toggle.blockSignals(True)
            toggle.setCheckState(state)
            toggle.blockSignals(False)
            set_policy_value(self._family_items[family], 2, state.value)
        self._sync_bulk_header()

    def _sync_bulk_header(self) -> None:
        keys = self._bulk_policy_keys()
        values = [self._config.marketplace_policy_enabled(key) for key in keys]
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
        self._emit(**changes)
