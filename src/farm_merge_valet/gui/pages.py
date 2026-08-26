"""Focused page widgets for the desktop application shell."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr
from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from farm_merge_valet.config import AppConfig, ItemPolicyOverride
from farm_merge_valet.core.item_catalog import (
    CatalogItem,
    ItemCatalog,
    TileClaimMode,
    load_item_catalog,
)
from farm_merge_valet.gui.assets import CatalogIconLoader
from farm_merge_valet.gui.controller import ApplicationState, ApplicationStatus

logger = logging.getLogger(__name__)

_ITEM_ICON_SIZE = QSize(40, 40)
_SHOP_TREE_ICON_SIZE = QSize(100, 54)


@dataclass(frozen=True)
class ConfigEdit:
    changes: dict[str, object]
    field: str | None = None


def secondary_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("secondary", True)
    return button


class AppPage(QWidget):
    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
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


class DashboardPage(AppPage):
    start_requested = Signal()
    pause_requested = Signal()
    stop_requested = Signal()
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
        self.start_button = QPushButton("Start")
        self.pause_button = secondary_button("Pause")
        self.stop_button = secondary_button("Stop")
        self.overlay_button = secondary_button("Show compact overlay")
        self.start_button.clicked.connect(self.start_requested)
        self.pause_button.clicked.connect(self.pause_requested)
        self.stop_button.clicked.connect(self.stop_requested)
        self.overlay_button.clicked.connect(self.overlay_requested)
        for button in (self.start_button, self.pause_button, self.stop_button):
            controls.addWidget(button)
        controls.addStretch()
        controls.addWidget(self.overlay_button)
        self.page_layout.addLayout(controls)
        self.page_layout.addStretch()
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
        self.mode_value.setText(status.mode)
        self.browser_value.setText(status.browser)
        self.runtime_value.setText(status.runtime)
        self.phase_value.setText(status.phase)
        self.activity_value.setText(status.last_activity)
        active = status.state.active
        self.start_button.setEnabled(not active)
        self.pause_button.setEnabled(active and status.state is not ApplicationState.STOPPING)
        self.stop_button.setEnabled(active and status.state is not ApplicationState.STOPPING)
        self.pause_button.setText(
            "Resume"
            if status.state in {ApplicationState.PAUSED, ApplicationState.RESUMING}
            else "Pause"
        )

    def set_overlay_visible(self, visible: bool) -> None:
        self.overlay_button.setText("Hide compact overlay" if visible else "Show compact overlay")


class _ShopIconDelegate(QStyledItemDelegate):
    def initStyleOption(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        super().initStyleOption(option, index)
        identity = index.data(Qt.ItemDataRole.UserRole)
        option.decorationSize = (
            _SHOP_TREE_ICON_SIZE
            if isinstance(identity, tuple) and identity and identity[0] == "shop"
            else _ITEM_ICON_SIZE
        )


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
            "Controls inherit global, category, and item defaults. Ingredient and train-ticket "
            "claims are enabled by default.",
        )
        self._config = config
        defaults = QHBoxLayout()
        self.default_controls: dict[str, QCheckBox] = {}
        for field, label in (
            ("enabled", "Automation"),
            ("merge", "Merge"),
            ("prefer_merge_five", "Merge five"),
            ("claim", "Claim"),
        ):
            checkbox = QCheckBox(f"Default {label}")
            checkbox.setChecked(getattr(config.item_policy_defaults, field))
            checkbox.toggled.connect(lambda value, name=field: self._set_default(name, value))
            self.default_controls[field] = checkbox
            defaults.addWidget(checkbox)
        defaults.addStretch()
        reset = secondary_button("Reset item policies")
        reset.clicked.connect(self.reset_requested)
        defaults.addWidget(reset)
        self.page_layout.addLayout(defaults)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search item families…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search item families")
        self.page_layout.addWidget(self.search)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ("Item family", "Category", "Enabled", "Merge", "Merge five", "Claim", "Override")
        )
        self.table.setIconSize(_ITEM_ICON_SIZE)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setAccessibleName("Item automation policies")
        self.page_layout.addWidget(self.table, 1)
        self.search.textChanged.connect(self._filter)
        self.populate()

    def apply_config(self, config: AppConfig, *, refresh: bool = True) -> None:
        self._config = config
        for field, control in self.default_controls.items():
            control.blockSignals(True)
            control.setChecked(getattr(config.item_policy_defaults, field))
            control.blockSignals(False)
        if refresh:
            self.populate()

    def _emit(self, **changes: object) -> None:
        self._config = AppConfig.model_validate(
            self._config.model_copy(update=changes).model_dump()
        )
        self.config_edited.emit(ConfigEdit(changes))

    def populate(self) -> None:
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.clearSpans()
        self._reset_buttons: dict[str, QPushButton] = {}
        catalog = _load_catalog(self._config)
        if catalog is None:
            self.table.setRowCount(1)
            self.table.setItem(
                0,
                0,
                QTableWidgetItem("Catalog unavailable — load the game and sync assets"),
            )
            self.table.setSpan(0, 0, 1, 7)
            self.table.setSortingEnabled(sorting)
            return
        grouped: dict[str, list[CatalogItem]] = defaultdict(list)
        for item in catalog.items.values():
            grouped[item.policy_key].append(item)
        rows: list[tuple[str, str, str, bool, bool]] = []
        for key, items in grouped.items():
            supports_merge = any(item.automation_item is not None for item in items)
            supports_claim = any(
                item.tile_claim_mode is TileClaimMode.IMMEDIATE
                or (item.category in {"animals", "crops"} and item.tier == 4)
                for item in items
            )
            if supports_merge or supports_claim:
                representative = min(items, key=lambda item: item.tier or 0)
                rows.append(
                    (
                        key,
                        representative.display_name,
                        representative.category,
                        supports_merge,
                        supports_claim,
                    )
                )
        rows.sort(key=lambda row: (row[2], row[1].casefold()))
        icons = CatalogIconLoader(self._config.catalog_dir)
        self.table.setRowCount(len(rows))
        for row, (key, name, category, supports_merge, supports_claim) in enumerate(rows):
            representative = min(grouped[key], key=lambda item: item.tier or 0)
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.ItemDataRole.UserRole, key)
            name_item.setIcon(icons.icon_for(representative))
            self.table.setItem(row, 0, name_item)
            self.table.setRowHeight(row, 48)
            self.table.setItem(row, 1, QTableWidgetItem(category.replace("_", " ").title()))
            policy = self._config.item_policy(key)
            for column, field, applicable in (
                (2, "enabled", True),
                (3, "merge", supports_merge),
                (4, "prefer_merge_five", supports_merge),
                (5, "claim", supports_claim),
            ):
                checkbox = QCheckBox()
                checkbox.setChecked(getattr(policy, field))
                checkbox.setEnabled(applicable)
                checkbox.setAccessibleName(f"{name}: {field.replace('_', ' ')}")
                checkbox.setToolTip(
                    "Not applicable to this item"
                    if not applicable
                    else "Uses the effective default unless this item has an override"
                )
                checkbox.toggled.connect(
                    lambda value, policy_key=key, name=field: self.set_override(
                        policy_key, name, value
                    )
                )
                container = QWidget()
                cell_layout = QHBoxLayout(container)
                cell_layout.setContentsMargins(0, 0, 0, 0)
                cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell_layout.addWidget(checkbox)
                self.table.setCellWidget(row, column, container)
            reset = secondary_button("Use default")
            reset.setEnabled(key in self._config.item_policy_overrides)
            reset.setAccessibleName(f"Use default policy for {name}")
            reset.clicked.connect(
                lambda _checked=False, policy_key=key: self.reset_item(policy_key)
            )
            self._reset_buttons[key] = reset
            self.table.setCellWidget(row, 6, reset)
        self.table.setSortingEnabled(sorting)

    def _filter(self, text: str) -> None:
        needle = text.casefold().strip()
        for row in range(self.table.rowCount()):
            cells = [self.table.item(row, column) for column in (0, 1)]
            content = " ".join(cell.text() for cell in cells if cell is not None)
            self.table.setRowHidden(row, needle not in content.casefold())

    def _set_default(self, field: str, value: bool) -> None:
        defaults = self._config.item_policy_defaults.model_copy(update={field: value})
        self._emit(item_policy_defaults=defaults)
        self.populate()

    def set_override(self, key: str, field: str, value: bool) -> None:
        overrides = dict(self._config.item_policy_overrides)
        values = overrides.get(key, ItemPolicyOverride()).model_dump(exclude_none=True)
        default_value = getattr(self._config.item_policy_default(key), field)
        if value == default_value:
            values.pop(field, None)
        else:
            values[field] = value
        if values:
            overrides[key] = ItemPolicyOverride.model_validate(values)
        else:
            overrides.pop(key, None)
        self._emit(item_policy_overrides=overrides)
        self._reset_buttons[key].setEnabled(key in overrides)

    def reset_item(self, key: str) -> None:
        overrides = dict(self._config.item_policy_overrides)
        overrides.pop(key, None)
        self._emit(item_policy_overrides=overrides)
        self.populate()


class ShopsPage(AppPage):
    config_edited = Signal(object)
    reset_requested = Signal()

    def __init__(self, config: AppConfig) -> None:
        super().__init__("Shops", "Configure discovered shops and recipes independently.")
        self._config = config
        defaults = QHBoxLayout()
        self.shop_default = QCheckBox("Enable shops by default")
        self.recipe_default = QCheckBox("Enable recipes by default")
        self.shop_default.setChecked(config.shop_default_enabled)
        self.recipe_default.setChecked(config.recipe_default_enabled)
        self.shop_default.toggled.connect(lambda value: self._set_default("shop", value))
        self.recipe_default.toggled.connect(lambda value: self._set_default("recipe", value))
        defaults.addWidget(self.shop_default)
        defaults.addWidget(self.recipe_default)
        defaults.addStretch()
        reset = secondary_button("Reset shop policies")
        reset.clicked.connect(self.reset_requested)
        defaults.addWidget(reset)
        self.page_layout.addLayout(defaults)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(("Shop / recipe", "Type", "Override"))
        self.tree.setIconSize(_SHOP_TREE_ICON_SIZE)
        self.tree.setItemDelegate(_ShopIconDelegate(self.tree))
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setAccessibleName("Shop and recipe policies")
        self.page_layout.addWidget(self.tree, 1)
        self.populate()
        self.tree.itemChanged.connect(self._item_changed)

    def apply_config(self, config: AppConfig, *, refresh: bool = True) -> None:
        self._config = config
        if refresh:
            self._sync_default_controls()
            self.populate()

    def _emit(self, **changes: object) -> None:
        self._config = AppConfig.model_validate(
            self._config.model_copy(update=changes).model_dump()
        )
        self.config_edited.emit(ConfigEdit(changes))

    def _sync_default_controls(self) -> None:
        for control, value in (
            (self.shop_default, self._config.shop_default_enabled),
            (self.recipe_default, self._config.recipe_default_enabled),
        ):
            control.blockSignals(True)
            control.setChecked(value)
            control.blockSignals(False)

    def populate(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self._reset_buttons: dict[tuple[str, str], QPushButton] = {}
        catalog = _load_catalog(self._config)
        if catalog is None:
            self.tree.blockSignals(False)
            return
        recipes: dict[str, list[CatalogItem]] = defaultdict(list)
        for item in catalog.items.values():
            if item.recipe is not None:
                recipes[item.recipe.shop_id].append(item)
        icons = CatalogIconLoader(self._config.catalog_dir)
        for shop_id in sorted(recipes):
            shop = catalog.items.get(shop_id)
            parent = QTreeWidgetItem((shop.display_name if shop else shop_id, "Shop"))
            parent.setIcon(0, icons.icon_for(shop))
            parent.setData(0, Qt.ItemDataRole.UserRole, ("shop", shop_id))
            parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            parent.setCheckState(
                0,
                Qt.CheckState.Checked
                if self._config.shop_overrides.get(shop_id, self._config.shop_default_enabled)
                else Qt.CheckState.Unchecked,
            )
            self.tree.addTopLevelItem(parent)
            self._add_reset(parent, "shop", shop_id)
            for recipe in sorted(recipes[shop_id], key=lambda item: item.display_name):
                child = QTreeWidgetItem((recipe.display_name, "Recipe"))
                child.setIcon(0, icons.icon_for(recipe))
                child.setData(0, Qt.ItemDataRole.UserRole, ("recipe", recipe.game_id))
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(
                    0,
                    Qt.CheckState.Checked
                    if self._config.recipe_overrides.get(
                        recipe.game_id, self._config.recipe_default_enabled
                    )
                    else Qt.CheckState.Unchecked,
                )
                parent.addChild(child)
                self._add_reset(child, "recipe", recipe.game_id)
        self.tree.expandAll()
        self.tree.blockSignals(False)

    def _add_reset(self, item: QTreeWidgetItem, kind: str, key: str) -> None:
        overrides = self._config.shop_overrides if kind == "shop" else self._config.recipe_overrides
        reset = secondary_button("Use default")
        reset.setEnabled(key in overrides)
        reset.clicked.connect(
            lambda _checked=False, item_kind=kind, item_key=key: self.reset_item(
                item_kind, item_key
            )
        )
        self._reset_buttons[(kind, key)] = reset
        self.tree.setItemWidget(item, 2, reset)

    def _item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind, key = data
        enabled = item.checkState(0) is Qt.CheckState.Checked
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
        self._reset_buttons[(kind, key)].setEnabled(key in values)

    def _set_default(self, kind: str, value: bool) -> None:
        field = "shop_default_enabled" if kind == "shop" else "recipe_default_enabled"
        self._emit(**{field: value})
        self.populate()

    def reset_item(self, kind: str, key: str) -> None:
        field = "shop_overrides" if kind == "shop" else "recipe_overrides"
        values = dict(getattr(self._config, field))
        values.pop(key, None)
        self._emit(**{field: values})
        self.populate()


class BrowserPage(AppPage):
    config_edited = Signal(object)
    refresh_requested = Signal()
    restart_requested = Signal()

    def __init__(self, config: AppConfig) -> None:
        super().__init__(
            "Managed browser",
            "The bot only restarts a managed profile after confirmation.",
        )
        card = QFrame()
        card.setObjectName("card")
        form = QFormLayout(card)
        self.status_label = QLabel("Not checked")
        self.status_label.setWordWrap(True)
        self.browser_choice = QComboBox()
        self.browser_choice.addItems(("auto", "chrome", "edge", "brave", "chromium"))
        self.browser_choice.setCurrentText(config.browser)
        self.browser_choice.currentTextChanged.connect(
            lambda value: self.config_edited.emit(ConfigEdit({"browser": value}, "browser"))
        )
        form.addRow("Status", self.status_label)
        form.addRow("Preferred browser", self.browser_choice)
        self.page_layout.addWidget(card)
        buttons = QHBoxLayout()
        self.refresh_button = secondary_button("Refresh status")
        self.restart_button = QPushButton("Restart managed browser")
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.restart_button.clicked.connect(self.restart_requested)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.restart_button)
        buttons.addStretch()
        self.page_layout.addLayout(buttons)
        self.page_layout.addStretch()
        self._busy = False
        self._runtime_active = False

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.refresh_button.setEnabled(not busy)
        self.restart_button.setEnabled(not busy and not self._runtime_active)

    def set_runtime_active(self, active: bool) -> None:
        self._runtime_active = active
        self.restart_button.setEnabled(not active and not self._busy)


class SettingsPage(AppPage):
    config_edited = Signal(object)
    reset_requested = Signal()
    reset_all_requested = Signal()

    def __init__(self, config: AppConfig) -> None:
        super().__init__("Settings", "Changes validate and autosave automatically.")
        self._config = config
        self.controls: dict[str, QWidget] = {}
        self.opacity_labels: dict[str, QLabel] = {}
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Configuration"))
        self.saved_label = QLabel("Saved")
        self.saved_label.setObjectName("saveStatus")
        status_row.addWidget(self.saved_label)
        status_row.addStretch()
        reset = secondary_button("Reset settings")
        reset_all = secondary_button("Reset everything")
        reset.clicked.connect(self.reset_requested)
        reset_all.clicked.connect(self.reset_all_requested)
        status_row.addWidget(reset)
        status_row.addWidget(reset_all)
        self.page_layout.addLayout(status_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        sections = QVBoxLayout(content)
        sections.setContentsMargins(4, 4, 4, 4)
        sections.setSpacing(14)

        automation, form = self._section("Automation")
        self._add_int(form, "Reserved empty cells", "merge_empty_cell_reserve", 0, 50)
        self._add_int(form, "Producer claim open cells", "producer_claim_min_empty_cells", 1, 50)
        self._add_float(form, "Idle polling (seconds)", "idle_wait_seconds", 0, 3600)
        self._add_float(form, "Loop interval (seconds)", "loop_interval", 0.1, 60)
        self._add_float(form, "Item delay minimum", "item_action_delay_min", 0, 60)
        self._add_float(form, "Item delay maximum", "item_action_delay_max", 0, 60)
        self._add_float(form, "Crate delay minimum", "crate_delay_min", 0, 5)
        self._add_float(form, "Crate delay maximum", "crate_delay_max", 0, 5)
        sections.addWidget(automation)

        controls, form = self._section("Controls & startup")
        self._add_text(form, "Pause / resume hotkey", "pause_hotkey")
        self._add_text(form, "Quit hotkey", "quit_hotkey")
        for field, label in (
            ("browser_auto_launch", "Launch managed browser when needed"),
            ("start_paused", "Start automation paused"),
            ("start_minimized", "Start minimized to tray"),
            ("bot_autostart", "Start bot with application"),
            ("close_to_tray", "Close window to tray"),
        ):
            self._add_checkbox(form, label, field)
        sections.addWidget(controls)

        browser, form = self._section("Browser & assets")
        self._add_text(form, "Game URL", "game_url")
        self._add_text(form, "Page target", "window_title")
        self._add_int(form, "CDP port", "cdp_port", 1, 65535)
        self._add_path(form, "Browser executable", "browser_executable")
        self._add_path(form, "Browser profile directory", "browser_profile_dir")
        self._add_path(form, "Catalog directory", "catalog_dir", optional=False)
        self._add_path(form, "Atlas cache directory", "atlas_cache_dir", optional=False)
        sections.addWidget(browser)

        notifications, form = self._section("Notifications")
        webhook = QLineEdit()
        webhook.setEchoMode(QLineEdit.EchoMode.Password)
        webhook.setPlaceholderText("Optional Discord webhook URL")
        if config.discord_webhook_url:
            webhook.setText(config.discord_webhook_url.get_secret_value())
        webhook.editingFinished.connect(
            lambda: self._request("discord_webhook_url", webhook.text().strip() or None)
        )
        self.controls["discord_webhook_url"] = webhook
        form.addRow("Discord webhook", webhook)
        self._add_float(form, "Webhook status interval", "webhook_status_interval", 0, 3600)
        self._add_float(form, "Webhook summary interval", "webhook_summary_interval", 60, 86400)
        sections.addWidget(notifications)

        appearance, form = self._section("Appearance")
        theme = QComboBox()
        theme.addItems(("system", "dark", "light"))
        theme.setCurrentText(config.theme)
        theme.currentTextChanged.connect(lambda value: self._request("theme", value))
        self.controls["theme"] = theme
        form.addRow("Theme", theme)
        for field, label in (
            ("main_always_on_top", "Dashboard always on top"),
            ("overlay_always_on_top", "Overlay always on top"),
            ("overlay_click_through", "Overlay click-through while inactive"),
        ):
            self._add_checkbox(form, label, field)
        self._add_opacity(form, "Dashboard inactive opacity", "main_unfocused_opacity")
        self._add_opacity(form, "Dashboard focused opacity", "main_focused_opacity")
        self._add_opacity(form, "Overlay inactive opacity", "overlay_unfocused_opacity")
        self._add_opacity(form, "Overlay focused opacity", "overlay_focused_opacity")
        sections.addWidget(appearance)
        sections.addStretch()
        scroll.setWidget(content)
        self.page_layout.addWidget(scroll, 1)

    @staticmethod
    def _section(title: str) -> tuple[QGroupBox, QFormLayout]:
        section = QGroupBox(title)
        section.setMaximumWidth(900)
        form = QFormLayout(section)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setContentsMargins(14, 18, 14, 14)
        form.setSpacing(10)
        return section, form

    def _request(self, field: str, value: object) -> None:
        self.config_edited.emit(ConfigEdit({field: value}, field))

    def _add_checkbox(self, form: QFormLayout, label: str, field: str) -> None:
        control = QCheckBox()
        control.setChecked(bool(getattr(self._config, field)))
        control.setAccessibleName(label)
        control.toggled.connect(lambda value, name=field: self._request(name, value))
        self.controls[field] = control
        form.addRow(label, control)

    def _add_int(
        self,
        form: QFormLayout,
        label: str,
        field: str,
        minimum: int,
        maximum: int,
    ) -> None:
        control = QSpinBox()
        control.setRange(minimum, maximum)
        control.setValue(getattr(self._config, field))
        control.valueChanged.connect(lambda value, name=field: self._request(name, value))
        self.controls[field] = control
        form.addRow(label, control)

    def _add_float(
        self, form: QFormLayout, label: str, field: str, minimum: float, maximum: float
    ) -> None:
        control = QDoubleSpinBox()
        control.setRange(minimum, maximum)
        control.setDecimals(2)
        control.setValue(getattr(self._config, field))
        control.valueChanged.connect(lambda value, name=field: self._request(name, value))
        self.controls[field] = control
        form.addRow(label, control)

    def _add_text(self, form: QFormLayout, label: str, field: str) -> None:
        control = QLineEdit(str(getattr(self._config, field)))
        control.editingFinished.connect(
            lambda widget=control, name=field: self._request(name, widget.text().strip())
        )
        self.controls[field] = control
        form.addRow(label, control)

    def _add_path(
        self, form: QFormLayout, label: str, field: str, *, optional: bool = True
    ) -> None:
        value = getattr(self._config, field)
        control = QLineEdit(str(value) if value is not None else "")
        browse = secondary_button("Browse…")
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
        form.addRow(label, row)

    def _add_opacity(self, form: QFormLayout, label: str, field: str) -> None:
        control = QSlider(Qt.Orientation.Horizontal)
        control.setRange(25, 100)
        control.setValue(round(getattr(self._config, field) * 100))
        value_label = QLabel(f"{control.value()}%")
        value_label.setMinimumWidth(42)
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
        form.addRow(label, row)

    def show_validation_error(self, field: str | None, message: str, config: AppConfig) -> None:
        self.saved_label.setText(f"Invalid: {message}")
        if field is None or field not in self.controls:
            return
        control = self.controls[field]
        self._set_control_value(field, control, getattr(config, field))
        control.setProperty("invalid", True)
        control.setToolTip(message)
        control.setAccessibleDescription(message)
        control.style().unpolish(control)
        control.style().polish(control)
        control.setFocus()

    def mark_saving(self, field: str | None = None) -> None:
        self.saved_label.setText("Saving…")
        if field is not None and field in self.controls:
            control = self.controls[field]
            control.setProperty("invalid", False)
            control.setToolTip("")
            control.setAccessibleDescription("")
            control.style().unpolish(control)
            control.style().polish(control)

    def apply_config(self, config: AppConfig) -> None:
        self._config = config
        for field, control in self.controls.items():
            self._set_control_value(field, control, getattr(config, field))
        self.saved_label.setText("Saved")

    def _set_control_value(self, field: str, control: QWidget, value: object) -> None:
        control.blockSignals(True)
        if isinstance(control, QCheckBox):
            control.setChecked(bool(value))
        elif isinstance(control, (QSpinBox, QDoubleSpinBox)):
            control.setValue(value)  # type: ignore[arg-type]
        elif isinstance(control, QSlider):
            percent = round(float(str(value)) * 100)
            control.setValue(percent)
            self.opacity_labels[field].setText(f"{percent}%")
        elif isinstance(control, QComboBox):
            control.setCurrentText(str(value))
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
        self.filter = QComboBox()
        self.filter.addItems(("DEBUG", "INFO", "WARNING", "ERROR"))
        self.filter.setCurrentText(log_level)
        self.filter.currentTextChanged.connect(self.log_level_changed)
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
        minimum = getattr(logging, self.filter.currentText(), logging.INFO)
        if levelno >= minimum:
            self.view.appendPlainText(f"[{timestamp}] {level:<8} {message}")

    def clear(self) -> None:
        self.view.clear()
