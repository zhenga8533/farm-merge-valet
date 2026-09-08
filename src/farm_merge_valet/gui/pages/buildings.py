"""Building repair policies and live requirement state."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHeaderView, QTreeWidgetItem

from farm_merge_valet.automation.runtime import BuildingRepairState
from farm_merge_valet.catalog.models import CatalogItem, ItemCatalog
from farm_merge_valet.config import AppConfig
from farm_merge_valet.gui.components.catalog_icon_delegate import (
    STRUCTURE_ICON_SIZE,
    CatalogIconDelegate,
)
from farm_merge_valet.gui.components.catalog_onboarding import CatalogOnboarding
from farm_merge_valet.gui.components.configuration_header import ConfigurationHeader
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
    configure_policy_toggle,
    filter_policy_tree,
    policy_badge,
    policy_cell,
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
    ) -> None:
        super().__init__(
            "Building policies",
            "Choose which placed buildings may reserve materials for future repairs.",
        )
        self._config = config
        self._icons = icons or CatalogIconLoader(config.catalog_dir)
        self._catalog: ItemCatalog | None = load_gui_catalog(config)
        self._states: tuple[BuildingRepairState, ...] | None = None
        self._loaded_once = False
        self._toggles: dict[str, PolicyCheckBox] = {}
        self._items: dict[str, QTreeWidgetItem] = {}

        self.configuration_header = ConfigurationHeader("Reset building policies")
        self.configuration_header.reset_requested.connect(self.reset_requested)
        self.saved_label = self.configuration_header.status_label
        self.page_layout.addWidget(self.configuration_header)
        self.catalog_onboarding = CatalogOnboarding()
        self.catalog_onboarding.setup_requested.connect(self.catalog_setup_requested)
        self.page_layout.addWidget(self.catalog_onboarding, 1, Qt.AlignmentFlag.AlignCenter)
        self.loading_state = LoadingState(
            "Loading building policies…",
            "Reading live repair status and material requirements from the game.",
        )
        self.page_layout.addWidget(self.loading_state, 1, Qt.AlignmentFlag.AlignCenter)

        scaffold = create_policy_tree(
            header_labels=("Building / requirement", "Type", "Availability", "Repair", ""),
            bulk_labels={4: "Preserve"},
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
        if self._catalog is None:
            self._show_catalog_onboarding()
        else:
            self._show_loading()

    def apply_config(self, config: AppConfig, *, refresh: bool = True) -> None:
        catalog_changed = config.catalog_dir != self._config.catalog_dir
        sort_changed = (
            config.buildings_sort_column != self._config.buildings_sort_column
            or config.buildings_sort_descending != self._config.buildings_sort_descending
        )
        self._config = config
        if catalog_changed:
            self._icons.set_catalog_dir(config.catalog_dir)
            self._catalog = None
        if sort_changed:
            self._apply_sort_preference()
        if refresh and self._loaded_once:
            self._populate()
        self.configuration_header.mark_saved()

    def reload_catalog(self) -> None:
        self._catalog = load_gui_catalog(self._config)
        if self._loaded_once:
            self._populate()
        elif self._catalog is not None:
            self._show_loading()

    def reload_catalog_if_missing(self) -> None:
        if self._catalog is not None:
            return
        self._catalog = load_gui_catalog(self._config)
        if self._catalog is None:
            return
        if self._loaded_once:
            self._populate()
        else:
            self._show_loading()

    def set_catalog_setup_busy(self, busy: bool) -> None:
        self.catalog_onboarding.set_busy(busy)

    def set_catalog_setup_status(self, message: str, *, error: bool = False) -> None:
        self.catalog_onboarding.set_status(message, error=error)

    def set_building_repairs(self, states: object) -> None:
        if states is not None and not isinstance(states, tuple):
            return
        self._states = states
        self._loaded_once = True
        self._populate()

    def _populate(self) -> None:
        self._catalog = load_gui_catalog(self._config)
        if self._catalog is None:
            self._show_catalog_onboarding()
            return
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        self._toggles = {}
        self._items = {}
        if self._states is None:
            self._show_empty("Live building state is unavailable; open the game and try again")
        elif not self._states:
            self._show_empty("No buildings were discovered in the current game")
        else:
            for state in self._states:
                self._add_building(state)
        self.tree.setSortingEnabled(True)
        self._apply_sort_preference()
        self._filter(self.toolbar.search.text())
        self._show_catalog_content()

    def _add_building(self, state: BuildingRepairState) -> None:
        catalog_item = self._catalog_item(state.building_id)
        name = catalog_item.display_name if catalog_item else self._fallback_name(state.building_id)
        parent = PolicyTreeItem((name, "", "", "", ""))
        parent.setData(0, Qt.ItemDataRole.UserRole, ("building", state.building_id))
        parent.setIcon(0, self._icons.icon_for(catalog_item))
        parent.setToolTip(0, state.building_id)
        parent.setSizeHint(0, QSize(0, POLICY_MEDIA_ROW_HEIGHT))
        font = parent.font(0)
        font.setWeight(QFont.Weight.DemiBold)
        parent.setFont(0, font)
        self.tree.addTopLevelItem(parent)
        building_type = "Shop" if state.workshop else "Building"
        availability = "On board" if state.placed else "Not on board"
        repair = self._repair_status(state)
        self._badge(parent, 1, building_type)
        self._badge(parent, 2, availability)
        self._badge(parent, 3, repair)
        toggle = PolicyCheckBox()
        configure_policy_toggle(toggle)
        toggle.setChecked(self._config.building_repair_enabled(state.building_id))
        toggle.setAccessibleName(f"{name}: preserve repair materials")
        toggle.toggled.connect(
            lambda enabled, building_id=state.building_id: self._set_enabled(building_id, enabled)
        )
        set_policy_widget(self.tree, parent, 4, policy_cell(toggle), sort_value=toggle.isChecked())
        self._toggles[state.building_id] = toggle
        self._items[state.building_id] = parent
        for requirement in state.requirements:
            self._add_requirement(
                parent,
                requirement.blueprint_id,
                requirement.available,
                requirement.amount,
            )
        parent.setExpanded(state.placed and not state.active)

    def _add_requirement(
        self, parent: QTreeWidgetItem, blueprint_id: str, available: int, amount: int
    ) -> None:
        catalog_item = self._catalog_item(blueprint_id)
        name = catalog_item.display_name if catalog_item else self._fallback_name(blueprint_id)
        tier = f"Tier {catalog_item.tier}" if catalog_item and catalog_item.tier else "Material"
        child = PolicyTreeItem((name, "", "", "", ""))
        child.setData(0, Qt.ItemDataRole.UserRole, blueprint_id)
        child.setIcon(0, self._icons.icon_for(catalog_item))
        child.setToolTip(0, blueprint_id)
        child.setSizeHint(0, QSize(0, POLICY_MEDIA_ROW_HEIGHT))
        parent.addChild(child)
        self._badge(child, 1, tier)
        self._badge(child, 2, f"{available} / {amount}")
        self._badge(child, 3, "Ready" if available >= amount else f"Missing {amount - available}")

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
        if enabled == self._config.building_repair_default_enabled:
            overrides.pop(building_id, None)
        else:
            overrides[building_id] = enabled
        self._emit(building_repair_overrides=overrides)
        self._sync_bulk_header()

    def _set_all(self, _column: int, enabled: bool) -> None:
        overrides = dict(self._config.building_repair_overrides)
        for building_id in self._visible_building_ids():
            if enabled == self._config.building_repair_default_enabled:
                overrides.pop(building_id, None)
            else:
                overrides[building_id] = enabled
        self._emit(building_repair_overrides=overrides)
        self._populate()

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
        if state.upgrading:
            return "Repairing"
        if not state.placed or not state.requirements:
            return "Unavailable"
        if all(requirement.missing == 0 for requirement in state.requirements):
            return "Ready"
        return f"Needs {sum(requirement.missing for requirement in state.requirements)}"

    @staticmethod
    def _fallback_name(game_id: str) -> str:
        return game_id.replace("_", " ").strip().title()
