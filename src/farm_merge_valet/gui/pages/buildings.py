"""Building repair policies and live requirement state."""

from __future__ import annotations

from collections.abc import Iterator

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHeaderView, QTreeWidgetItem

from farm_merge_valet.automation.runtime import BuildingRepairState
from farm_merge_valet.catalog.models import CatalogItem, ItemCatalog
from farm_merge_valet.config import AppConfig
from farm_merge_valet.gui.components.catalog_icon_delegate import (
    STRUCTURE_ICON_SIZE,
    CatalogIconDelegate,
    CatalogRowRole,
    set_catalog_row_icon,
)
from farm_merge_valet.gui.components.catalog_onboarding import CatalogOnboarding
from farm_merge_valet.gui.components.configuration_header import ConfigurationHeader
from farm_merge_valet.gui.components.incremental_work import IncrementalPopulation
from farm_merge_valet.gui.components.loading_state import LoadingState
from farm_merge_valet.gui.components.metrics import (
    POLICY_COMPACT_ROW_HEIGHT,
    POLICY_MEDIA_ROW_HEIGHT,
)
from farm_merge_valet.gui.components.policy_tree import create_policy_tree
from farm_merge_valet.gui.components.policy_view import (
    PolicyCheckBox,
    PolicyTreeItem,
    aggregate_check_state,
    apply_policy_override,
    filter_policy_tree,
    policy_badge,
    policy_cell,
    policy_checkbox,
    set_policy_value,
    set_policy_widget,
)
from farm_merge_valet.gui.pages.base import AppPage, ConfigEdit
from farm_merge_valet.gui.services.assets import CatalogIconLoader
from farm_merge_valet.gui.services.catalog import load_gui_catalog

_SORT_COLUMNS = {"building": 0, "type": 1, "availability": 2, "repair": 3, "enabled": 4}


class BuildingsPage(AppPage):
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
            "Building policies",
            "Choose which placed buildings may be repaired automatically.",
        )
        self._config = config
        self._icons = icons or CatalogIconLoader(config.catalog_dir)
        self._catalog: ItemCatalog | None = None
        self._populated = False
        self._population = IncrementalPopulation(self)
        self._states: tuple[BuildingRepairState, ...] | None = None
        self._toggles: dict[str, PolicyCheckBox] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        self._groups: dict[str, QTreeWidgetItem] = {}
        self._group_toggles: dict[str, PolicyCheckBox] = {}
        self._group_building_ids: dict[str, list[str]] = {}

        self.configuration_header = ConfigurationHeader("Reset building policies")
        self.configuration_header.reset_requested.connect(self.reset_requested)
        self.saved_label = self.configuration_header.status_label
        self.page_layout.addWidget(self.configuration_header)
        self.catalog_onboarding = CatalogOnboarding()
        self.catalog_onboarding.setup_requested.connect(self.catalog_setup_requested)
        self.page_layout.addWidget(self.catalog_onboarding, 1, Qt.AlignmentFlag.AlignCenter)
        self.loading_state = LoadingState(
            "Loading buildings…",
            "Preparing locally cached buildings, repair requirements, and controls.",
        )
        self.loading_state.setVisible(False)
        self.page_layout.addWidget(self.loading_state, 1, Qt.AlignmentFlag.AlignCenter)
        scaffold = create_policy_tree(
            header_labels=("Building / requirement", "Type", "Availability", "Repair", ""),
            bulk_labels={4: "Repair"},
            accessible_name="Building repair policies",
            search_placeholder="Search buildings and requirements…",
            search_accessible_name="Search buildings and requirements",
            scope="building groups",
            icon_size=STRUCTURE_ICON_SIZE,
        )
        self.tree = scaffold.tree
        self.tree.setItemDelegate(CatalogIconDelegate(self.tree))
        self.bulk_header = scaffold.header
        self.toolbar = scaffold.toolbar
        self.bulk_header.toggled.connect(self._set_all)
        self.toolbar.filter_requested.connect(self._filter)
        self.bulk_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            self.bulk_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.page_layout.addWidget(self.toolbar)
        self.page_layout.addWidget(self.tree, 1)
        self._apply_sort_preference()
        self.bulk_header.sortIndicatorChanged.connect(self._sort_changed)
        if populate_immediately:
            self.populate()

    def ensure_populated(self, *, deferred: bool = False) -> None:
        self._population.ensure(
            populated=self._populated,
            deferred=deferred,
            steps=self._populate_steps,
            show_loading=self._show_loading,
        )

    def apply_config(self, config: AppConfig, *, refresh: bool = True) -> None:
        catalog_changed = config.catalog_dir != self._config.catalog_dir
        sort_changed = (
            config.buildings_sort_column != self._config.buildings_sort_column
            or config.buildings_sort_descending != self._config.buildings_sort_descending
        )
        self._config = config
        self._icons.set_catalog_dir(config.catalog_dir)
        if sort_changed:
            self._apply_sort_preference()
        if refresh and self._populated:
            if catalog_changed or self._catalog is None:
                self.populate()
            else:
                self._sync_controls()
        self.configuration_header.mark_saved()

    def reload_catalog(self) -> None:
        if self._populated:
            self.populate()

    def reload_catalog_if_missing(self) -> None:
        if not self._populated or self._catalog is not None:
            return
        self.populate()

    def set_catalog_setup_busy(self, busy: bool) -> None:
        self.catalog_onboarding.set_busy(busy)

    def set_catalog_setup_status(self, message: str, *, error: bool = False) -> None:
        self.catalog_onboarding.set_status(message, error=error)

    def set_building_repairs(self, states: object) -> None:
        if states is not None and not isinstance(states, tuple):
            return
        self._states = states
        if self._populated:
            self.populate()

    def populate(self) -> None:
        self._population.run_now(self._populate_steps)

    def _populate_steps(self) -> Iterator[None]:
        self._populated = True
        self._catalog = load_gui_catalog(self._config)
        if self._catalog is None:
            self._show_catalog_onboarding()
            return
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        self._toggles = {}
        self._items = {}
        self._groups = {}
        self._group_toggles = {}
        self._group_building_ids = {}
        live_states = self._states or ()
        states_by_catalog_id = {
            catalog_item.game_id: state
            for state in live_states
            if (catalog_item := self._catalog_item(state.building_id)) is not None
        }
        catalog_buildings = self._catalog_buildings()
        for catalog_item in catalog_buildings:
            self._add_building(
                self._building_id(catalog_item),
                catalog_item,
                states_by_catalog_id.get(catalog_item.game_id),
            )
            yield
        for state in live_states:
            if self._catalog_item(state.building_id) is None:
                self._add_building(state.building_id, None, state)
                yield
        if not catalog_buildings and not live_states:
            self._show_empty("No buildings were discovered in the cached catalog")
        self._finalize_groups()
        self.tree.setSortingEnabled(True)
        self._apply_sort_preference()
        self._filter(self.toolbar.search.text())
        self._show_catalog_content()

    def _add_building(
        self,
        building_id: str,
        catalog_item: CatalogItem | None,
        state: BuildingRepairState | None,
    ) -> None:
        if state is not None:
            building_id = state.building_id
        name = catalog_item.display_name if catalog_item else self._fallback_name(building_id)
        is_workshop = (
            state.workshop
            if state is not None
            else catalog_item is not None and catalog_item.category == "shops"
        )
        group = self._building_group(building_id, is_workshop, catalog_item)
        parent = PolicyTreeItem((name, "", "", "", ""))
        parent.setData(0, Qt.ItemDataRole.UserRole, ("building", building_id))
        set_catalog_row_icon(parent, self._icons.icon_for(catalog_item), CatalogRowRole.STRUCTURE)
        parent.setToolTip(0, building_id)
        parent.setSizeHint(0, QSize(0, POLICY_MEDIA_ROW_HEIGHT))
        font = parent.font(0)
        font.setWeight(QFont.Weight.DemiBold)
        parent.setFont(0, font)
        group.addChild(parent)
        building_type = "Shop" if is_workshop else "Building"
        availability = (
            "On board"
            if state is not None and state.placed
            else "Not on board"
            if state is not None
            else "Live status unavailable"
        )
        repair = self._repair_status(state) if state is not None else "Unavailable"
        self._badge(parent, 1, building_type)
        self._badge(parent, 2, availability)
        self._badge(parent, 3, repair)
        toggle = policy_checkbox(
            self._config.building_repair_enabled(building_id),
            f"{name}: preserve repair materials",
        )
        toggle.toggled.connect(
            lambda enabled, building_id=building_id: self._set_enabled(building_id, enabled)
        )
        set_policy_widget(self.tree, parent, 4, policy_cell(toggle), sort_value=toggle.isChecked())
        self._toggles[building_id] = toggle
        self._items[building_id] = parent
        for requirement in state.requirements if state is not None else ():
            self._add_requirement(
                parent,
                requirement.blueprint_id,
                requirement.available,
                requirement.amount,
            )
        parent.setExpanded(state is not None and state.placed and not state.active)

    def _building_group(
        self,
        building_id: str,
        is_workshop: bool,
        catalog_item: CatalogItem | None,
    ) -> QTreeWidgetItem:
        if is_workshop:
            label = "Workshops"
        elif catalog_item is not None and (
            "event" in catalog_item.traits
            or catalog_item.display_name.casefold().startswith("decorative ")
        ):
            label = "Event Buildings"
        elif (
            catalog_item is not None and "decorative" in catalog_item.traits
        ) or building_id.startswith("decorative_"):
            label = "Decorative Buildings"
        else:
            label = "Structures"
        group = self._groups.get(label)
        if group is None:
            group = PolicyTreeItem((label, "", "", "", ""))
            group.setData(0, Qt.ItemDataRole.UserRole, ("building_group", label.casefold()))
            set_catalog_row_icon(group, None, CatalogRowRole.GROUP)
            group.setSizeHint(0, QSize(0, POLICY_MEDIA_ROW_HEIGHT))
            font = group.font(0)
            font.setWeight(QFont.Weight.DemiBold)
            group.setFont(0, font)
            self.tree.addTopLevelItem(group)
            group.setExpanded(True)
            self._badge(group, 1, "Group")
            self._groups[label] = group
        self._group_building_ids.setdefault(label, []).append(building_id)
        return group

    def _finalize_groups(self) -> None:
        """Add each group's aggregate repair-preservation toggle once every
        building has been assigned to it, matching the group/family toggles
        Marketplace already shows for its own multi-level tree."""
        for label, group_item in self._groups.items():
            building_ids = self._group_building_ids.get(label, [])
            state = aggregate_check_state(
                [self._config.building_repair_enabled(building_id) for building_id in building_ids]
            )
            toggle = policy_checkbox(
                state, f"{label}: preserve repair materials for all buildings", tristate=True
            )
            toggle.clicked.connect(
                lambda checked, ids=tuple(building_ids): self._set_group(ids, checked)
            )
            set_policy_widget(self.tree, group_item, 4, policy_cell(toggle), sort_value=state.value)
            self._group_toggles[label] = toggle

    def _catalog_buildings(self) -> tuple[CatalogItem, ...]:
        if self._catalog is None:
            return ()
        return tuple(
            sorted(
                (
                    item
                    for item in self._catalog.items.values()
                    if item.tier is None
                    and (item.category == "shops" or "building" in item.capabilities)
                ),
                key=lambda item: (item.display_name.casefold(), item.game_id),
            )
        )

    @staticmethod
    def _building_id(catalog_item: CatalogItem) -> str:
        if catalog_item.game_id in {"gazebo", "greenhouse"}:
            return f"{catalog_item.game_id}_building"
        return catalog_item.game_id

    def _add_requirement(
        self, parent: QTreeWidgetItem, blueprint_id: str, available: int, amount: int
    ) -> None:
        catalog_item = self._catalog_item(blueprint_id)
        base_name = catalog_item.display_name if catalog_item else self._fallback_name(blueprint_id)
        tier = catalog_item.tier if catalog_item is not None else None
        name = f"{base_name} (T{tier})" if tier is not None else base_name
        child = PolicyTreeItem((name, "", "", "", ""))
        child.setData(0, Qt.ItemDataRole.UserRole, blueprint_id)
        child.setData(
            0,
            Qt.ItemDataRole.AccessibleTextRole,
            f"{base_name}, Tier {tier}" if tier is not None else base_name,
        )
        set_catalog_row_icon(child, self._icons.icon_for(catalog_item), CatalogRowRole.ITEM)
        child.setToolTip(
            0,
            f"{base_name}, Tier {tier}\n{blueprint_id}" if tier is not None else blueprint_id,
        )
        child.setSizeHint(0, QSize(0, POLICY_MEDIA_ROW_HEIGHT))
        parent.addChild(child)
        self._badge(child, 2, f"{available} / {amount}")

    def _catalog_item(self, game_id: str) -> CatalogItem | None:
        if self._catalog is None:
            return None
        exact = self._catalog.items.get(game_id)
        if exact is not None:
            return exact
        if game_id.endswith("_building"):
            return self._catalog.items.get(game_id.removesuffix("_building"))
        return None

    def _badge(self, item: QTreeWidgetItem, column: int, text: str) -> None:
        set_policy_widget(
            self.tree,
            item,
            column,
            policy_badge(text),
            sort_value=text,
            search_text=text,
        )

    def _set_enabled(self, building_id: str, enabled: bool) -> None:
        overrides = dict(self._config.building_repair_overrides)
        apply_policy_override(
            overrides, building_id, enabled, self._config.building_repair_default_enabled
        )
        self._emit(building_repair_overrides=overrides)
        self._sync_bulk_header()
        self._sync_group_checkboxes()

    def _set_group(self, building_ids: tuple[str, ...], enabled: bool) -> None:
        overrides = dict(self._config.building_repair_overrides)
        for building_id in building_ids:
            apply_policy_override(
                overrides, building_id, enabled, self._config.building_repair_default_enabled
            )
        self._emit(building_repair_overrides=overrides)
        self._sync_controls()

    def _sync_controls(self) -> None:
        """Refresh every toggle and badge from the current config without
        rebuilding the tree, matching Items'/Marketplace's/Shops' sync-in-place
        path for a config-only change (e.g. an override made elsewhere)."""
        for building_id, toggle in self._toggles.items():
            enabled = self._config.building_repair_enabled(building_id)
            toggle.blockSignals(True)
            toggle.setChecked(enabled)
            toggle.blockSignals(False)
            set_policy_value(self._items[building_id], 4, enabled)
        self._sync_group_checkboxes()
        self._sync_bulk_header()

    def _sync_group_checkboxes(self) -> None:
        for label, toggle in self._group_toggles.items():
            building_ids = self._group_building_ids.get(label, [])
            state = aggregate_check_state(
                [self._config.building_repair_enabled(building_id) for building_id in building_ids]
            )
            toggle.blockSignals(True)
            toggle.setCheckState(state)
            toggle.blockSignals(False)
            set_policy_value(self._groups[label], 4, state.value)

    def _set_all(self, _column: int, enabled: bool) -> None:
        overrides = dict(self._config.building_repair_overrides)
        for building_id in self._visible_building_ids():
            apply_policy_override(
                overrides, building_id, enabled, self._config.building_repair_default_enabled
            )
        self._emit(building_repair_overrides=overrides)
        self.populate()

    def _visible_building_ids(self) -> list[str]:
        return [building_id for building_id, item in self._items.items() if not item.isHidden()]

    def _sync_bulk_header(self) -> None:
        values = [
            self._config.building_repair_enabled(building_id)
            for building_id in self._visible_building_ids()
        ]
        self.bulk_header.set_state(4, aggregate_check_state(values), enabled=bool(values))

    def _filter(self, text: str) -> None:
        filter_policy_tree(self.tree, text)
        self._sync_bulk_header()

    def _apply_sort_preference(self) -> None:
        column = _SORT_COLUMNS[self._config.buildings_sort_column]
        order = (
            Qt.SortOrder.DescendingOrder
            if self._config.buildings_sort_descending
            else Qt.SortOrder.AscendingOrder
        )
        self.bulk_header.setSortIndicator(column, order)
        if self.tree.isSortingEnabled():
            self.tree.sortByColumn(column, order)

    def _sort_changed(self, column: int, order: Qt.SortOrder) -> None:
        key = next(name for name, value in _SORT_COLUMNS.items() if value == column)
        descending = order is Qt.SortOrder.DescendingOrder
        if key != self._config.buildings_sort_column or (
            descending != self._config.buildings_sort_descending
        ):
            self._emit(buildings_sort_column=key, buildings_sort_descending=descending)

    def _emit(self, **changes: object) -> None:
        self._config = AppConfig.model_validate(
            self._config.model_copy(update=changes).model_dump()
        )
        self.config_edited.emit(ConfigEdit(changes, source="buildings"))

    def mark_saving(self, _field: str | None = None) -> None:
        self.configuration_header.mark_saving()

    def mark_error(self, message: str) -> None:
        self.configuration_header.mark_error(message)

    def _show_empty(self, message: str) -> None:
        item = QTreeWidgetItem((message, "", "", "", ""))
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setSizeHint(0, QSize(0, POLICY_COMPACT_ROW_HEIGHT))
        item.setFirstColumnSpanned(True)
        self.tree.addTopLevelItem(item)
        self.bulk_header.set_state(4, Qt.CheckState.Unchecked, enabled=False)

    def _show_catalog_onboarding(self) -> None:
        self.loading_state.setVisible(False)
        self.catalog_onboarding.setVisible(True)
        self.toolbar.setVisible(False)
        self.tree.setVisible(False)

    def _show_catalog_content(self) -> None:
        self.loading_state.setVisible(False)
        self.catalog_onboarding.setVisible(False)
        self.toolbar.setVisible(True)
        self.tree.setVisible(True)

    def _show_loading(self) -> None:
        self.catalog_onboarding.setVisible(False)
        self.toolbar.setVisible(False)
        self.tree.setVisible(False)
        self.loading_state.setVisible(True)

    @staticmethod
    def _repair_status(state: BuildingRepairState) -> str:
        if state.active:
            return "Repaired"
        if not state.placed or not state.requirements:
            return "Unavailable"
        if all(requirement.missing == 0 for requirement in state.requirements):
            return "Ready"
        return f"Needs {sum(requirement.missing for requirement in state.requirements)}"

    @staticmethod
    def _fallback_name(game_id: str) -> str:
        return game_id.replace("_", " ").strip().title()
