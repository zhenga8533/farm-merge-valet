"""Focused page widgets for the desktop application shell."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from functools import partial

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from farm_merge_valet.automation.runtime import BuildingRepairState
from farm_merge_valet.catalog.models import (
    CatalogItem,
    ItemCatalog,
)
from farm_merge_valet.config import AppConfig
from farm_merge_valet.gui.components.catalog_icon_delegate import (
    STRUCTURE_ICON_SIZE,
    CatalogIconDelegate,
    CatalogRowRole,
    set_catalog_row_icon,
)
from farm_merge_valet.gui.components.configuration_header import ConfigurationHeader
from farm_merge_valet.gui.components.incremental_work import IncrementalPopulation
from farm_merge_valet.gui.components.input_controls import FocusAwareSpinBox
from farm_merge_valet.gui.components.metrics import (
    POLICY_COMPACT_ROW_HEIGHT,
    POLICY_MEDIA_BADGE_COLUMN_WIDTH,
    POLICY_MEDIA_ROW_HEIGHT,
)
from farm_merge_valet.gui.components.policy_tree import create_policy_tree
from farm_merge_valet.gui.components.policy_view import (
    LazyPolicyBranches,
    PolicyCheckBox,
    PolicyTreeItem,
    aggregate_check_state,
    apply_policy_override,
    expanded_policy_keys,
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

_SHOP_SORT_COLUMNS = {"item": 0, "type": 1, "repair": 2, "enabled": 3}


class IngredientReservesDialog(QDialog):
    def __init__(self, config: AppConfig, catalog: ItemCatalog, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Shop ingredient reserves")
        self.setMinimumWidth(520)
        self._existing_reserves = dict(config.shop_ingredient_reserves)
        layout = QVBoxLayout(self)
        description = QLabel(
            "Keep at least this many of each ingredient after starting a shop order."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        self.default_control = self._spin_box(config.shop_ingredient_reserve_default)
        form.addRow("Default reserve", self.default_control)
        self.controls: dict[str, tuple[QCheckBox, FocusAwareSpinBox]] = {}
        ingredient_ids = sorted(
            {
                ingredient.item_id
                for item in catalog.items.values()
                if item.recipe is not None
                for ingredient in item.recipe.ingredients
            },
            key=lambda item_id: (
                catalog.items[item_id].display_name.casefold()
                if item_id in catalog.items
                else item_id.casefold()
            ),
        )
        for item_id in ingredient_ids:
            item = catalog.items.get(item_id)
            label = item.display_name if item is not None else item_id
            use_default = QCheckBox("Use default")
            use_default.setAccessibleName(f"{label}: use default reserve")
            reserve = self._spin_box(
                config.shop_ingredient_reserves.get(item_id, config.shop_ingredient_reserve_default)
            )
            reserve.setAccessibleName(f"{label}: ingredient reserve")
            inherited = item_id not in config.shop_ingredient_reserves
            use_default.setChecked(inherited)
            reserve.setEnabled(not inherited)
            use_default.toggled.connect(reserve.setDisabled)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(use_default)
            row_layout.addWidget(reserve)
            form.addRow(label, row)
            self.controls[item_id] = (use_default, reserve)
        if not ingredient_ids:
            form.addRow(QLabel("No recipe ingredients have been discovered."))
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _spin_box(value: int) -> FocusAwareSpinBox:
        control = FocusAwareSpinBox()
        control.setRange(0, 1_000_000_000)
        control.setValue(value)
        control.setKeyboardTracking(False)
        return control

    def values(self) -> tuple[int, dict[str, int]]:
        reserves = {
            item_id: value
            for item_id, value in self._existing_reserves.items()
            if item_id not in self.controls
        }
        reserves.update(
            {
                item_id: reserve.value()
                for item_id, (use_default, reserve) in self.controls.items()
                if not use_default.isChecked()
            }
        )
        return self.default_control.value(), reserves


class ShopsPage(CatalogTreePage):
    config_edited = Signal(object)
    reset_requested = Signal()

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
        self.reserves_button = self.configuration_header.add_action("Ingredient reserves")
        self.reserves_button.clicked.connect(self._edit_ingredient_reserves)
        self.page_layout.addWidget(self.configuration_header)
        self._init_catalog_scaffold(
            "Loading shops\u2026",
            "Preparing locally cached shops, recipes, and controls.",
        )
        scaffold = create_policy_tree(
            header_labels=("Shop / recipe", "Type", "Repair", ""),
            bulk_labels={3: "Enabled"},
            accessible_name="Shop and recipe policies",
            search_placeholder="Search shops and recipes\u2026",
            search_accessible_name="Search shops and recipes",
            scope="shop and recipe groups",
            icon_size=STRUCTURE_ICON_SIZE,
            minimum_section_size=96,
        )
        self.tree = scaffold.tree
        self.bulk_header = scaffold.header
        self.bulk_header.toggled.connect(self._set_all)
        self.tree.setItemDelegate(CatalogIconDelegate(self.tree))
        self.bulk_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.bulk_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.bulk_header.resizeSection(1, 124)
        self.bulk_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.bulk_header.resizeSection(2, 150)
        self.bulk_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._building_repairs: dict[str, BuildingRepairState] = {}
        self._apply_sort_preference()

        self.toolbar = scaffold.toolbar
        self.search = self.toolbar.search
        self.expansion_controls = self.toolbar.expansion_controls
        self.page_layout.addWidget(self.toolbar)
        self.page_layout.addWidget(self.tree, 1)
        self.toolbar.filter_requested.connect(self._filter)
        self._catalog_loaded = False
        self._population = IncrementalPopulation(self)
        self._catalog_keys: list[tuple[str, str]] = []
        self._search_text_by_identity: dict[tuple[str, str], str] = {}
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
        self._population.run_now(self._populate_steps)

    def _populate_steps(self) -> Iterator[None]:
        self._populated = True
        expanded = expanded_policy_keys(self.tree)
        sort_column = self.tree.sortColumn()
        sort_order = self.bulk_header.sortIndicatorOrder()
        self.tree.setSortingEnabled(False)
        self.tree.blockSignals(True)
        self.tree.clear()
        self._catalog_keys = []
        self._search_text_by_identity = {}
        self._toggles = {}
        self._tree_items = {}
        self._lazy_branches.reset()
        catalog = load_gui_catalog(self._config)
        if catalog is None:
            self._catalog_loaded = False
            self._show_catalog_onboarding()
            self.tree.blockSignals(False)
            return
        self._catalog_loaded = True
        recipes: dict[str, list[CatalogItem]] = defaultdict(list)
        for item in catalog.items.values():
            if item.recipe is not None:
                recipes[item.recipe.shop_id].append(item)
        if not recipes:
            self._show_empty("No shops or recipes have been discovered yet")
            self.tree.blockSignals(False)
            self._show_catalog_content()
            return
        for shop_id in sorted(recipes):
            shop = catalog.items.get(shop_id)
            shop_name = shop.display_name if shop else shop_id
            parent = PolicyTreeItem((shop_name, "", "", ""))
            set_catalog_row_icon(parent, self._icons.icon_for(shop), CatalogRowRole.STRUCTURE)
            parent.setData(0, Qt.ItemDataRole.UserRole, ("shop", shop_id))
            parent.setSizeHint(0, QSize(0, POLICY_MEDIA_ROW_HEIGHT))
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
            self._set_repair_status(parent, shop_id)
            shop_toggle = policy_checkbox(
                self._config.shop_overrides.get(shop_id, self._config.shop_default_enabled),
                f"{parent.text(0)}: enabled",
            )
            shop_toggle.toggled.connect(
                lambda value, item_key=shop_id: self._set_item_enabled("shop", item_key, value)
            )
            set_policy_widget(
                self.tree,
                parent,
                3,
                policy_cell(shop_toggle),
                sort_value=shop_toggle.isChecked(),
            )
            self._catalog_keys.append(("shop", shop_id))
            shop_search_text = f"{shop_name} {shop_id} Shop"
            self._search_text_by_identity[("shop", shop_id)] = shop_search_text.casefold()
            self._toggles[("shop", shop_id)] = shop_toggle
            self._tree_items[("shop", shop_id)] = parent
            shop_recipes = tuple(sorted(recipes[shop_id], key=lambda item: item.display_name))
            for recipe in shop_recipes:
                self._catalog_keys.append(("recipe", recipe.game_id))
                self._search_text_by_identity[("recipe", recipe.game_id)] = " ".join(
                    (shop_search_text, recipe.display_name, recipe.game_id, "Recipe")
                ).casefold()
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
            yield
        self._lazy_branches.restore_expanded(expanded)
        fit_policy_widget_column(
            self.tree,
            1,
            minimum=POLICY_MEDIA_BADGE_COLUMN_WIDTH,
        )
        self.tree.blockSignals(False)
        self.tree.setSortingEnabled(True)
        self.bulk_header.setSortIndicatorShown(False)
        self.tree.sortByColumn(sort_column, sort_order)
        self._sync_bulk_header()
        self._filter(self.search.text())
        self._show_catalog_content()

    def _filter(self, text: str) -> None:
        self._lazy_branches.apply_filter(text)
        self._sync_bulk_header()

    def _bulk_catalog_keys(self) -> list[tuple[str, str]]:
        if not self.search.text().strip():
            return self._catalog_keys
        query = self.search.text().casefold().strip()
        return [
            identity
            for identity in self._catalog_keys
            if query in self._search_text_by_identity[identity]
        ]

    def _populate_shop_recipes(
        self,
        parent: QTreeWidgetItem,
        recipes: tuple[CatalogItem, ...],
    ) -> None:
        for recipe in recipes:
            child = PolicyTreeItem((recipe.display_name, "", "", ""))
            set_catalog_row_icon(child, self._icons.icon_for(recipe), CatalogRowRole.ITEM)
            child.setData(0, Qt.ItemDataRole.UserRole, ("recipe", recipe.game_id))
            child.setSizeHint(0, QSize(0, POLICY_MEDIA_ROW_HEIGHT))
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
            recipe_toggle = policy_checkbox(
                self._config.recipe_overrides.get(
                    recipe.game_id, self._config.recipe_default_enabled
                ),
                f"{recipe.display_name}: enabled",
            )
            recipe_toggle.toggled.connect(
                lambda value, item_key=recipe.game_id: self._set_item_enabled(
                    "recipe", item_key, value
                )
            )
            set_policy_widget(
                self.tree,
                child,
                3,
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
            set_policy_value(item, 3, value)
        self._sync_bulk_header()

    def _show_empty(self, message: str) -> None:
        item = QTreeWidgetItem((message, "", "", ""))
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setSizeHint(0, QSize(0, POLICY_COMPACT_ROW_HEIGHT))
        self.tree.addTopLevelItem(item)
        item.setFirstColumnSpanned(True)
        self.bulk_header.set_state(3, Qt.CheckState.Unchecked, enabled=False)

    def _catalog_content_widgets(self) -> tuple[QWidget, ...]:
        return (self.toolbar, self.tree)

    def _set_item_enabled(self, kind: str, key: str, enabled: bool) -> None:
        field = "shop_overrides" if kind == "shop" else "recipe_overrides"
        default = (
            self._config.shop_default_enabled
            if kind == "shop"
            else self._config.recipe_default_enabled
        )
        values = dict(getattr(self._config, field))
        apply_policy_override(values, key, enabled, default)
        self._emit(**{field: values})
        self._sync_bulk_header()

    def _edit_ingredient_reserves(self) -> None:
        catalog = load_gui_catalog(self._config)
        if catalog is None:
            return
        dialog = IngredientReservesDialog(self._config, catalog, self)
        dialog.exec()
        default, reserves = dialog.values()
        if (
            default != self._config.shop_ingredient_reserve_default
            or reserves != self._config.shop_ingredient_reserves
        ):
            self._emit(
                shop_ingredient_reserve_default=default,
                shop_ingredient_reserves=reserves,
            )

    def _set_all(self, _column: int, value: bool) -> None:
        shop_overrides = dict(self._config.shop_overrides)
        recipe_overrides = dict(self._config.recipe_overrides)
        for kind, key in self._bulk_catalog_keys():
            overrides = shop_overrides if kind == "shop" else recipe_overrides
            default = (
                self._config.shop_default_enabled
                if kind == "shop"
                else self._config.recipe_default_enabled
            )
            apply_policy_override(overrides, key, value, default)
        self._emit(shop_overrides=shop_overrides, recipe_overrides=recipe_overrides)
        self.populate()

    def _sync_bulk_header(self) -> None:
        values = [
            (self._config.shop_overrides.get(key, self._config.shop_default_enabled))
            if kind == "shop"
            else self._config.recipe_overrides.get(key, self._config.recipe_default_enabled)
            for kind, key in self._bulk_catalog_keys()
        ]
        state = aggregate_check_state(values)
        self.bulk_header.set_state(3, state, enabled=bool(values))

    def set_building_repairs(self, states: object) -> None:
        if not isinstance(states, tuple):
            return
        self._building_repairs = {
            state.building_id: state for state in states if isinstance(state, BuildingRepairState)
        }
        for (kind, shop_id), item in self._tree_items.items():
            if kind == "shop":
                self._set_repair_status(item, shop_id)

    def _set_repair_status(self, item: QTreeWidgetItem, shop_id: str) -> None:
        state = self._building_repairs.get(shop_id)
        if state is None:
            status = "Unknown"
        elif state.active:
            status = "Repaired"
        elif state.upgrading:
            status = "Repairing"
        elif not state.placed:
            status = "Not on board"
        elif not state.requirements:
            status = "Unavailable"
        elif all(requirement.missing == 0 for requirement in state.requirements):
            status = "Ready to repair"
        else:
            status = f"Needs {sum(requirement.missing for requirement in state.requirements)} items"
        item.setText(2, status)
        item.setData(2, Qt.ItemDataRole.UserRole, status)
