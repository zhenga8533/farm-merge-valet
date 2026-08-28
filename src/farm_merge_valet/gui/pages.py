"""Focused page widgets for the desktop application shell."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from functools import partial

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from farm_merge_valet.config import AppConfig, ItemPolicyOverride
from farm_merge_valet.core.board import item_tier_policy_key
from farm_merge_valet.core.item_catalog import (
    CatalogItem,
    ItemCatalog,
    TileInteractionMode,
    load_item_catalog,
)
from farm_merge_valet.core.upgrade_progress import (
    UpgradeProgress,
    UpgradeTargetProgress,
    UpgradeTierState,
)
from farm_merge_valet.gui.action_button import ActionButton
from farm_merge_valet.gui.assets import CatalogIconLoader
from farm_merge_valet.gui.bulk_header import BulkToggleHeader
from farm_merge_valet.gui.catalog_onboarding import CatalogOnboarding
from farm_merge_valet.gui.configuration_header import ConfigurationHeader
from farm_merge_valet.gui.controller import ApplicationState, ApplicationStatus
from farm_merge_valet.gui.loading_state import LoadingState
from farm_merge_valet.gui.page_base import AppPage, ConfigEdit
from farm_merge_valet.gui.policy_view import (
    LazyPolicyBranches,
    PolicyCheckBox,
    PolicyTreeItem,
    PolicyTreeToolbar,
    aggregate_check_state,
    configure_policy_toggle,
    configure_policy_view,
    expanded_policy_keys,
    fit_policy_widget_column,
    policy_badge,
    policy_badges,
    policy_cell,
    policy_status,
    policy_unavailable,
    set_policy_value,
    set_policy_widget,
)
from farm_merge_valet.gui.widgets import secondary_button

logger = logging.getLogger(__name__)

_ITEM_ICON_SIZE = QSize(40, 40)
_SHOP_ICON_SIZE = QSize(100, 54)
_ITEM_SORT_COLUMNS = {
    "item": 0,
    "category": 1,
    "enabled": 2,
    "merge": 3,
    "merge_five": 4,
    "interact": 5,
    "remove": 6,
}
_SHOP_SORT_COLUMNS = {"item": 0, "type": 1, "enabled": 2}


class _ShopIconDelegate(QStyledItemDelegate):
    def initStyleOption(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        super().initStyleOption(option, index)
        identity = index.data(Qt.ItemDataRole.UserRole)
        option.decorationSize = (
            _SHOP_ICON_SIZE
            if isinstance(identity, tuple) and identity and identity[0] == "shop"
            else _ITEM_ICON_SIZE
        )


@dataclass(frozen=True)
class _ItemPolicyRow:
    policy_key: str
    family_key: str
    item: CatalogItem
    supports_merge: bool
    supports_interact: bool
    supports_remove: bool

    def supports(self, field: str) -> bool:
        return (
            field == "enabled"
            or field in {"merge", "prefer_merge_five"}
            and self.supports_merge
            or field == "interact"
            and self.supports_interact
            or field == "always_remove"
            and self.supports_remove
        )


class DashboardPage(AppPage):
    run_requested = Signal()
    pause_requested = Signal()
    overlay_requested = Signal()

    def __init__(self) -> None:
        super().__init__("Dashboard", "Control automation and review live state.")
        metrics = QGridLayout()
        metrics.setSpacing(10)
        self.mode_value = self._metric(metrics, 0, 0, "Mode", "Stopped")
        self.browser_value = self._metric(metrics, 0, 1, "Browser", "Not checked")
        self.runtime_value = self._metric(metrics, 1, 0, "Runtime", "Waiting")
        self.phase_value = self._metric(metrics, 1, 1, "Phase", "—")
        self.page_layout.addLayout(metrics)

        activity = QFrame()
        activity.setObjectName("card")
        activity_layout = QVBoxLayout(activity)
        activity_label = QLabel("Last activity")
        activity_label.setObjectName("metricLabel")
        self.activity_value = QLabel("No activity yet")
        self.activity_value.setWordWrap(True)
        activity_layout.addWidget(activity_label)
        activity_layout.addWidget(self.activity_value)
        self.page_layout.addWidget(activity)

        controls = QHBoxLayout()
        self.run_button = ActionButton("Start")
        self.pause_button = ActionButton("Pause", secondary=True)
        self.overlay_button = secondary_button("Show compact overlay")
        self.run_button.clicked.connect(self.run_requested)
        self.pause_button.clicked.connect(self.pause_requested)
        self.overlay_button.clicked.connect(self.overlay_requested)
        for button in (self.run_button, self.pause_button):
            controls.addWidget(button)
        controls.addStretch()
        controls.addWidget(self.overlay_button)
        self.page_layout.addLayout(controls)
        self.page_layout.addStretch()
        self._start_stop_hotkey: str | None = None
        self._pause_hotkey: str | None = None
        self._status = ApplicationStatus()
        self.set_status(ApplicationStatus())

    @staticmethod
    def _metric(layout: QGridLayout, row: int, column: int, title: str, value: str) -> QLabel:
        card = QFrame()
        card.setObjectName("metricCard")
        card_layout = QVBoxLayout(card)
        label = QLabel(title)
        label.setObjectName("metricLabel")
        output = QLabel(value)
        output.setObjectName("metricValue")
        output.setWordWrap(True)
        card_layout.addWidget(label)
        card_layout.addWidget(output)
        layout.addWidget(card, row, column)
        return output

    def set_status(self, status: ApplicationStatus) -> None:
        self._status = status
        self.mode_value.setText(status.mode)
        self.browser_value.setText(status.browser)
        self.runtime_value.setText(status.runtime)
        self.phase_value.setText(status.phase)
        self.activity_value.setText(status.last_activity)
        active = status.state.active
        self.run_button.setEnabled(status.state is not ApplicationState.STOPPING)
        self.pause_button.setEnabled(active and status.state is not ApplicationState.STOPPING)
        run_label = "Stop" if active else "Start"
        pause_label = (
            "Resume"
            if status.state in {ApplicationState.PAUSED, ApplicationState.RESUMING}
            else "Pause"
        )
        self.run_button.set_action(run_label, self._start_stop_hotkey, danger=active)
        self.pause_button.set_action(pause_label, self._pause_hotkey)

    def set_hotkeys(self, start_stop: str | None, pause_resume: str | None) -> None:
        self._start_stop_hotkey = start_stop
        self._pause_hotkey = pause_resume
        self.set_status(self._status)

    def set_overlay_visible(self, visible: bool) -> None:
        self.overlay_button.setText("Hide compact overlay" if visible else "Show compact overlay")


def _load_catalog(config: AppConfig) -> ItemCatalog | None:
    path = config.catalog_dir / "catalog.json"
    try:
        return load_item_catalog(path) if path.is_file() else None
    except (OSError, ValueError) as exc:
        logger.warning("Could not load the GUI item catalog: %s", exc)
        return None


class ItemsPage(AppPage):
    config_edited = Signal(object)
    reset_requested = Signal()
    catalog_setup_requested = Signal()

    def __init__(
        self,
        config: AppConfig,
        *,
        icons: CatalogIconLoader | None = None,
        populate_immediately: bool = True,
    ) -> None:
        super().__init__(
            "Item policies",
            "Configure automation behavior for every discovered item family.",
        )
        self._config = config
        self._icons = icons or CatalogIconLoader(config.catalog_dir)
        self.configuration_header = ConfigurationHeader("Reset item policies")
        self.configuration_header.reset_requested.connect(self.reset_requested)
        self.saved_label = self.configuration_header.status_label
        self.page_layout.addWidget(self.configuration_header)
        self.catalog_onboarding = CatalogOnboarding()
        self.catalog_onboarding.setup_requested.connect(self.catalog_setup_requested)
        self.page_layout.addWidget(
            self.catalog_onboarding,
            1,
            Qt.AlignmentFlag.AlignCenter,
        )
        self.loading_state = LoadingState(
            "Loading item policies…",
            "Preparing locally cached item families and controls.",
        )
        self.loading_state.setVisible(False)
        self.page_layout.addWidget(
            self.loading_state,
            1,
            Qt.AlignmentFlag.AlignCenter,
        )

        self.table = QTreeWidget()
        self.table.setColumnCount(7)
        self.table.setHeaderLabels(("Item / tier", "Category", "", "", "", "", ""))
        self.bulk_header = BulkToggleHeader(
            {
                2: "Enabled",
                3: "Merge",
                4: "Merge 5",
                5: "Interact",
                6: "Remove",
            },
            self.table,
        )
        self.bulk_header.toggled.connect(self._set_all)
        self.table.setHeader(self.bulk_header)
        self.table.setIconSize(_ITEM_ICON_SIZE)
        self.bulk_header.setMinimumSectionSize(64)
        self.bulk_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.bulk_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.bulk_header.resizeSection(1, 108)
        policy_widths = {2: 92, 3: 78, 4: 88, 5: 92, 6: 88}
        for column, width in policy_widths.items():
            self.bulk_header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.bulk_header.resizeSection(column, width)
        configure_policy_view(self.table)
        self.table.setIndentation(22)
        self.table.setRootIsDecorated(True)
        self.table.setUniformRowHeights(True)
        self._apply_sort_preference()
        self.table.setAccessibleName("Item automation policies")

        self.toolbar = PolicyTreeToolbar(
            self.table,
            placeholder="Search item families and tiers…",
            accessible_name="Search item families and tiers",
            scope="item groups",
        )
        self.search = self.toolbar.search
        self.expansion_controls = self.toolbar.expansion_controls
        self.page_layout.addWidget(self.toolbar)
        self.page_layout.addWidget(self.table, 1)
        self.toolbar.filter_requested.connect(self._filter)
        self._upgrade_progress: UpgradeProgress | None = None
        self._upgrade_targets: dict[str, UpgradeTargetProgress] = {}
        self._catalog_loaded = False
        self._populated = False
        self._population_pending = False
        self._row_definitions: list[_ItemPolicyRow] = []
        self._policy_controls: list[
            tuple[QTreeWidgetItem, PolicyCheckBox, tuple[_ItemPolicyRow, ...], str]
        ] = []
        self._lazy_branches = LazyPolicyBranches(self.table)
        if populate_immediately:
            self.populate()
        self.bulk_header.sortIndicatorChanged.connect(self._sort_changed)

    def apply_config(self, config: AppConfig, *, refresh: bool = True) -> None:
        catalog_changed = config.catalog_dir != self._config.catalog_dir
        sort_changed = (
            config.items_sort_column != self._config.items_sort_column
            or config.items_sort_descending != self._config.items_sort_descending
        )
        self._config = config
        self._icons.set_catalog_dir(config.catalog_dir)
        if sort_changed:
            self._apply_sort_preference()
        if refresh and self._populated:
            if catalog_changed or not self._catalog_loaded:
                self.populate()
            else:
                self._sync_policy_controls()
        self.configuration_header.mark_saved()

    def reload_catalog(self) -> None:
        if self._populated:
            self.populate()

    def reload_catalog_if_missing(self) -> None:
        if self._populated and not self._catalog_loaded:
            self.populate()

    def ensure_populated(self, *, deferred: bool = False) -> None:
        if self._populated or self._population_pending:
            return
        if not deferred:
            self.populate()
            return
        self._population_pending = True
        self._show_loading()
        QTimer.singleShot(16, self._finish_deferred_population)

    def _finish_deferred_population(self) -> None:
        try:
            self.populate()
        finally:
            self._population_pending = False

    def set_upgrade_progress(self, progress: UpgradeProgress | None) -> None:
        if progress == self._upgrade_progress:
            return
        self._upgrade_progress = progress
        if self._catalog_loaded:
            self.populate()

    def _apply_sort_preference(self) -> None:
        column = _ITEM_SORT_COLUMNS[self._config.items_sort_column]
        order = (
            Qt.SortOrder.DescendingOrder
            if self._config.items_sort_descending
            else Qt.SortOrder.AscendingOrder
        )
        self.bulk_header.setSortIndicator(column, order)
        if self.table.isSortingEnabled():
            self.table.sortByColumn(column, order)

    def _sort_changed(self, column: int, order: Qt.SortOrder) -> None:
        self.table.sortByColumn(column, order)
        sort_key = next(
            key
            for key, configured_column in _ITEM_SORT_COLUMNS.items()
            if configured_column == column
        )
        descending = order is Qt.SortOrder.DescendingOrder
        if (
            sort_key == self._config.items_sort_column
            and descending == self._config.items_sort_descending
        ):
            return
        self._emit(
            items_sort_column=sort_key,
            items_sort_descending=descending,
        )

    def _emit(self, **changes: object) -> None:
        self._config = AppConfig.model_validate(
            self._config.model_copy(update=changes).model_dump()
        )
        self.config_edited.emit(ConfigEdit(changes, source="items"))

    def mark_saving(self, _field: str | None = None) -> None:
        self.configuration_header.mark_saving()

    def mark_error(self, message: str) -> None:
        self.configuration_header.mark_error(message)

    def populate(self) -> None:
        self._populated = True
        expanded_keys = expanded_policy_keys(self.table)
        sort_column = self.table.sortColumn()
        sort_order = self.bulk_header.sortIndicatorOrder()
        self.table.setSortingEnabled(False)
        self.table.clear()
        self._row_definitions = []
        self._policy_controls = []
        self._lazy_branches.reset()
        self._upgrade_targets = {}
        catalog = _load_catalog(self._config)
        if catalog is None:
            self._catalog_loaded = False
            self._show_catalog_onboarding()
            return
        self._catalog_loaded = True
        self._show_catalog_content()
        grouped: dict[str, list[CatalogItem]] = defaultdict(list)
        for item in catalog.items.values():
            grouped[item.presentation_key].append(item)
        upgrade_target_names: dict[str, str] = {}
        upgrade_target_families: dict[str, str] = {}
        upgrade_groups: dict[str, list[CatalogItem]] = {}
        for key, items in tuple(grouped.items()):
            if not items or items[0].category != "upgrade_cards":
                continue
            grouped.pop(key)
            targets: list[tuple[str, str, str, UpgradeTargetProgress | None]] = []
            if self._upgrade_progress is not None:
                for target_progress in self._upgrade_progress.targets:
                    producer = catalog.items.get(target_progress.producer_blueprint_id)
                    if producer is None or producer.category not in {"animals", "crops"}:
                        continue
                    targets.append(
                        (
                            target_progress.target_id,
                            producer.display_name,
                            producer.family_id,
                            target_progress,
                        )
                    )
            else:
                fallback_targets: dict[str, tuple[str, str]] = {}
                for target in catalog.items.values():
                    if target.category in {"animals", "crops"}:
                        target_id = target.upgrade_target_id or target.family_id
                        fallback_targets.setdefault(
                            target_id,
                            (target.display_name, target.family_id),
                        )
                targets.extend(
                    (target_id, target_name, producer_family_id, None)
                    for target_id, (target_name, producer_family_id) in fallback_targets.items()
                )
            for target_id, target_name, producer_family_id, maybe_progress in targets:
                variant_key = f"{key}/{target_id}"
                upgrade_groups[variant_key] = items
                upgrade_target_names[variant_key] = target_name
                upgrade_target_families[variant_key] = producer_family_id
                if maybe_progress is not None:
                    self._upgrade_targets[variant_key] = maybe_progress
        grouped.update(upgrade_groups)
        families = []
        for key, items in grouped.items():
            configurable = [item for item in items if self._item_is_configurable(item)]
            if configurable:
                families.append(
                    (key, sorted(configurable, key=lambda item: (item.tier or 0, item.game_id)))
                )
        families.sort(
            key=lambda entry: (
                entry[1][0].category,
                entry[1][0].display_name.casefold(),
            )
        )
        if not families:
            self._show_empty("No configurable item families have been discovered yet")
            return
        roots_by_family_id: dict[str, QTreeWidgetItem] = {}
        for family_key, items in families:
            definitions = [
                self._row_definition(family_key, item, tier_scoped=len(items) > 1) for item in items
            ]
            self._row_definitions.extend(definitions)
            representative = items[0]
            upgrade_target = upgrade_target_names.get(family_key)
            family_name = (
                "Upgrade cards" if upgrade_target else representative.presentation_group_name
            )
            if len(definitions) == 1:
                root = self._new_policy_item(
                    family_name,
                    representative,
                    self._icons,
                    category=representative.category,
                )
                root.setData(0, Qt.ItemDataRole.UserRole, family_key)
                self._attach_policy_root(
                    root,
                    representative,
                    roots_by_family_id,
                    upgrade_target,
                    upgrade_target_families.get(family_key),
                )
                self._set_category_badge(root, representative)
                self._add_policy_controls(root, definitions[0], family_name)
                continue
            root = self._new_policy_item(
                family_name,
                representative,
                self._icons,
                category=representative.category,
            )
            root.setData(0, Qt.ItemDataRole.UserRole, family_key)
            if upgrade_target:
                root.setToolTip(
                    0,
                    f"Upgrade cards targeting {upgrade_target}. "
                    "Each child configures one card tier.",
                )
            self._attach_policy_root(
                root,
                representative,
                roots_by_family_id,
                upgrade_target,
                upgrade_target_families.get(family_key),
            )
            self._set_category_badge(root, representative)
            self._add_family_controls(root, family_key, definitions, family_name)
            tier_definitions = tuple(definitions)
            self._lazy_branches.register(
                family_key,
                root,
                partial(self._populate_item_tiers, root, tier_definitions, family_name),
                searchable_text=" ".join(
                    (
                        family_name,
                        representative.category.replace("_", " ").title(),
                        family_key,
                        *(row.item.game_id for row in definitions),
                        *(" ".join(sorted(row.item.traits)) for row in definitions),
                        *(f"Tier {row.item.tier} {row.item.display_name}" for row in definitions),
                    )
                ),
            )
        self._lazy_branches.restore_expanded(expanded_keys)
        fit_policy_widget_column(self.table, 1, minimum=116)
        self.table.setSortingEnabled(True)
        self.bulk_header.setSortIndicatorShown(False)
        self.table.sortByColumn(sort_column, sort_order)
        self._sync_bulk_header()
        self._filter(self.search.text())

    def _populate_item_tiers(
        self,
        root: QTreeWidgetItem,
        definitions: tuple[_ItemPolicyRow, ...],
        family_name: str,
    ) -> None:
        for definition in definitions:
            tier = definition.item.tier
            child_name = definition.item.variant_label or (
                f"Tier {tier}" if tier is not None else definition.item.display_name
            )
            child = self._new_policy_item(
                child_name,
                definition.item,
                self._icons,
                tier=tier,
            )
            child.setData(0, Qt.ItemDataRole.UserRole, definition.policy_key)
            root.addChild(child)
            self._add_policy_controls(child, definition, f"{family_name}, {child_name}")

    def _attach_policy_root(
        self,
        root: QTreeWidgetItem,
        representative: CatalogItem,
        roots_by_family_id: dict[str, QTreeWidgetItem],
        upgrade_target: str | None,
        upgrade_target_family_id: str | None,
    ) -> None:
        if upgrade_target is not None:
            target_root = roots_by_family_id.get(upgrade_target_family_id or "")
            if target_root is not None:
                target_root.addChild(root)
                return
            root.setText(0, f"{upgrade_target} Upgrade Cards")
        self.table.addTopLevelItem(root)
        if upgrade_target is None and representative.category in {"animals", "crops"}:
            roots_by_family_id[representative.family_id] = root

    @staticmethod
    def _item_is_configurable(item: CatalogItem) -> bool:
        if "unlinked" in item.traits:
            return False
        supports_interact = item.tile_interaction_mode in {
            TileInteractionMode.DIRECT,
            TileInteractionMode.DIRECT_OPT_IN,
            TileInteractionMode.REWARD,
            TileInteractionMode.CLEAR,
        } or (item.category in {"animals", "crops"} and item.tier == 4)
        return (
            item.merge_target is not None
            or supports_interact
            or item.tile_interaction_mode is TileInteractionMode.UPGRADE
            or "shovelable" in item.traits
            or "shovelable" in item.capabilities
        )

    def _row_definition(
        self,
        family_key: str,
        item: CatalogItem,
        *,
        tier_scoped: bool,
    ) -> _ItemPolicyRow:
        upgrade_progress = self._upgrade_targets.get(family_key)
        upgrade_interactable = bool(
            item.tile_interaction_mode is TileInteractionMode.UPGRADE
            and upgrade_progress is not None
            and item.tier is not None
            and upgrade_progress.tier_state(item.tier) is UpgradeTierState.NOT_APPLIED
        )
        return _ItemPolicyRow(
            (
                item_tier_policy_key(family_key, item.tier)
                if tier_scoped and item.tier is not None
                else item.policy_key
            ),
            family_key,
            item,
            item.merge_target is not None,
            item.tile_interaction_mode
            in {
                TileInteractionMode.DIRECT,
                TileInteractionMode.DIRECT_OPT_IN,
                TileInteractionMode.REWARD,
                TileInteractionMode.CLEAR,
            }
            or (item.category in {"animals", "crops"} and item.tier == 4)
            or upgrade_interactable,
            "shovelable" in item.traits or "shovelable" in item.capabilities,
        )

    def _new_policy_item(
        self,
        name: str,
        catalog_item: CatalogItem,
        icons: CatalogIconLoader,
        *,
        category: str | None = None,
        tier: int | None = None,
    ) -> QTreeWidgetItem:
        item = PolicyTreeItem(
            (name, "", "", "", "", "", ""),
            tier=tier,
        )
        item.setIcon(0, icons.icon_for(catalog_item))
        item.setSizeHint(0, QSize(0, 52))
        item.setToolTip(0, f"{name}\nGame ID: {catalog_item.game_id}")
        font = item.font(0)
        font.setWeight(QFont.Weight.DemiBold if category is not None else QFont.Weight.Normal)
        item.setFont(0, font)
        return item

    def _set_category_badge(self, item: QTreeWidgetItem, catalog_item: CatalogItem) -> None:
        label = catalog_item.category.replace("_", " ").title()
        labels: tuple[str, ...] = (label,)
        if "event" in catalog_item.traits:
            labels += ("Event",)
        set_policy_widget(
            self.table,
            item,
            1,
            policy_badges(labels),
            sort_value=label,
            search_text=" ".join(labels),
        )

    def _add_policy_controls(
        self, item: QTreeWidgetItem, definition: _ItemPolicyRow, label: str
    ) -> None:
        policy = self._config.item_policy(definition.policy_key)
        for column, field in self._policy_fields().items():
            if (
                field == "interact"
                and definition.item.tile_interaction_mode is TileInteractionMode.UPGRADE
                and not definition.supports_interact
            ):
                self._add_upgrade_tier_status(item, column, definition, label)
                continue
            if not definition.supports(field):
                set_policy_widget(
                    self.table,
                    item,
                    column,
                    policy_unavailable(),
                    sort_value=-1,
                    search_text="Not applicable",
                )
                continue
            value = getattr(policy, field)
            field_label = self._policy_field_label(definition.item, field)
            control = self._policy_checkbox(value, f"{label}: {field_label}")
            control.toggled.connect(
                lambda checked, row=definition, name=field: self.set_override(
                    row.policy_key, row.family_key, name, checked
                )
            )
            set_policy_widget(
                self.table,
                item,
                column,
                policy_cell(control),
                sort_value=value,
            )
            self._policy_controls.append((item, control, (definition,), field))

    def _add_family_controls(
        self,
        item: QTreeWidgetItem,
        family_key: str,
        definitions: list[_ItemPolicyRow],
        label: str,
    ) -> None:
        for column, field in self._policy_fields().items():
            if (
                field == "interact"
                and definitions
                and definitions[0].item.tile_interaction_mode is TileInteractionMode.UPGRADE
                and not any(definition.supports_interact for definition in definitions)
            ):
                self._add_upgrade_family_status(item, column, family_key, label)
                continue
            applicable = [definition for definition in definitions if definition.supports(field)]
            if not applicable:
                set_policy_widget(
                    self.table,
                    item,
                    column,
                    policy_unavailable(),
                    sort_value=-1,
                    search_text="Not applicable",
                )
                continue
            values = [
                getattr(self._config.item_policy(definition.policy_key), field)
                for definition in applicable
            ]
            state = aggregate_check_state(values)
            field_label = self._policy_field_label(applicable[0].item, field)
            control = self._policy_checkbox(
                state,
                f"{label}: {field_label} for all tiers",
                tristate=True,
            )
            control.clicked.connect(
                lambda checked, key=family_key, rows=applicable, name=field: self._set_family(
                    key, rows, name, checked
                )
            )
            set_policy_widget(
                self.table,
                item,
                column,
                policy_cell(control),
                sort_value=state.value,
            )
            self._policy_controls.append((item, control, tuple(applicable), field))

    def _add_upgrade_family_status(
        self,
        item: QTreeWidgetItem,
        column: int,
        family_key: str,
        label: str,
    ) -> None:
        progress = self._upgrade_targets.get(family_key)
        if progress is None:
            self._set_policy_status(
                item,
                column,
                "Unknown",
                "muted",
                f"{label}: connect the game to read applied upgrade tiers",
            )
            return
        self._set_policy_status(
            item,
            column,
            "Applied",
            "applied",
            f"{label}: all available card tiers are already applied",
        )

    def _add_upgrade_tier_status(
        self,
        item: QTreeWidgetItem,
        column: int,
        definition: _ItemPolicyRow,
        label: str,
    ) -> None:
        progress = self._upgrade_targets.get(definition.family_key)
        tier = definition.item.tier
        if progress is None or tier is None:
            self._set_policy_status(
                item,
                column,
                "Unknown",
                "muted",
                f"{label}: connect the game to read this upgrade tier's state",
            )
            return
        self._set_policy_status(
            item,
            column,
            "Applied",
            "applied",
            f"{label}: this card tier or a higher tier is already applied",
        )

    def _set_policy_status(
        self,
        item: QTreeWidgetItem,
        column: int,
        text: str,
        tone: str,
        tooltip: str,
    ) -> None:
        set_policy_widget(
            self.table,
            item,
            column,
            policy_status(text, tone, tooltip),
            sort_value=text,
            search_text=text,
        )

    def _sync_policy_controls(self) -> None:
        for item, control, definitions, field in self._policy_controls:
            values = [
                getattr(self._config.item_policy(definition.policy_key), field)
                for definition in definitions
            ]
            state = aggregate_check_state(values)
            control.blockSignals(True)
            control.setCheckState(state)
            control.blockSignals(False)
            set_policy_value(
                item,
                self._column_for_field(field),
                state.value,
            )
        self._sync_bulk_header()

    def _column_for_field(self, field: str) -> int:
        return next(column for column, name in self._policy_fields().items() if name == field)

    @staticmethod
    def _policy_field_label(item: CatalogItem, field: str) -> str:
        if field != "interact":
            return field.replace("_", " ")
        if item.tile_interaction_mode is TileInteractionMode.CLEAR:
            return "clear"
        if item.tile_interaction_mode is TileInteractionMode.UPGRADE:
            return "apply"
        if item.category in {"animals", "crops"}:
            return "harvest"
        return "interact"

    @staticmethod
    def _policy_checkbox(
        value: bool | Qt.CheckState, accessible_name: str, *, tristate: bool = False
    ) -> PolicyCheckBox:
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

    @staticmethod
    def _policy_fields() -> dict[int, str]:
        return {
            2: "enabled",
            3: "merge",
            4: "prefer_merge_five",
            5: "interact",
            6: "always_remove",
        }

    def _show_empty(self, message: str) -> None:
        item = QTreeWidgetItem((message, "", "", "", "", "", ""))
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setSizeHint(0, QSize(0, 52))
        self.table.addTopLevelItem(item)
        for column in range(2, 7):
            self.bulk_header.set_state(column, Qt.CheckState.Unchecked, enabled=False)

    def _show_catalog_onboarding(self) -> None:
        self.loading_state.setVisible(False)
        self.catalog_onboarding.setVisible(True)
        self.toolbar.setVisible(False)
        self.table.setVisible(False)

    def _show_catalog_content(self) -> None:
        self.loading_state.setVisible(False)
        self.catalog_onboarding.setVisible(False)
        self.toolbar.setVisible(True)
        self.table.setVisible(True)

    def _show_loading(self) -> None:
        self.catalog_onboarding.setVisible(False)
        self.toolbar.setVisible(False)
        self.table.setVisible(False)
        self.loading_state.setVisible(True)

    def set_catalog_setup_busy(self, busy: bool) -> None:
        self.catalog_onboarding.set_busy(busy)

    def set_catalog_setup_status(self, message: str, *, error: bool = False) -> None:
        self.catalog_onboarding.set_status(message, error=error)

    def _filter(self, text: str) -> None:
        self._lazy_branches.apply_filter(text)

    def set_override(self, key: str, family_key: str, field: str, value: bool) -> None:
        overrides = dict(self._config.item_policy_overrides)
        values = overrides.get(key, ItemPolicyOverride()).model_dump(exclude_none=True)
        inherited_value = getattr(self._config.item_policy(family_key), field)
        if value == inherited_value:
            values.pop(field, None)
        else:
            values[field] = value
        if values:
            overrides[key] = ItemPolicyOverride.model_validate(values)
        else:
            overrides.pop(key, None)
        self._emit(item_policy_overrides=overrides)
        self._sync_policy_controls()

    def _set_family(
        self,
        _family_key: str,
        definitions: list[_ItemPolicyRow],
        field: str,
        value: bool,
    ) -> None:
        overrides = dict(self._config.item_policy_overrides)
        for definition in definitions:
            values = overrides.get(definition.policy_key, ItemPolicyOverride()).model_dump(
                exclude_none=True
            )
            inherited_value = getattr(self._config.item_policy(definition.family_key), field)
            if value == inherited_value:
                values.pop(field, None)
            else:
                values[field] = value
            if values:
                overrides[definition.policy_key] = ItemPolicyOverride.model_validate(values)
            else:
                overrides.pop(definition.policy_key, None)
        self._emit(item_policy_overrides=overrides)
        self._sync_policy_controls()

    def _set_all(self, column: int, value: bool) -> None:
        field = self._policy_fields()[column]
        overrides = dict(self._config.item_policy_overrides)
        for definition in self._row_definitions:
            if not definition.supports(field):
                continue
            key = definition.policy_key
            values = overrides.get(key, ItemPolicyOverride()).model_dump(exclude_none=True)
            inherited_value = getattr(self._config.item_policy(definition.family_key), field)
            if value == inherited_value:
                values.pop(field, None)
            else:
                values[field] = value
            if values:
                overrides[key] = ItemPolicyOverride.model_validate(values)
            else:
                overrides.pop(key, None)
        self._emit(item_policy_overrides=overrides)
        self._sync_policy_controls()

    def _sync_bulk_header(self) -> None:
        for column, field in self._policy_fields().items():
            values = [
                getattr(self._config.item_policy(definition.policy_key), field)
                for definition in self._row_definitions
                if definition.supports(field)
            ]
            state = aggregate_check_state(values)
            self.bulk_header.set_state(column, state, enabled=bool(values))


class ShopsPage(AppPage):
    config_edited = Signal(object)
    reset_requested = Signal()
    catalog_setup_requested = Signal()

    def __init__(
        self,
        config: AppConfig,
        *,
        icons: CatalogIconLoader | None = None,
        populate_immediately: bool = True,
    ) -> None:
        super().__init__("Shops", "Configure every discovered shop and recipe independently.")
        self._config = config
        self._icons = icons or CatalogIconLoader(config.catalog_dir)
        self.configuration_header = ConfigurationHeader("Reset shop policies")
        self.configuration_header.reset_requested.connect(self.reset_requested)
        self.saved_label = self.configuration_header.status_label
        self.page_layout.addWidget(self.configuration_header)
        self.catalog_onboarding = CatalogOnboarding()
        self.catalog_onboarding.setup_requested.connect(self.catalog_setup_requested)
        self.page_layout.addWidget(
            self.catalog_onboarding,
            1,
            Qt.AlignmentFlag.AlignCenter,
        )
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(("Shop / recipe", "Type", ""))
        self.bulk_header = BulkToggleHeader({2: "Enabled"}, self.tree)
        self.bulk_header.toggled.connect(self._set_all)
        self.tree.setHeader(self.bulk_header)
        self.tree.setIconSize(_SHOP_ICON_SIZE)
        self.tree.setItemDelegate(_ShopIconDelegate(self.tree))
        self.bulk_header.setMinimumSectionSize(96)
        self.bulk_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.bulk_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.bulk_header.resizeSection(1, 124)
        self.bulk_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        configure_policy_view(self.tree)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(22)
        self.tree.setRootIsDecorated(True)
        self._apply_sort_preference()
        self.tree.setAccessibleName("Shop and recipe policies")

        self.toolbar = PolicyTreeToolbar(
            self.tree,
            placeholder="Search shops and recipes…",
            accessible_name="Search shops and recipes",
            scope="shop and recipe groups",
        )
        self.search = self.toolbar.search
        self.expansion_controls = self.toolbar.expansion_controls
        self.page_layout.addWidget(self.toolbar)
        self.page_layout.addWidget(self.tree, 1)
        self.toolbar.filter_requested.connect(self._filter)
        self._catalog_loaded = False
        self._populated = False
        self._catalog_keys: list[tuple[str, str]] = []
        self._toggles: dict[tuple[str, str], PolicyCheckBox] = {}
        self._tree_items: dict[tuple[str, str], QTreeWidgetItem] = {}
        self._lazy_branches = LazyPolicyBranches(self.tree)
        if populate_immediately:
            self.populate()
        self.bulk_header.sortIndicatorChanged.connect(self._sort_changed)

    def apply_config(self, config: AppConfig, *, refresh: bool = True) -> None:
        catalog_changed = config.catalog_dir != self._config.catalog_dir
        sort_changed = (
            config.shops_sort_column != self._config.shops_sort_column
            or config.shops_sort_descending != self._config.shops_sort_descending
        )
        self._config = config
        self._icons.set_catalog_dir(config.catalog_dir)
        if sort_changed:
            self._apply_sort_preference()
        if refresh and self._populated:
            if catalog_changed or not self._catalog_loaded:
                self.populate()
            else:
                self._sync_controls()
        self.configuration_header.mark_saved()

    def reload_catalog(self) -> None:
        if self._populated:
            self.populate()

    def reload_catalog_if_missing(self) -> None:
        if self._populated and not self._catalog_loaded:
            self.populate()

    def ensure_populated(self) -> None:
        if not self._populated:
            self.populate()

    def _apply_sort_preference(self) -> None:
        column = _SHOP_SORT_COLUMNS[self._config.shops_sort_column]
        order = (
            Qt.SortOrder.DescendingOrder
            if self._config.shops_sort_descending
            else Qt.SortOrder.AscendingOrder
        )
        self.bulk_header.setSortIndicator(column, order)
        if self.tree.isSortingEnabled():
            self.tree.sortByColumn(column, order)

    def _sort_changed(self, column: int, order: Qt.SortOrder) -> None:
        sort_key = next(
            key
            for key, configured_column in _SHOP_SORT_COLUMNS.items()
            if configured_column == column
        )
        descending = order is Qt.SortOrder.DescendingOrder
        if (
            sort_key == self._config.shops_sort_column
            and descending == self._config.shops_sort_descending
        ):
            return
        self._emit(
            shops_sort_column=sort_key,
            shops_sort_descending=descending,
        )

    def _emit(self, **changes: object) -> None:
        self._config = AppConfig.model_validate(
            self._config.model_copy(update=changes).model_dump()
        )
        self.config_edited.emit(ConfigEdit(changes, source="shops"))

    def mark_saving(self, _field: str | None = None) -> None:
        self.configuration_header.mark_saving()

    def mark_error(self, message: str) -> None:
        self.configuration_header.mark_error(message)

    def populate(self) -> None:
        self._populated = True
        expanded = expanded_policy_keys(self.tree)
        sort_column = self.tree.sortColumn()
        sort_order = self.bulk_header.sortIndicatorOrder()
        self.tree.setSortingEnabled(False)
        self.tree.blockSignals(True)
        self.tree.clear()
        self._catalog_keys = []
        self._toggles = {}
        self._tree_items = {}
        self._lazy_branches.reset()
        catalog = _load_catalog(self._config)
        if catalog is None:
            self._catalog_loaded = False
            self._show_catalog_onboarding()
            self.tree.blockSignals(False)
            return
        self._catalog_loaded = True
        self._show_catalog_content()
        recipes: dict[str, list[CatalogItem]] = defaultdict(list)
        for item in catalog.items.values():
            if item.recipe is not None:
                recipes[item.recipe.shop_id].append(item)
        if not recipes:
            self._show_empty("No shops or recipes have been discovered yet")
            self.tree.blockSignals(False)
            return
        for shop_id in sorted(recipes):
            shop = catalog.items.get(shop_id)
            shop_name = shop.display_name if shop else shop_id
            parent = PolicyTreeItem((shop_name, "", ""))
            parent.setIcon(0, self._icons.icon_for(shop))
            parent.setData(0, Qt.ItemDataRole.UserRole, ("shop", shop_id))
            parent.setSizeHint(0, QSize(0, 64))
            parent_font = parent.font(0)
            parent_font.setWeight(QFont.Weight.DemiBold)
            parent.setFont(0, parent_font)
            parent.setToolTip(0, shop.display_name if shop else shop_id)
            self.tree.addTopLevelItem(parent)
            set_policy_widget(
                self.tree,
                parent,
                1,
                policy_badge("Shop"),
                sort_value="Shop",
                search_text="Shop",
            )
            shop_toggle = PolicyCheckBox()
            configure_policy_toggle(shop_toggle)
            shop_toggle.setChecked(
                self._config.shop_overrides.get(shop_id, self._config.shop_default_enabled)
            )
            shop_toggle.setAccessibleName(f"{parent.text(0)}: enabled")
            shop_toggle.toggled.connect(
                lambda value, item_key=shop_id: self._set_item_enabled("shop", item_key, value)
            )
            set_policy_widget(
                self.tree,
                parent,
                2,
                policy_cell(shop_toggle),
                sort_value=shop_toggle.isChecked(),
            )
            self._catalog_keys.append(("shop", shop_id))
            self._toggles[("shop", shop_id)] = shop_toggle
            self._tree_items[("shop", shop_id)] = parent
            shop_recipes = tuple(sorted(recipes[shop_id], key=lambda item: item.display_name))
            for recipe in shop_recipes:
                self._catalog_keys.append(("recipe", recipe.game_id))
            self._lazy_branches.register(
                ("shop", shop_id),
                parent,
                partial(self._populate_shop_recipes, parent, shop_recipes),
                searchable_text=" ".join(
                    (
                        shop_name,
                        shop_id,
                        *(f"{recipe.display_name} {recipe.game_id}" for recipe in shop_recipes),
                    )
                ),
            )
        self._lazy_branches.restore_expanded(expanded)
        fit_policy_widget_column(self.tree, 1, minimum=124)
        self.tree.blockSignals(False)
        self.tree.setSortingEnabled(True)
        self.bulk_header.setSortIndicatorShown(False)
        self.tree.sortByColumn(sort_column, sort_order)
        self._sync_bulk_header()
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        self._lazy_branches.apply_filter(text)

    def _populate_shop_recipes(
        self,
        parent: QTreeWidgetItem,
        recipes: tuple[CatalogItem, ...],
    ) -> None:
        for recipe in recipes:
            child = PolicyTreeItem((recipe.display_name, "", ""))
            child.setIcon(0, self._icons.icon_for(recipe))
            child.setData(0, Qt.ItemDataRole.UserRole, ("recipe", recipe.game_id))
            child.setSizeHint(0, QSize(0, 64))
            child.setToolTip(0, recipe.display_name)
            parent.addChild(child)
            set_policy_widget(
                self.tree,
                child,
                1,
                policy_badge("Recipe"),
                sort_value="Recipe",
                search_text="Recipe",
            )
            recipe_toggle = PolicyCheckBox()
            configure_policy_toggle(recipe_toggle)
            recipe_toggle.setChecked(
                self._config.recipe_overrides.get(
                    recipe.game_id, self._config.recipe_default_enabled
                )
            )
            recipe_toggle.setAccessibleName(f"{recipe.display_name}: enabled")
            recipe_toggle.toggled.connect(
                lambda value, item_key=recipe.game_id: self._set_item_enabled(
                    "recipe", item_key, value
                )
            )
            set_policy_widget(
                self.tree,
                child,
                2,
                policy_cell(recipe_toggle),
                sort_value=recipe_toggle.isChecked(),
            )
            identity = ("recipe", recipe.game_id)
            self._toggles[identity] = recipe_toggle
            self._tree_items[identity] = child

    def _sync_controls(self) -> None:
        for identity, control in self._toggles.items():
            kind, key = identity
            value = (
                self._config.shop_overrides.get(key, self._config.shop_default_enabled)
                if kind == "shop"
                else self._config.recipe_overrides.get(key, self._config.recipe_default_enabled)
            )
            control.blockSignals(True)
            control.setChecked(value)
            control.blockSignals(False)
            item = self._tree_items[identity]
            set_policy_value(item, 2, value)
        self._sync_bulk_header()

    def _show_empty(self, message: str) -> None:
        item = QTreeWidgetItem((message, "", ""))
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setSizeHint(0, QSize(0, 52))
        self.tree.addTopLevelItem(item)
        item.setFirstColumnSpanned(True)
        self.bulk_header.set_state(2, Qt.CheckState.Unchecked, enabled=False)

    def _show_catalog_onboarding(self) -> None:
        self.catalog_onboarding.setVisible(True)
        self.toolbar.setVisible(False)
        self.tree.setVisible(False)

    def _show_catalog_content(self) -> None:
        self.catalog_onboarding.setVisible(False)
        self.toolbar.setVisible(True)
        self.tree.setVisible(True)

    def set_catalog_setup_busy(self, busy: bool) -> None:
        self.catalog_onboarding.set_busy(busy)

    def set_catalog_setup_status(self, message: str, *, error: bool = False) -> None:
        self.catalog_onboarding.set_status(message, error=error)

    def _set_item_enabled(self, kind: str, key: str, enabled: bool) -> None:
        field = "shop_overrides" if kind == "shop" else "recipe_overrides"
        default = (
            self._config.shop_default_enabled
            if kind == "shop"
            else self._config.recipe_default_enabled
        )
        values = dict(getattr(self._config, field))
        if enabled == default:
            values.pop(key, None)
        else:
            values[key] = enabled
        self._emit(**{field: values})
        self._sync_bulk_header()

    def _set_all(self, _column: int, value: bool) -> None:
        shop_overrides = dict(self._config.shop_overrides)
        recipe_overrides = dict(self._config.recipe_overrides)
        for kind, key in self._catalog_keys:
            overrides = shop_overrides if kind == "shop" else recipe_overrides
            default = (
                self._config.shop_default_enabled
                if kind == "shop"
                else self._config.recipe_default_enabled
            )
            if value == default:
                overrides.pop(key, None)
            else:
                overrides[key] = value
        self._emit(shop_overrides=shop_overrides, recipe_overrides=recipe_overrides)
        self.populate()

    def _sync_bulk_header(self) -> None:
        values = [
            (self._config.shop_overrides.get(key, self._config.shop_default_enabled))
            if kind == "shop"
            else self._config.recipe_overrides.get(key, self._config.recipe_default_enabled)
            for kind, key in self._catalog_keys
        ]
        state = aggregate_check_state(values)
        self.bulk_header.set_state(2, state, enabled=bool(values))
