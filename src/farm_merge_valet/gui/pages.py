"""Focused page widgets for the desktop application shell."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr
from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from farm_merge_valet.config import AppConfig, ItemPolicyOverride
from farm_merge_valet.core.board import item_tier_policy_key
from farm_merge_valet.core.item_catalog import (
    CatalogItem,
    ItemCatalog,
    TileClaimMode,
    load_item_catalog,
)
from farm_merge_valet.gui.action_button import ActionButton
from farm_merge_valet.gui.assets import CatalogIconLoader
from farm_merge_valet.gui.bulk_header import BulkToggleHeader
from farm_merge_valet.gui.configuration_header import ConfigurationHeader
from farm_merge_valet.gui.controller import ApplicationState, ApplicationStatus
from farm_merge_valet.gui.hotkey_edit import HotkeyEdit
from farm_merge_valet.gui.input_controls import (
    FocusAwareComboBox,
    FocusAwareDoubleSpinBox,
    FocusAwareSlider,
    FocusAwareSpinBox,
    SettingsToggle,
)
from farm_merge_valet.gui.policy_view import (
    PolicyCheckBox,
    PolicyTreeToolbar,
    aggregate_check_state,
    configure_policy_toggle,
    configure_policy_view,
    filter_policy_tree,
    policy_badge,
    policy_cell,
    policy_unavailable,
)
from farm_merge_valet.gui.widgets import secondary_button, set_validation_state

logger = logging.getLogger(__name__)

_ITEM_ICON_SIZE = QSize(40, 40)
_SHOP_ICON_SIZE = QSize(100, 54)
_ITEM_SORT_COLUMNS = {
    "item": 0,
    "category": 1,
    "enabled": 2,
    "merge": 3,
    "merge_five": 4,
    "claim": 5,
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
    supports_claim: bool
    supports_remove: bool

    def supports(self, field: str) -> bool:
        return (
            field == "enabled"
            or field in {"merge", "prefer_merge_five"}
            and self.supports_merge
            or field == "claim"
            and self.supports_claim
            or field == "always_remove"
            and self.supports_remove
        )


@dataclass(frozen=True)
class ConfigEdit:
    changes: dict[str, object]
    field: str | None = None
    source: str = "settings"


def _settings_section(title: str) -> tuple[QGroupBox, QFormLayout]:
    section = QGroupBox(title)
    section.setMaximumWidth(900)
    form = QFormLayout(section)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setContentsMargins(14, 18, 14, 14)
    form.setSpacing(10)
    return section, form


class AppPage(QWidget):
    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("appPage")
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.AlternateBase)
        self.page_layout = QVBoxLayout(self)
        self.page_layout.setContentsMargins(20, 18, 20, 20)
        self.page_layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        self.page_layout.addWidget(heading)
        if subtitle:
            description = QLabel(subtitle)
            description.setObjectName("pageSubtitle")
            description.setWordWrap(True)
            self.page_layout.addWidget(description)


class _ConfigFormPage(AppPage):
    def __init__(
        self,
        title: str,
        subtitle: str,
        config: AppConfig,
        *,
        form_label_width: int = 210,
    ) -> None:
        super().__init__(title, subtitle)
        self._config = config
        self._form_label_width = form_label_width
        self.controls: dict[str, QWidget] = {}

    def _request(self, field: str, value: object) -> None:
        raise NotImplementedError

    def _add_form_row(
        self,
        form: QFormLayout,
        text: str,
        field: QWidget | QLayout,
    ) -> None:
        label = QLabel(text)
        label.setFixedWidth(self._form_label_width)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if isinstance(field, QWidget):
            label.setBuddy(field)
        form.addRow(label, field)

    def _add_toggle(self, form: QFormLayout, label: str, field: str) -> None:
        control = SettingsToggle()
        control.setChecked(bool(getattr(self._config, field)))
        control.setAccessibleName(label)
        control.toggled.connect(lambda value, name=field: self._request(name, value))
        self.controls[field] = control
        self._add_form_row(form, label, control)

    def _add_int(
        self,
        form: QFormLayout,
        label: str,
        field: str,
        minimum: int,
        maximum: int,
    ) -> None:
        control = FocusAwareSpinBox()
        control.setRange(minimum, maximum)
        control.setValue(getattr(self._config, field))
        control.setAccessibleName(label)
        control.valueChanged.connect(lambda value, name=field: self._request(name, value))
        self.controls[field] = control
        self._add_form_row(form, label, control)


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

    def __init__(self, config: AppConfig) -> None:
        super().__init__(
            "Item policies",
            "Configure automation behavior for every discovered item family.",
        )
        self._config = config
        self.configuration_header = ConfigurationHeader("Reset item policies")
        self.configuration_header.reset_requested.connect(self.reset_requested)
        self.saved_label = self.configuration_header.status_label
        self.page_layout.addWidget(self.configuration_header)

        self.table = QTreeWidget()
        self.table.setColumnCount(7)
        self.table.setHeaderLabels(("Item / tier", "Category", "", "", "", "", ""))
        self.bulk_header = BulkToggleHeader(
            {
                2: "Enabled",
                3: "Merge",
                4: "Merge 5",
                5: "Claim",
                6: "Remove",
            },
            self.table,
        )
        self.bulk_header.toggled.connect(self._set_all)
        self.table.setHeader(self.bulk_header)
        self.table.setIconSize(_ITEM_ICON_SIZE)
        self.bulk_header.setMinimumSectionSize(72)
        self.bulk_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.bulk_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.bulk_header.resizeSection(1, 116)
        for column in range(2, 7):
            self.bulk_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        configure_policy_view(self.table)
        self.table.setIndentation(22)
        self.table.setRootIsDecorated(True)
        self.table.setUniformRowHeights(True)
        self._apply_sort_preference()
        self.table.setAccessibleName("Item automation policies")

        toolbar = PolicyTreeToolbar(
            self.table,
            placeholder="Search item families and tiers…",
            accessible_name="Search item families and tiers",
            scope="item groups",
        )
        self.search = toolbar.search
        self.expansion_controls = toolbar.expansion_controls
        self.page_layout.addWidget(toolbar)
        self.page_layout.addWidget(self.table, 1)
        self.search.textChanged.connect(self._filter)
        self.populate()
        self.bulk_header.sortIndicatorChanged.connect(self._sort_changed)

    def apply_config(self, config: AppConfig, *, refresh: bool = True) -> None:
        catalog_changed = config.catalog_dir != self._config.catalog_dir
        sort_changed = (
            config.items_sort_column != self._config.items_sort_column
            or config.items_sort_descending != self._config.items_sort_descending
        )
        self._config = config
        if sort_changed:
            self._apply_sort_preference()
        if refresh:
            if catalog_changed or not hasattr(self, "_policy_controls"):
                self.populate()
            else:
                self._sync_policy_controls()
        self.configuration_header.mark_saved()

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
        expanded_keys = self._expanded_policy_keys()
        sort_column = self.table.sortColumn()
        sort_order = self.bulk_header.sortIndicatorOrder()
        self.table.setSortingEnabled(False)
        self.table.clear()
        self._row_definitions: list[_ItemPolicyRow] = []
        self._policy_controls: list[
            tuple[QTreeWidgetItem, PolicyCheckBox, tuple[_ItemPolicyRow, ...], str]
        ] = []
        catalog = _load_catalog(self._config)
        if catalog is None:
            self._show_empty("Catalog unavailable — load the game and sync assets")
            return
        grouped: dict[str, list[CatalogItem]] = defaultdict(list)
        for item in catalog.items.values():
            grouped[item.policy_key].append(item)
        upgrade_target_names: dict[str, str] = {}
        upgrade_groups: dict[str, list[CatalogItem]] = {}
        for key, items in tuple(grouped.items()):
            if not items or items[0].category != "upgrade_cards":
                continue
            grouped.pop(key)
            targets: dict[str, str] = {}
            for target in catalog.items.values():
                if target.category in {"animals", "crops"}:
                    targets.setdefault(target.family_id, target.display_name)
            for target_id, target_name in targets.items():
                variant_key = f"{key}/{target_id}"
                upgrade_groups[variant_key] = items
                upgrade_target_names[variant_key] = target_name
        grouped.update(upgrade_groups)
        families = [
            (key, sorted(items, key=lambda item: (item.tier or 0, item.game_id)))
            for key, items in grouped.items()
            if any(self._item_is_configurable(item) for item in items)
        ]
        families.sort(
            key=lambda entry: (
                entry[1][0].category,
                entry[1][0].display_name.casefold(),
            )
        )
        if not families:
            self._show_empty("No configurable item families have been discovered yet")
            return
        icons = CatalogIconLoader(self._config.catalog_dir)
        roots_by_family_id: dict[str, QTreeWidgetItem] = {}
        for family_key, items in families:
            definitions = [
                self._row_definition(family_key, item, tier_scoped=len(items) > 1) for item in items
            ]
            self._row_definitions.extend(definitions)
            representative = items[0]
            upgrade_target = upgrade_target_names.get(family_key)
            family_name = "Upgrade cards" if upgrade_target else representative.display_name
            if len(definitions) == 1:
                root = self._new_policy_item(
                    family_name,
                    representative,
                    icons,
                    category=representative.category,
                )
                root.setData(0, Qt.ItemDataRole.UserRole, family_key)
                self._attach_policy_root(
                    root,
                    representative,
                    roots_by_family_id,
                    upgrade_target,
                )
                self._set_category_badge(root, representative.category)
                self._add_policy_controls(root, definitions[0], family_name)
                continue
            root = self._new_policy_item(
                family_name,
                representative,
                icons,
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
            )
            self._set_category_badge(root, representative.category)
            self._add_family_controls(root, family_key, definitions, family_name)
            for definition in definitions:
                tier = definition.item.tier
                child_name = f"Tier {tier}" if tier is not None else definition.item.display_name
                child = self._new_policy_item(child_name, definition.item, icons)
                child.setData(0, Qt.ItemDataRole.UserRole, definition.policy_key)
                root.addChild(child)
                self._add_policy_controls(child, definition, f"{family_name}, {child_name}")
            root.setExpanded(family_key in expanded_keys)
        self.table.setSortingEnabled(True)
        self.bulk_header.setSortIndicatorShown(False)
        self.table.sortByColumn(sort_column, sort_order)
        self._sync_bulk_header()

    def _expanded_policy_keys(self) -> set[object]:
        expanded: set[object] = set()
        pending = [
            self.table.topLevelItem(index) for index in range(self.table.topLevelItemCount())
        ]
        while pending:
            item = pending.pop()
            if item is None:
                continue
            if item.isExpanded():
                expanded.add(item.data(0, Qt.ItemDataRole.UserRole))
            pending.extend(item.child(index) for index in range(item.childCount()))
        return expanded

    def _attach_policy_root(
        self,
        root: QTreeWidgetItem,
        representative: CatalogItem,
        roots_by_family_id: dict[str, QTreeWidgetItem],
        upgrade_target: str | None,
    ) -> None:
        if upgrade_target is not None:
            target_root = roots_by_family_id.get(
                root.data(0, Qt.ItemDataRole.UserRole).rsplit("/", 1)[-1]
            )
            if target_root is not None:
                target_root.addChild(root)
                return
            root.setText(0, f"{upgrade_target} Upgrade Cards")
        self.table.addTopLevelItem(root)
        if upgrade_target is None and representative.category in {"animals", "crops"}:
            roots_by_family_id[representative.family_id] = root

    @staticmethod
    def _item_is_configurable(item: CatalogItem) -> bool:
        supports_claim = item.tile_claim_mode is TileClaimMode.IMMEDIATE or (
            item.category in {"animals", "crops"} and item.tier == 4
        )
        return item.merge_target is not None or supports_claim or "shovelable" in item.capabilities

    @staticmethod
    def _row_definition(family_key: str, item: CatalogItem, *, tier_scoped: bool) -> _ItemPolicyRow:
        return _ItemPolicyRow(
            item_tier_policy_key(family_key, item.tier)
            if tier_scoped and item.tier is not None
            else family_key,
            family_key,
            item,
            item.merge_target is not None,
            item.tile_claim_mode is TileClaimMode.IMMEDIATE
            or (item.category in {"animals", "crops"} and item.tier == 4),
            "shovelable" in item.capabilities,
        )

    def _new_policy_item(
        self,
        name: str,
        catalog_item: CatalogItem,
        icons: CatalogIconLoader,
        *,
        category: str | None = None,
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem((name, "", "", "", "", "", ""))
        item.setIcon(0, icons.icon_for(catalog_item))
        item.setSizeHint(0, QSize(0, 52))
        item.setToolTip(0, name)
        font = item.font(0)
        font.setWeight(QFont.Weight.DemiBold if category is not None else QFont.Weight.Normal)
        item.setFont(0, font)
        if category is not None:
            category_label = category.replace("_", " ").title()
            item.setText(1, category_label)
        return item

    def _set_category_badge(self, item: QTreeWidgetItem, category: str) -> None:
        item.setForeground(1, QColor(Qt.GlobalColor.transparent))
        self.table.setItemWidget(item, 1, policy_badge(category.replace("_", " ").title()))

    def _add_policy_controls(
        self, item: QTreeWidgetItem, definition: _ItemPolicyRow, label: str
    ) -> None:
        policy = self._config.item_policy(definition.policy_key)
        for column, field in self._policy_fields().items():
            if not definition.supports(field):
                self.table.setItemWidget(item, column, policy_unavailable())
                item.setText(column, "−1")
                item.setForeground(column, QColor(Qt.GlobalColor.transparent))
                continue
            value = getattr(policy, field)
            control = self._policy_checkbox(value, f"{label}: {field.replace('_', ' ')}")
            control.toggled.connect(
                lambda checked, row=definition, name=field: self.set_override(
                    row.policy_key, row.family_key, name, checked
                )
            )
            self.table.setItemWidget(item, column, policy_cell(control))
            item.setText(column, "1" if value else "0")
            item.setForeground(column, QColor(Qt.GlobalColor.transparent))
            self._policy_controls.append((item, control, (definition,), field))

    def _add_family_controls(
        self,
        item: QTreeWidgetItem,
        family_key: str,
        definitions: list[_ItemPolicyRow],
        label: str,
    ) -> None:
        for column, field in self._policy_fields().items():
            applicable = [definition for definition in definitions if definition.supports(field)]
            if not applicable:
                self.table.setItemWidget(item, column, policy_unavailable())
                item.setText(column, "−1")
                item.setForeground(column, QColor(Qt.GlobalColor.transparent))
                continue
            values = [
                getattr(self._config.item_policy(definition.policy_key), field)
                for definition in applicable
            ]
            state = aggregate_check_state(values)
            control = self._policy_checkbox(
                state,
                f"{label}: {field.replace('_', ' ')} for all tiers",
                tristate=True,
            )
            control.clicked.connect(
                lambda checked, key=family_key, rows=applicable, name=field: self._set_family(
                    key, rows, name, checked
                )
            )
            self.table.setItemWidget(item, column, policy_cell(control))
            item.setText(column, str(state.value))
            item.setForeground(column, QColor(Qt.GlobalColor.transparent))
            self._policy_controls.append((item, control, tuple(applicable), field))

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
            item.setText(column := self._column_for_field(field), str(state.value))
            item.setForeground(column, QColor(Qt.GlobalColor.transparent))
        self._sync_bulk_header()

    def _column_for_field(self, field: str) -> int:
        return next(column for column, name in self._policy_fields().items() if name == field)

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
            5: "claim",
            6: "always_remove",
        }

    def _show_empty(self, message: str) -> None:
        item = QTreeWidgetItem((message, "", "", "", "", "", ""))
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setSizeHint(0, QSize(0, 52))
        self.table.addTopLevelItem(item)
        for column in range(2, 7):
            self.bulk_header.set_state(column, Qt.CheckState.Unchecked, enabled=False)

    def _filter(self, text: str) -> None:
        filter_policy_tree(self.table, text)

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

    def __init__(self, config: AppConfig) -> None:
        super().__init__("Shops", "Configure every discovered shop and recipe independently.")
        self._config = config
        self.configuration_header = ConfigurationHeader("Reset shop policies")
        self.configuration_header.reset_requested.connect(self.reset_requested)
        self.saved_label = self.configuration_header.status_label
        self.page_layout.addWidget(self.configuration_header)
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

        toolbar = PolicyTreeToolbar(
            self.tree,
            placeholder="Search shops and recipes…",
            accessible_name="Search shops and recipes",
            scope="shop and recipe groups",
        )
        self.search = toolbar.search
        self.expansion_controls = toolbar.expansion_controls
        self.page_layout.addWidget(toolbar)
        self.page_layout.addWidget(self.tree, 1)
        self.search.textChanged.connect(self._filter)
        self.populate()
        self.bulk_header.sortIndicatorChanged.connect(self._sort_changed)

    def apply_config(self, config: AppConfig, *, refresh: bool = True) -> None:
        catalog_changed = config.catalog_dir != self._config.catalog_dir
        sort_changed = (
            config.shops_sort_column != self._config.shops_sort_column
            or config.shops_sort_descending != self._config.shops_sort_descending
        )
        self._config = config
        if sort_changed:
            self._apply_sort_preference()
        if refresh:
            if catalog_changed or not hasattr(self, "_toggles"):
                self.populate()
            else:
                self._sync_controls()
        self.configuration_header.mark_saved()

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
        sort_column = self.tree.sortColumn()
        sort_order = self.bulk_header.sortIndicatorOrder()
        self.tree.setSortingEnabled(False)
        self.tree.blockSignals(True)
        self.tree.clear()
        self._catalog_keys: list[tuple[str, str]] = []
        self._toggles: dict[tuple[str, str], PolicyCheckBox] = {}
        self._tree_items: dict[tuple[str, str], QTreeWidgetItem] = {}
        catalog = _load_catalog(self._config)
        if catalog is None:
            self._show_empty("Catalog unavailable — load the game and sync assets")
            self.tree.blockSignals(False)
            return
        recipes: dict[str, list[CatalogItem]] = defaultdict(list)
        for item in catalog.items.values():
            if item.recipe is not None:
                recipes[item.recipe.shop_id].append(item)
        if not recipes:
            self._show_empty("No shops or recipes have been discovered yet")
            self.tree.blockSignals(False)
            return
        icons = CatalogIconLoader(self._config.catalog_dir)
        for shop_id in sorted(recipes):
            shop = catalog.items.get(shop_id)
            shop_name = shop.display_name if shop else shop_id
            parent = QTreeWidgetItem((shop_name, "Shop", ""))
            parent.setIcon(0, icons.icon_for(shop))
            parent.setData(0, Qt.ItemDataRole.UserRole, ("shop", shop_id))
            parent.setSizeHint(0, QSize(0, 64))
            parent_font = parent.font(0)
            parent_font.setWeight(QFont.Weight.DemiBold)
            parent.setFont(0, parent_font)
            parent.setToolTip(0, shop.display_name if shop else shop_id)
            self.tree.addTopLevelItem(parent)
            parent.setForeground(1, QColor(Qt.GlobalColor.transparent))
            self.tree.setItemWidget(parent, 1, policy_badge("Shop"))
            shop_toggle = PolicyCheckBox()
            configure_policy_toggle(shop_toggle)
            shop_toggle.setChecked(
                self._config.shop_overrides.get(shop_id, self._config.shop_default_enabled)
            )
            parent.setText(2, "1" if shop_toggle.isChecked() else "0")
            parent.setForeground(2, QColor(Qt.GlobalColor.transparent))
            shop_toggle.setAccessibleName(f"{parent.text(0)}: enabled")
            shop_toggle.toggled.connect(
                lambda value, item_key=shop_id: self._set_item_enabled("shop", item_key, value)
            )
            self.tree.setItemWidget(parent, 2, policy_cell(shop_toggle))
            self._catalog_keys.append(("shop", shop_id))
            self._toggles[("shop", shop_id)] = shop_toggle
            self._tree_items[("shop", shop_id)] = parent
            for recipe in sorted(recipes[shop_id], key=lambda item: item.display_name):
                child = QTreeWidgetItem((recipe.display_name, "Recipe", ""))
                child.setIcon(0, icons.icon_for(recipe))
                child.setData(0, Qt.ItemDataRole.UserRole, ("recipe", recipe.game_id))
                child.setSizeHint(0, QSize(0, 64))
                child.setToolTip(0, recipe.display_name)
                parent.addChild(child)
                child.setForeground(1, QColor(Qt.GlobalColor.transparent))
                self.tree.setItemWidget(child, 1, policy_badge("Recipe"))
                recipe_toggle = PolicyCheckBox()
                configure_policy_toggle(recipe_toggle)
                recipe_toggle.setChecked(
                    self._config.recipe_overrides.get(
                        recipe.game_id, self._config.recipe_default_enabled
                    )
                )
                child.setText(2, "1" if recipe_toggle.isChecked() else "0")
                child.setForeground(2, QColor(Qt.GlobalColor.transparent))
                recipe_toggle.setAccessibleName(f"{recipe.display_name}: enabled")
                recipe_toggle.toggled.connect(
                    lambda value, item_key=recipe.game_id: self._set_item_enabled(
                        "recipe", item_key, value
                    )
                )
                self.tree.setItemWidget(child, 2, policy_cell(recipe_toggle))
                self._catalog_keys.append(("recipe", recipe.game_id))
                self._toggles[("recipe", recipe.game_id)] = recipe_toggle
                self._tree_items[("recipe", recipe.game_id)] = child
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self.tree.setSortingEnabled(True)
        self.bulk_header.setSortIndicatorShown(False)
        self.tree.sortByColumn(sort_column, sort_order)
        self._sync_bulk_header()
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        filter_policy_tree(self.tree, text)

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
            item.setText(2, "1" if value else "0")
            item.setForeground(2, QColor(Qt.GlobalColor.transparent))
        self._sync_bulk_header()

    def _show_empty(self, message: str) -> None:
        item = QTreeWidgetItem((message, "", ""))
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setSizeHint(0, QSize(0, 52))
        self.tree.addTopLevelItem(item)
        item.setFirstColumnSpanned(True)
        self.bulk_header.set_state(2, Qt.CheckState.Unchecked, enabled=False)

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


class BrowserPage(_ConfigFormPage):
    config_edited = Signal(object)
    refresh_requested = Signal()
    restart_requested = Signal()
    reset_requested = Signal()

    def __init__(self, config: AppConfig) -> None:
        super().__init__(
            "Managed browser",
            "Manage the dedicated browser, game connection, and local asset cache.",
            config,
        )
        self.configuration_header = ConfigurationHeader("Reset browser configuration")
        self.configuration_header.reset_requested.connect(self.reset_requested)
        self.saved_label = self.configuration_header.status_label
        self.page_layout.addWidget(self.configuration_header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        sections = QVBoxLayout(content)
        sections.setContentsMargins(4, 4, 4, 4)
        sections.setSpacing(14)

        managed, form = _settings_section("Managed browser")
        self.status_label = QLabel("Not checked")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Managed browser status")
        self.browser_choice = FocusAwareComboBox()
        self.browser_choice.set_choices(
            (
                ("Auto-detect", "auto"),
                ("Google Chrome", "chrome"),
                ("Microsoft Edge", "edge"),
                ("Brave", "brave"),
                ("Chromium", "chromium"),
            )
        )
        self.browser_choice.set_current_value(config.browser)
        self.browser_choice.setAccessibleName("Preferred browser")
        self.browser_choice.currentIndexChanged.connect(
            lambda _index: self._request(
                "browser", str(self.browser_choice.current_value())
            )
        )
        self.controls["browser"] = self.browser_choice
        self._add_form_row(form, "Status", self.status_label)
        self._add_form_row(form, "Preferred browser", self.browser_choice)
        self._add_toggle(form, "Launch automatically when needed", "browser_auto_launch")
        self._add_path(form, "Browser executable", "browser_executable")
        self._add_path(form, "Browser profile directory", "browser_profile_dir")
        buttons = QHBoxLayout()
        self.refresh_button = secondary_button("Refresh status")
        self.restart_button = QPushButton("Restart managed browser")
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.restart_button.clicked.connect(self.restart_requested)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.restart_button)
        buttons.addStretch()
        self._add_form_row(form, "Actions", buttons)
        sections.addWidget(managed)

        connection, form = _settings_section("Game connection")
        self._add_text(form, "Game URL", "game_url")
        self._add_text(form, "Page target", "window_title")
        self._add_int(form, "CDP port", "cdp_port", 1, 65535)
        sections.addWidget(connection)

        assets, form = _settings_section("Assets and cache")
        self._add_path(form, "Catalog directory", "catalog_dir", optional=False)
        self._add_path(form, "Atlas cache directory", "atlas_cache_dir", optional=False)
        sections.addWidget(assets)
        sections.addStretch()
        scroll.setWidget(content)
        self.page_layout.addWidget(scroll, 1)
        self._busy = False
        self._runtime_active = False

    def _request(self, field: str, value: object) -> None:
        self.config_edited.emit(ConfigEdit({field: value}, field, "browser"))

    def _add_text(self, form: QFormLayout, label: str, field: str) -> None:
        control = QLineEdit(str(getattr(self._config, field)))
        control.setAccessibleName(label)
        control.editingFinished.connect(
            lambda widget=control, name=field: self._request(name, widget.text().strip())
        )
        self.controls[field] = control
        self._add_form_row(form, label, control)

    def _add_path(
        self, form: QFormLayout, label: str, field: str, *, optional: bool = True
    ) -> None:
        value = getattr(self._config, field)
        control = QLineEdit(str(value) if value is not None else "")
        control.setAccessibleName(label)
        browse = secondary_button("Browse…")
        browse.setAccessibleName(f"Choose {label.lower()}")
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(control, 1)
        layout.addWidget(browse)

        def update() -> None:
            text = control.text().strip()
            self._request(field, Path(text) if text else (None if optional else value))

        def choose() -> None:
            current = control.text().strip()
            if field == "browser_executable":
                selected, _ = QFileDialog.getOpenFileName(self, f"Choose {label}", current)
            else:
                selected = QFileDialog.getExistingDirectory(self, f"Choose {label}", current)
            if selected:
                control.setText(selected)
                update()

        control.editingFinished.connect(update)
        browse.clicked.connect(choose)
        self.controls[field] = control
        self._add_form_row(form, label, row)

    def apply_config(self, config: AppConfig) -> None:
        self._config = config
        for field, control in self.controls.items():
            control.blockSignals(True)
            value = getattr(config, field)
            if isinstance(control, FocusAwareComboBox):
                control.set_current_value(value)
            elif isinstance(control, QCheckBox):
                control.setChecked(bool(value))
            elif isinstance(control, QSpinBox):
                control.setValue(int(value))
            elif isinstance(control, QLineEdit):
                control.setText(str(value) if value is not None else "")
            control.blockSignals(False)
        self.configuration_header.mark_saved()

    def mark_saving(self, field: str | None = None) -> None:
        self.configuration_header.mark_saving()
        if field is not None and field in self.controls:
            set_validation_state(self.controls[field])

    def show_validation_error(self, field: str | None, message: str, config: AppConfig) -> None:
        self.configuration_header.mark_error(f"Invalid: {message}")
        if field is None or field not in self.controls:
            return
        self.apply_config(config)
        self.configuration_header.mark_error(f"Invalid: {message}")
        control = self.controls[field]
        set_validation_state(control, message)
        control.setFocus()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.refresh_button.setEnabled(not busy)
        self.restart_button.setEnabled(not busy and not self._runtime_active)

    def set_runtime_active(self, active: bool) -> None:
        self._runtime_active = active
        self.restart_button.setEnabled(not active and not self._busy)


class SettingsPage(_ConfigFormPage):
    config_edited = Signal(object)
    reset_requested = Signal()
    reset_all_requested = Signal()
    hotkey_recording_changed = Signal(bool)

    def __init__(self, config: AppConfig) -> None:
        super().__init__("Settings", "Changes validate and autosave automatically.", config)
        self.opacity_labels: dict[str, QLabel] = {}
        self.configuration_header = ConfigurationHeader("Reset settings")
        self.configuration_header.reset_requested.connect(self.reset_requested)
        self.saved_label = self.configuration_header.status_label
        reset_all = self.configuration_header.add_action("Reset all")
        reset_all.clicked.connect(lambda _checked=False: self.reset_all_requested.emit())
        self.reset_all_button = reset_all
        self.page_layout.addWidget(self.configuration_header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        sections = QVBoxLayout(content)
        sections.setContentsMargins(4, 4, 4, 4)
        sections.setSpacing(14)

        automation, form = _settings_section("Automation")
        self._add_int(form, "Reserved empty cells", "merge_empty_cell_reserve", 0, 50)
        self._add_int(form, "Producer claim open cells", "producer_claim_min_empty_cells", 1, 50)
        self._add_float(form, "Idle polling (seconds)", "idle_wait_seconds", 0, 3600, 0.1)
        self._add_float(form, "Loop interval (seconds)", "loop_interval", 0.01, 60, 0.1)
        self._add_float(form, "Item delay minimum", "item_action_delay_min", 0, 60, 0.1)
        self._add_float(form, "Item delay maximum", "item_action_delay_max", 0, 60, 0.1)
        self._add_float(form, "Crate delay minimum", "crate_delay_min", 0, 5, 0.05)
        self._add_float(form, "Crate delay maximum", "crate_delay_max", 0, 5, 0.05)
        sections.addWidget(automation)

        controls, form = _settings_section("Controls and startup")
        self._add_hotkey(form, "Start / stop", "start_stop_hotkey")
        self._add_hotkey(form, "Pause / resume", "pause_hotkey")
        self._add_hotkey(form, "Quit application", "quit_hotkey")
        for field, label in (
            ("start_paused", "Start automation paused"),
            ("start_minimized", "Start minimized to tray"),
            ("bot_autostart", "Start bot with application"),
            ("close_to_tray", "Close window to tray"),
        ):
            self._add_toggle(form, label, field)
        sections.addWidget(controls)

        notifications, form = _settings_section("Notifications")
        webhook = QLineEdit()
        webhook.setEchoMode(QLineEdit.EchoMode.Password)
        webhook.setPlaceholderText("Optional Discord webhook URL")
        webhook.setAccessibleName("Discord webhook")
        if config.discord_webhook_url:
            webhook.setText(config.discord_webhook_url.get_secret_value())
        webhook.editingFinished.connect(
            lambda: self._request("discord_webhook_url", webhook.text().strip() or None)
        )
        self.controls["discord_webhook_url"] = webhook
        self._add_form_row(form, "Discord webhook", webhook)
        self._add_float(form, "Webhook status interval", "webhook_status_interval", 0, 3600, 1)
        self._add_float(form, "Webhook summary interval", "webhook_summary_interval", 60, 86400, 1)
        sections.addWidget(notifications)

        appearance, form = _settings_section("Appearance")
        theme = FocusAwareComboBox()
        theme.set_choices(
            (("System default", "system"), ("Dark", "dark"), ("Light", "light"))
        )
        theme.set_current_value(config.theme)
        theme.setAccessibleName("Theme")
        theme.currentIndexChanged.connect(
            lambda _index: self._request("theme", str(theme.current_value()))
        )
        self.controls["theme"] = theme
        self._add_form_row(form, "Theme", theme)
        for field, label in (
            ("main_always_on_top", "Dashboard always on top"),
            ("overlay_always_on_top", "Overlay always on top"),
            ("overlay_click_through", "Overlay click-through while inactive"),
        ):
            self._add_toggle(form, label, field)
        self._add_opacity(form, "Dashboard inactive opacity", "main_unfocused_opacity")
        self._add_opacity(form, "Dashboard focused opacity", "main_focused_opacity")
        self._add_opacity(form, "Overlay inactive opacity", "overlay_unfocused_opacity")
        self._add_opacity(form, "Overlay focused opacity", "overlay_focused_opacity")
        sections.addWidget(appearance)
        sections.addStretch()
        scroll.setWidget(content)
        self.page_layout.addWidget(scroll, 1)

    def _request(self, field: str, value: object) -> None:
        self.config_edited.emit(ConfigEdit({field: value}, field))

    def _add_float(
        self,
        form: QFormLayout,
        label: str,
        field: str,
        minimum: float,
        maximum: float,
        step: float,
    ) -> None:
        control = FocusAwareDoubleSpinBox()
        control.setRange(minimum, maximum)
        control.setDecimals(2)
        control.setSingleStep(step)
        control.setKeyboardTracking(False)
        control.setValue(getattr(self._config, field))
        control.setAccessibleName(label)
        control.valueChanged.connect(lambda value, name=field: self._request(name, value))
        self.controls[field] = control
        self._add_form_row(form, label, control)

    def _add_hotkey(self, form: QFormLayout, label: str, field: str) -> None:
        value = getattr(self._config, field)
        control = HotkeyEdit(value if isinstance(value, str) else None, f"{label} global hotkey")
        control.value_changed.connect(lambda hotkey, name=field: self._request(name, hotkey))
        control.recording_changed.connect(self.hotkey_recording_changed)
        self.controls[field] = control
        self._add_form_row(form, label, control)

    def _add_opacity(self, form: QFormLayout, label: str, field: str) -> None:
        control = FocusAwareSlider(Qt.Orientation.Horizontal)
        control.setRange(25, 100)
        control.setValue(round(getattr(self._config, field) * 100))
        control.setAccessibleName(label)
        value_label = QLabel(f"{control.value()}%")
        value_label.setMinimumWidth(42)
        value_label.setAccessibleName(f"{label} value")
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(control, 1)
        layout.addWidget(value_label)

        def update(value: int) -> None:
            value_label.setText(f"{value}%")
            self._request(field, value / 100)

        control.valueChanged.connect(update)
        self.controls[field] = control
        self.opacity_labels[field] = value_label
        self._add_form_row(form, label, row)

    def show_validation_error(self, field: str | None, message: str, config: AppConfig) -> None:
        self.configuration_header.mark_error(f"Invalid: {message}")
        if field is None or field not in self.controls:
            return
        control = self.controls[field]
        self._set_control_value(field, control, getattr(config, field))
        if isinstance(control, HotkeyEdit):
            control.show_error(message)
            control.setFocus()
            return
        set_validation_state(control, message)
        control.setFocus()

    def mark_saving(self, field: str | None = None) -> None:
        self.configuration_header.mark_saving()
        if field is not None and field in self.controls:
            control = self.controls[field]
            if isinstance(control, HotkeyEdit):
                control.show_error("")
            else:
                set_validation_state(control)

    def apply_config(self, config: AppConfig) -> None:
        self._config = config
        for field, control in self.controls.items():
            self._set_control_value(field, control, getattr(config, field))
        self.configuration_header.mark_saved()

    def _set_control_value(self, field: str, control: QWidget, value: object) -> None:
        control.blockSignals(True)
        if isinstance(control, HotkeyEdit):
            control.set_value(value if isinstance(value, str) else None)
        elif isinstance(control, QCheckBox):
            control.setChecked(bool(value))
        elif isinstance(control, QSpinBox):
            control.setValue(int(str(value)))
        elif isinstance(control, QDoubleSpinBox):
            control.setValue(float(str(value)))
        elif isinstance(control, QSlider):
            percent = round(float(str(value)) * 100)
            control.setValue(percent)
            self.opacity_labels[field].setText(f"{percent}%")
        elif isinstance(control, FocusAwareComboBox):
            control.set_current_value(value)
        elif isinstance(control, QLineEdit):
            if field == "discord_webhook_url" and isinstance(value, SecretStr):
                value = value.get_secret_value()
            control.setText(str(value) if value is not None else "")
        control.blockSignals(False)


class LogsPage(AppPage):
    log_level_changed = Signal(str)

    def __init__(self, log_level: str) -> None:
        super().__init__("Logs", "Structured application events from the shared logging pipeline.")
        toolbar = QHBoxLayout()
        self.filter = FocusAwareComboBox()
        self.filter.set_choices(
            (("Debug", "DEBUG"), ("Info", "INFO"), ("Warning", "WARNING"), ("Error", "ERROR"))
        )
        self.filter.set_current_value(log_level)
        self.filter.setAccessibleName("Minimum log level")
        self.filter.currentIndexChanged.connect(
            lambda _index: self.log_level_changed.emit(str(self.filter.current_value()))
        )
        clear = secondary_button("Clear")
        clear.clicked.connect(self.clear)
        toolbar.addWidget(QLabel("Minimum level"))
        toolbar.addWidget(self.filter)
        toolbar.addStretch()
        toolbar.addWidget(clear)
        self.page_layout.addLayout(toolbar)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(2000)
        self.view.setFont(QFont("Cascadia Mono, Consolas, monospace", 10))
        self.view.setAccessibleName("Application logs")
        self.page_layout.addWidget(self.view, 1)

    def append(self, timestamp: str, level: str, message: str, levelno: int) -> None:
        minimum = getattr(logging, str(self.filter.current_value()), logging.INFO)
        if levelno >= minimum:
            self.view.appendPlainText(f"[{timestamp}] {level:<8} {message}")

    def clear(self) -> None:
        self.view.clear()
