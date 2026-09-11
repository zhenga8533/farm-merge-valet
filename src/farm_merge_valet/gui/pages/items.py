"""Focused page widgets for the desktop application shell."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from functools import partial

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHeaderView,
    QTreeWidgetItem,
)

from farm_merge_valet.catalog.models import (
    CatalogItem,
    TileInteractionMode,
)
from farm_merge_valet.config import AppConfig, ItemPolicyOverride
from farm_merge_valet.core.items import item_tier_policy_key
from farm_merge_valet.core.upgrade_progress import (
    UpgradeProgress,
    UpgradeTargetProgress,
    UpgradeTierState,
)
from farm_merge_valet.gui.components.catalog_icon_delegate import (
    CatalogRowRole,
    set_catalog_row_icon,
)
from farm_merge_valet.gui.components.catalog_onboarding import CatalogOnboarding
from farm_merge_valet.gui.components.configuration_header import ConfigurationHeader
from farm_merge_valet.gui.components.incremental_work import IncrementalPopulation
from farm_merge_valet.gui.components.loading_state import LoadingState
from farm_merge_valet.gui.components.metrics import (
    POLICY_COMPACT_BADGE_COLUMN_WIDTH,
    POLICY_COMPACT_ROW_HEIGHT,
    POLICY_ICON_SIZE,
)
from farm_merge_valet.gui.components.policy_tree import create_policy_tree
from farm_merge_valet.gui.components.policy_view import (
    POLICY_SEARCH_ROLE,
    LazyPolicyBranches,
    PolicyCheckBox,
    PolicyTreeItem,
    aggregate_check_state,
    configure_policy_toggle,
    expanded_policy_keys,
    fit_policy_widget_column,
    policy_badges,
    policy_cell,
    policy_status,
    policy_unavailable,
    set_policy_value,
    set_policy_widget,
)
from farm_merge_valet.gui.pages.base import AppPage, ConfigEdit
from farm_merge_valet.gui.services.assets import CatalogIconLoader
from farm_merge_valet.gui.services.catalog import load_gui_catalog

_ITEM_SORT_COLUMNS = {
    "item": 0,
    "category": 1,
    "enabled": 2,
    "merge": 3,
    "merge_five": 4,
    "interact": 5,
    "remove": 6,
}


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

        scaffold = create_policy_tree(
            header_labels=("Item / tier", "Category", "", "", "", "", ""),
            bulk_labels={
                2: "Enabled",
                3: "Merge",
                4: "Merge 5",
                5: "Interact",
                6: "Remove",
            },
            accessible_name="Item automation policies",
            search_placeholder="Search item families and tiers\u2026",
            search_accessible_name="Search item families and tiers",
            scope="item groups",
            icon_size=POLICY_ICON_SIZE,
            minimum_section_size=64,
        )
        self.table = scaffold.tree
        self.bulk_header = scaffold.header
        self.bulk_header.toggled.connect(self._set_all)
        self.bulk_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.bulk_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.bulk_header.resizeSection(1, 108)
        for column in range(2, 7):
            self.bulk_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._apply_sort_preference()

        self.toolbar = scaffold.toolbar
        self.search = self.toolbar.search
        self.expansion_controls = self.toolbar.expansion_controls
        self.page_layout.addWidget(self.toolbar)
        self.page_layout.addWidget(self.table, 1)
        self.toolbar.filter_requested.connect(self._filter)
        self._upgrade_progress: UpgradeProgress | None = None
        self._upgrade_targets: dict[str, UpgradeTargetProgress] = {}
        self._catalog_loaded = False
        self._populated = False
        self._population = IncrementalPopulation(self)
        self._row_definitions: list[_ItemPolicyRow] = []
        self._search_text_by_policy_key: dict[str, str] = {}
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
        self._population.ensure(
            populated=self._populated,
            deferred=deferred,
            steps=self._populate_steps,
            show_loading=self._show_loading,
        )

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
        self._population.run_now(self._populate_steps)

    def _populate_steps(self) -> Iterator[None]:
        self._populated = True
        expanded_keys = expanded_policy_keys(self.table)
        sort_column = self.table.sortColumn()
        sort_order = self.bulk_header.sortIndicatorOrder()
        self.table.setSortingEnabled(False)
        self.table.clear()
        self._row_definitions = []
        self._search_text_by_policy_key = {}
        self._policy_controls = []
        self._lazy_branches.reset()
        self._upgrade_targets = {}
        catalog = load_gui_catalog(self._config)
        if catalog is None:
            self._catalog_loaded = False
            self._show_catalog_onboarding()
            return
        self._catalog_loaded = True
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
            self._show_catalog_content()
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
            for definition in definitions:
                self._search_text_by_policy_key[definition.policy_key] = " ".join(
                    (
                        family_name,
                        representative.category.replace("_", " ").title(),
                        family_key,
                        definition.item.game_id,
                        " ".join(sorted(definition.item.traits)),
                        f"Tier {definition.item.tier} {definition.item.display_name}",
                    )
                ).casefold()
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
                yield
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
            yield
        self._lazy_branches.restore_expanded(expanded_keys)
        fit_policy_widget_column(
            self.table,
            1,
            minimum=POLICY_COMPACT_BADGE_COLUMN_WIDTH,
        )
        self.table.setSortingEnabled(True)
        self.bulk_header.setSortIndicatorShown(False)
        self.table.sortByColumn(sort_column, sort_order)
        self._sync_bulk_header()
        self._filter(self.search.text())
        self._show_catalog_content()

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
            child.setData(
                0,
                POLICY_SEARCH_ROLE,
                self._search_text_by_policy_key[definition.policy_key],
            )
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
            TileInteractionMode.OPEN_REQUIREMENT,
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
                TileInteractionMode.OPEN_REQUIREMENT,
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
        set_catalog_row_icon(item, icons.icon_for(catalog_item), CatalogRowRole.ITEM)
        item.setSizeHint(0, QSize(0, POLICY_COMPACT_ROW_HEIGHT))
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
        if item.tile_interaction_mode is TileInteractionMode.OPEN_REQUIREMENT:
            return "open"
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
        item.setSizeHint(0, QSize(0, POLICY_COMPACT_ROW_HEIGHT))
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
        self._sync_bulk_header()

    def _bulk_definitions(self) -> list[_ItemPolicyRow]:
        if not self.search.text().strip():
            return self._row_definitions
        query = self.search.text().casefold().strip()
        return [
            definition
            for definition in self._row_definitions
            if query in self._search_text_by_policy_key[definition.policy_key]
        ]

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
        for definition in self._bulk_definitions():
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
        definitions = self._bulk_definitions()
        for column, field in self._policy_fields().items():
            values = [
                getattr(self._config.item_policy(definition.policy_key), field)
                for definition in definitions
                if definition.supports(field)
            ]
            state = aggregate_check_state(values)
            self.bulk_header.set_state(column, state, enabled=bool(values))
