"""Focused page widgets for the desktop application shell."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from functools import partial

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHeaderView,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidgetItem,
)

from farm_merge_valet.catalog.models import (
    CatalogItem,
)
from farm_merge_valet.config import AppConfig
from farm_merge_valet.gui.components.catalog_onboarding import CatalogOnboarding
from farm_merge_valet.gui.components.configuration_header import ConfigurationHeader
from farm_merge_valet.gui.components.incremental_work import IncrementalWorkRunner
from farm_merge_valet.gui.components.loading_state import LoadingState
from farm_merge_valet.gui.components.metrics import (
    POLICY_COMPACT_ROW_HEIGHT,
    POLICY_ICON_SIZE,
    POLICY_MEDIA_BADGE_COLUMN_WIDTH,
    POLICY_MEDIA_ROW_HEIGHT,
)
from farm_merge_valet.gui.components.policy_tree import create_policy_tree
from farm_merge_valet.gui.components.policy_view import (
    LazyPolicyBranches,
    PolicyCheckBox,
    PolicyTreeItem,
    aggregate_check_state,
    configure_policy_toggle,
    expanded_policy_keys,
    fit_policy_widget_column,
    policy_badge,
    policy_cell,
    set_policy_value,
    set_policy_widget,
)
from farm_merge_valet.gui.pages.base import AppPage, ConfigEdit
from farm_merge_valet.gui.services.assets import CatalogIconLoader
from farm_merge_valet.gui.services.catalog import load_gui_catalog

_SHOP_ICON_SIZE = QSize(100, 54)
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
            else POLICY_ICON_SIZE
        )


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
        self.master_toggle = PolicyCheckBox("Enable shop automation")
        configure_policy_toggle(self.master_toggle)
        self.master_toggle.setChecked(config.shop_automation_enabled)
        self.master_toggle.setAccessibleName("Enable shop automation")
        self.master_toggle.setToolTip(
            "Pause all shop starts and claims without changing shop or recipe selections."
        )
        self.master_toggle.toggled.connect(self._set_master_enabled)
        self.page_layout.addWidget(self.master_toggle)
        self.catalog_onboarding = CatalogOnboarding()
        self.catalog_onboarding.setup_requested.connect(self.catalog_setup_requested)
        self.page_layout.addWidget(
            self.catalog_onboarding,
            1,
            Qt.AlignmentFlag.AlignCenter,
        )
        self.loading_state = LoadingState(
            "Loading shops\u2026",
            "Preparing locally cached shops, recipes, and controls.",
        )
        self.loading_state.setVisible(False)
        self.page_layout.addWidget(
            self.loading_state,
            1,
            Qt.AlignmentFlag.AlignCenter,
        )
        scaffold = create_policy_tree(
            header_labels=("Shop / recipe", "Type", ""),
            bulk_labels={2: "Enabled"},
            accessible_name="Shop and recipe policies",
            search_placeholder="Search shops and recipes\u2026",
            search_accessible_name="Search shops and recipes",
            scope="shop and recipe groups",
            icon_size=_SHOP_ICON_SIZE,
            minimum_section_size=96,
        )
        self.tree = scaffold.tree
        self.bulk_header = scaffold.header
        self.bulk_header.toggled.connect(self._set_all)
        self.tree.setItemDelegate(_ShopIconDelegate(self.tree))
        self.bulk_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.bulk_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.bulk_header.resizeSection(1, 124)
        self.bulk_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._apply_sort_preference()

        self.toolbar = scaffold.toolbar
        self.search = self.toolbar.search
        self.expansion_controls = self.toolbar.expansion_controls
        self.page_layout.addWidget(self.toolbar)
        self.page_layout.addWidget(self.tree, 1)
        self.toolbar.filter_requested.connect(self._filter)
        self._catalog_loaded = False
        self._populated = False
        self._population_pending = False
        self._population_runner = IncrementalWorkRunner(self)
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
        self.master_toggle.blockSignals(True)
        self.master_toggle.setChecked(config.shop_automation_enabled)
        self.master_toggle.blockSignals(False)
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

    def ensure_populated(self, *, deferred: bool = False) -> None:
        if self._populated or self._population_pending:
            return
        if not deferred:
            self.populate()
            return
        self._population_pending = True
        self._show_loading()
        self._population_runner.start(self._populate_steps(), self._finish_deferred_population)

    def _finish_deferred_population(self) -> None:
        self._population_pending = False

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
        self._population_runner.cancel()
        self._population_pending = False
        for _step in self._populate_steps():
            pass

    def _populate_steps(self) -> Iterator[None]:
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
            parent = PolicyTreeItem((shop_name, "", ""))
            parent.setIcon(0, self._icons.icon_for(shop))
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

    def _populate_shop_recipes(
        self,
        parent: QTreeWidgetItem,
        recipes: tuple[CatalogItem, ...],
    ) -> None:
        for recipe in recipes:
            child = PolicyTreeItem((recipe.display_name, "", ""))
            child.setIcon(0, self._icons.icon_for(recipe))
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
        item.setSizeHint(0, QSize(0, POLICY_COMPACT_ROW_HEIGHT))
        self.tree.addTopLevelItem(item)
        item.setFirstColumnSpanned(True)
        self.bulk_header.set_state(2, Qt.CheckState.Unchecked, enabled=False)

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

    def _set_master_enabled(self, enabled: bool) -> None:
        self._config = AppConfig.model_validate(
            self._config.model_copy(update={"shop_automation_enabled": enabled}).model_dump()
        )
        self.config_edited.emit(
            ConfigEdit({"shop_automation_enabled": enabled}, source="shops")
        )

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
