"""GUI-first Farm Merge Valet desktop application."""

from __future__ import annotations

import logging
import signal
import sys
from collections import defaultdict
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import QEvent, QModelIndex, QPersistentModelIndex, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from farm_merge_valet.config import AppConfig, ConfigStore, ItemPolicyOverride
from farm_merge_valet.core.item_catalog import (
    ItemCatalog,
    TileClaimMode,
    load_item_catalog,
)
from farm_merge_valet.gui.assets import CatalogIconLoader
from farm_merge_valet.gui.controller import ApplicationController, ApplicationStatus
from farm_merge_valet.gui.theme import apply_theme
from farm_merge_valet.logging_setup import configure_logging, logging_sink

logger = logging.getLogger(__name__)

_ITEM_ICON_SIZE = QSize(40, 40)
_SHOP_TREE_ICON_SIZE = QSize(100, 54)


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


def _app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#238636"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(18, 16, 18, 32)
    painter.drawEllipse(30, 16, 18, 32)
    painter.end()
    return QIcon(pixmap)


def _secondary(button: QPushButton) -> QPushButton:
    button.setProperty("secondary", True)
    return button


def _page(title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
    page = QWidget()
    layout = QVBoxLayout(page)
    heading = QLabel(title)
    heading.setFont(QFont("Segoe UI", 20, QFont.Weight.DemiBold))
    layout.addWidget(heading)
    if subtitle:
        description = QLabel(subtitle)
        description.setWordWrap(True)
        description.setStyleSheet("color: #8b949e; margin-bottom: 8px;")
        layout.addWidget(description)
    return page, layout


class CompactOverlay(QMainWindow):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self.setWindowTitle("Farm Merge Valet · Compact")
        self.status = QLabel("Stopped")
        self.status.setFont(QFont("Segoe UI", 13, QFont.Weight.DemiBold))
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(30)
        self.logs.setMinimumSize(440, 160)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self.status)
        layout.addWidget(self.logs)
        self.setCentralWidget(root)
        self.resize(480, 220)
        self.apply_config(config)

    def apply_config(self, config: AppConfig) -> None:
        self._config = config
        always_on_top = Qt.WindowType.WindowStaysOnTopHint
        if bool(self.windowFlags() & always_on_top) != config.overlay_always_on_top:
            visible = self.isVisible()
            self.setWindowFlag(always_on_top, config.overlay_always_on_top)
            if visible:
                self.show()
        self._apply_focus_state()

    def _apply_focus_state(self) -> None:
        self.setWindowOpacity(
            self._config.overlay_focused_opacity
            if self.isActiveWindow()
            else self._config.overlay_unfocused_opacity
        )
        if sys.platform == "win32":
            from farm_merge_valet.gui.overlay import _set_click_through

            _set_click_through(
                int(self.winId()),
                self._config.overlay_click_through and not self.isActiveWindow(),
            )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange:
            self._apply_focus_state()

    def append_log(self, timestamp: str, level: str, message: str, _levelno: int) -> None:
        self.logs.appendPlainText(f"[{timestamp}] {level:<8} {message}")


class MainWindow(QMainWindow):
    _NAVIGATION = ("Dashboard", "Items", "Shops", "Browser", "Settings", "Logs")

    def __init__(self, controller: ApplicationController) -> None:
        super().__init__()
        self.controller = controller
        self._draft = controller.config
        self._really_quit = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self._flush_config)
        self.setWindowTitle("Farm Merge Valet")
        self.setWindowIcon(_app_icon())
        self.resize(1080, 700)

        root = QWidget()
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        self.navigation = QListWidget()
        self.navigation.setFixedWidth(170)
        self.navigation.addItems(self._NAVIGATION)
        self.pages = QStackedWidget()
        shell.addWidget(self.navigation)
        shell.addWidget(self.pages, 1)
        self.setCentralWidget(root)

        self.pages.addWidget(self._build_dashboard())
        self.pages.addWidget(self._build_items())
        self.pages.addWidget(self._build_shops())
        self.pages.addWidget(self._build_browser())
        self.pages.addWidget(self._build_settings())
        self.pages.addWidget(self._build_logs())
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)

        self.overlay = CompactOverlay(self._draft)
        self._setup_tray()
        controller.status_changed.connect(self._status_changed)
        controller.running_changed.connect(self._running_changed)
        controller.log_received.connect(self._append_log)
        controller.config_changed.connect(self._config_changed)
        controller.error.connect(self._show_error)
        controller.application_quit_requested.connect(self.quit_application)
        self._apply_appearance()

    def _build_dashboard(self) -> QWidget:
        page, layout = _page("Dashboard", "Control automation and review live state.")
        card = QFrame()
        card.setObjectName("card")
        form = QFormLayout(card)
        self.mode_value = QLabel("Stopped")
        self.browser_value = QLabel("Not checked")
        self.runtime_value = QLabel("Waiting")
        self.phase_value = QLabel("—")
        self.activity_value = QLabel("No activity yet")
        self.activity_value.setWordWrap(True)
        for label, widget in (
            ("Mode", self.mode_value),
            ("Browser", self.browser_value),
            ("Runtime", self.runtime_value),
            ("Phase", self.phase_value),
            ("Last activity", self.activity_value),
        ):
            form.addRow(label, widget)
        layout.addWidget(card)
        controls = QHBoxLayout()
        self.start_button = QPushButton("Start")
        self.pause_button = _secondary(QPushButton("Pause / Resume"))
        self.stop_button = _secondary(QPushButton("Stop"))
        self.overlay_button = _secondary(QPushButton("Show compact overlay"))
        self.start_button.clicked.connect(self.controller.start_bot)
        self.pause_button.clicked.connect(self.controller.toggle_pause)
        self.stop_button.clicked.connect(self.controller.stop_bot)
        self.overlay_button.clicked.connect(self._toggle_overlay)
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        for button in (self.start_button, self.pause_button, self.stop_button):
            controls.addWidget(button)
        controls.addStretch()
        controls.addWidget(self.overlay_button)
        layout.addLayout(controls)
        layout.addStretch()
        return page

    def _load_catalog(self) -> ItemCatalog | None:
        path = self._draft.catalog_dir / "catalog.json"
        try:
            return load_item_catalog(path) if path.is_file() else None
        except (OSError, ValueError) as exc:
            logger.warning("Could not load the GUI item catalog: %s", exc)
            return None

    def _build_items(self) -> QWidget:
        page, layout = _page(
            "Item policies",
            "Controls inherit global, category, and item defaults. Ingredient and train-ticket "
            "claims are enabled by default.",
        )
        defaults = QHBoxLayout()
        for field, label in (
            ("enabled", "Automation"),
            ("merge", "Merge"),
            ("prefer_merge_five", "Merge five"),
            ("claim", "Claim"),
        ):
            checkbox = QCheckBox(f"Default {label}")
            checkbox.setChecked(getattr(self._draft.item_policy_defaults, field))
            checkbox.toggled.connect(lambda value, name=field: self._set_item_default(name, value))
            defaults.addWidget(checkbox)
        defaults.addStretch()
        reset = _secondary(QPushButton("Reset item policies"))
        reset.clicked.connect(self._reset_item_policies)
        defaults.addWidget(reset)
        layout.addLayout(defaults)
        self.item_search = QLineEdit()
        self.item_search.setPlaceholderText("Search item families…")
        layout.addWidget(self.item_search)
        self.item_table = QTableWidget(0, 7)
        self.item_table.setHorizontalHeaderLabels(
            ("Item family", "Category", "Enabled", "Merge", "Merge five", "Claim", "Override")
        )
        self.item_table.setIconSize(_ITEM_ICON_SIZE)
        self.item_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.item_table.setAlternatingRowColors(True)
        layout.addWidget(self.item_table, 1)
        self.item_search.textChanged.connect(self._filter_items)
        self._populate_items()
        return page

    def _populate_items(self) -> None:
        self.item_table.clearContents()
        self.item_table.clearSpans()
        self._item_reset_buttons: dict[str, QPushButton] = {}
        catalog = self._load_catalog()
        if catalog is None:
            self.item_table.setRowCount(1)
            self.item_table.setItem(
                0,
                0,
                QTableWidgetItem("Catalog unavailable — load the game and sync assets"),
            )
            self.item_table.setSpan(0, 0, 1, 7)
            return
        grouped: dict[str, list] = defaultdict(list)
        for item in catalog.items.values():
            grouped[item.policy_key].append(item)
        rows = []
        for key, items in grouped.items():
            supports_merge = any(item.automation_item is not None for item in items)
            supports_claim = any(
                item.tile_claim_mode is TileClaimMode.IMMEDIATE
                or (item.category in {"animals", "crops"} and item.tier == 4)
                for item in items
            )
            if not (supports_merge or supports_claim):
                continue
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
        icons = CatalogIconLoader(self._draft.catalog_dir)
        self.item_table.setRowCount(len(rows))
        for row, (key, name, category, supports_merge, supports_claim) in enumerate(rows):
            representative = min(grouped[key], key=lambda item: item.tier or 0)
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.ItemDataRole.UserRole, key)
            name_item.setIcon(icons.icon_for(representative))
            self.item_table.setItem(row, 0, name_item)
            self.item_table.setRowHeight(row, 48)
            self.item_table.setItem(row, 1, QTableWidgetItem(category.replace("_", " ").title()))
            policy = self._draft.item_policy(key)
            for column, field, applicable in (
                (2, "enabled", True),
                (3, "merge", supports_merge),
                (4, "prefer_merge_five", supports_merge),
                (5, "claim", supports_claim),
            ):
                checkbox = QCheckBox()
                checkbox.setChecked(getattr(policy, field))
                checkbox.setEnabled(applicable)
                checkbox.setToolTip(
                    "Not applicable to this item"
                    if not applicable
                    else "Uses the effective default unless this item has an override"
                )
                checkbox.toggled.connect(
                    lambda value, policy_key=key, name=field: self._set_item_override(
                        policy_key, name, value
                    )
                )
                container = QWidget()
                cell_layout = QHBoxLayout(container)
                cell_layout.setContentsMargins(0, 0, 0, 0)
                cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell_layout.addWidget(checkbox)
                self.item_table.setCellWidget(row, column, container)
            reset = _secondary(QPushButton("Use default"))
            reset.setEnabled(key in self._draft.item_policy_overrides)
            reset.clicked.connect(
                lambda _checked=False, policy_key=key: self._reset_item(policy_key)
            )
            self._item_reset_buttons[key] = reset
            self.item_table.setCellWidget(row, 6, reset)

    def _filter_items(self, text: str) -> None:
        needle = text.casefold().strip()
        for row in range(self.item_table.rowCount()):
            cells = [self.item_table.item(row, column) for column in (0, 1)]
            content = " ".join(cell.text() for cell in cells if cell is not None)
            self.item_table.setRowHidden(row, needle not in content.casefold())

    def _set_item_default(self, field: str, value: bool) -> None:
        defaults = self._draft.item_policy_defaults.model_copy(update={field: value})
        self._queue_config(item_policy_defaults=defaults)
        self._populate_items()

    def _set_item_override(self, key: str, field: str, value: bool) -> None:
        overrides = dict(self._draft.item_policy_overrides)
        values = overrides.get(key, ItemPolicyOverride()).model_dump(exclude_none=True)
        default_value = getattr(self._draft.item_policy_default(key), field)
        if value == default_value:
            values.pop(field, None)
        else:
            values[field] = value
        if values:
            overrides[key] = ItemPolicyOverride.model_validate(values)
        else:
            overrides.pop(key, None)
        self._queue_config(item_policy_overrides=overrides)
        self._item_reset_buttons[key].setEnabled(key in overrides)

    def _reset_item(self, key: str) -> None:
        overrides = dict(self._draft.item_policy_overrides)
        overrides.pop(key, None)
        self._queue_config(item_policy_overrides=overrides)
        self._populate_items()

    def _reset_item_policies(self) -> None:
        if not self._confirm_reset(
            "Reset item policies?",
            "Restore item, category, and global item defaults and remove all item overrides?",
        ):
            return
        defaults = AppConfig()
        self._replace_config(
            self._draft.model_copy(
                update={
                    "item_policy_defaults": defaults.item_policy_defaults,
                    "item_category_defaults": defaults.item_category_defaults,
                    "item_default_overrides": defaults.item_default_overrides,
                    "item_policy_overrides": {},
                }
            ),
            {1},
        )

    def _build_shops(self) -> QWidget:
        page, layout = _page("Shops", "Configure discovered shops and recipes independently.")
        defaults = QHBoxLayout()
        self.shop_default = QCheckBox("Enable shops by default")
        self.recipe_default = QCheckBox("Enable recipes by default")
        self.shop_default.setChecked(self._draft.shop_default_enabled)
        self.recipe_default.setChecked(self._draft.recipe_default_enabled)
        self.shop_default.toggled.connect(lambda value: self._set_shop_default("shop", value))
        self.recipe_default.toggled.connect(lambda value: self._set_shop_default("recipe", value))
        defaults.addWidget(self.shop_default)
        defaults.addWidget(self.recipe_default)
        defaults.addStretch()
        reset = _secondary(QPushButton("Reset shop policies"))
        reset.clicked.connect(self._reset_shop_policies)
        defaults.addWidget(reset)
        layout.addLayout(defaults)
        self.shop_tree = QTreeWidget()
        self.shop_tree.setHeaderLabels(("Shop / recipe", "Type", "Override"))
        self.shop_tree.setIconSize(_SHOP_TREE_ICON_SIZE)
        self.shop_tree.setItemDelegate(_ShopIconDelegate(self.shop_tree))
        self.shop_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.shop_tree, 1)
        self._populate_shops()
        self.shop_tree.itemChanged.connect(self._shop_changed)
        return page

    def _populate_shops(self) -> None:
        self.shop_tree.clear()
        self._shop_reset_buttons: dict[tuple[str, str], QPushButton] = {}
        catalog = self._load_catalog()
        if catalog is None:
            return
        recipes: dict[str, list] = defaultdict(list)
        for item in catalog.items.values():
            if item.recipe is not None:
                recipes[item.recipe.shop_id].append(item)
        icons = CatalogIconLoader(self._draft.catalog_dir)
        self.shop_tree.blockSignals(True)
        for shop_id in sorted(recipes):
            shop = catalog.items.get(shop_id)
            parent = QTreeWidgetItem((shop.display_name if shop else shop_id, "Shop"))
            parent.setIcon(0, icons.icon_for(shop))
            parent.setData(0, Qt.ItemDataRole.UserRole, ("shop", shop_id))
            parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            parent.setCheckState(
                0,
                (
                    Qt.CheckState.Checked
                    if self._draft.shop_overrides.get(shop_id, self._draft.shop_default_enabled)
                    else Qt.CheckState.Unchecked
                ),
            )
            self.shop_tree.addTopLevelItem(parent)
            self._add_shop_reset_button(parent, "shop", shop_id)
            for recipe in sorted(recipes[shop_id], key=lambda item: item.display_name):
                child = QTreeWidgetItem((recipe.display_name, "Recipe"))
                child.setIcon(0, icons.icon_for(recipe))
                child.setData(0, Qt.ItemDataRole.UserRole, ("recipe", recipe.game_id))
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(
                    0,
                    (
                        Qt.CheckState.Checked
                        if self._draft.recipe_overrides.get(
                            recipe.game_id, self._draft.recipe_default_enabled
                        )
                        else Qt.CheckState.Unchecked
                    ),
                )
                parent.addChild(child)
                self._add_shop_reset_button(child, "recipe", recipe.game_id)
        self.shop_tree.blockSignals(False)

    def _add_shop_reset_button(self, item: QTreeWidgetItem, kind: str, key: str) -> None:
        overrides = self._draft.shop_overrides if kind == "shop" else self._draft.recipe_overrides
        reset = _secondary(QPushButton("Use default"))
        reset.setEnabled(key in overrides)
        reset.clicked.connect(
            lambda _checked=False, item_kind=kind, item_key=key: self._reset_shop_item(
                item_kind, item_key
            )
        )
        self._shop_reset_buttons[(kind, key)] = reset
        self.shop_tree.setItemWidget(item, 2, reset)

    def _shop_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind, key = data
        enabled = item.checkState(0) is Qt.CheckState.Checked
        if kind == "shop":
            values = dict(self._draft.shop_overrides)
            default = self._draft.shop_default_enabled
            field = "shop_overrides"
        else:
            values = dict(self._draft.recipe_overrides)
            default = self._draft.recipe_default_enabled
            field = "recipe_overrides"
        if enabled == default:
            values.pop(key, None)
        else:
            values[key] = enabled
        self._queue_config(**{field: values})
        self._shop_reset_buttons[(kind, key)].setEnabled(key in values)

    def _set_shop_default(self, kind: str, value: bool) -> None:
        field = "shop_default_enabled" if kind == "shop" else "recipe_default_enabled"
        self._queue_config(**{field: value})
        self._populate_shops()

    def _reset_shop_item(self, kind: str, key: str) -> None:
        field = "shop_overrides" if kind == "shop" else "recipe_overrides"
        values = dict(getattr(self._draft, field))
        values.pop(key, None)
        self._queue_config(**{field: values})
        self._populate_shops()

    def _reset_shop_policies(self) -> None:
        if not self._confirm_reset(
            "Reset shop policies?",
            "Restore shop and recipe defaults and remove all shop and recipe overrides?",
        ):
            return
        defaults = AppConfig()
        self._replace_config(
            self._draft.model_copy(
                update={
                    "shop_default_enabled": defaults.shop_default_enabled,
                    "recipe_default_enabled": defaults.recipe_default_enabled,
                    "shop_overrides": {},
                    "recipe_overrides": {},
                }
            ),
            {2},
        )

    def _build_browser(self) -> QWidget:
        page, layout = _page(
            "Managed browser",
            "The bot only restarts a managed profile after confirmation.",
        )
        card = QFrame()
        card.setObjectName("card")
        form = QFormLayout(card)
        self.browser_status_label = QLabel("Not checked")
        self.browser_choice = QComboBox()
        self.browser_choice.addItems(("auto", "chrome", "edge", "brave", "chromium"))
        self.browser_choice.setCurrentText(self._draft.browser)
        self.browser_choice.currentTextChanged.connect(
            lambda value: self._queue_config(browser=value)
        )
        form.addRow("Status", self.browser_status_label)
        form.addRow("Preferred browser", self.browser_choice)
        layout.addWidget(card)
        buttons = QHBoxLayout()
        check = _secondary(QPushButton("Refresh status"))
        restart = QPushButton("Restart managed browser")
        check.clicked.connect(self._refresh_browser)
        restart.clicked.connect(self._confirm_browser_restart)
        buttons.addWidget(check)
        buttons.addWidget(restart)
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addStretch()
        return page

    def _refresh_browser(self) -> None:
        try:
            self.browser_status_label.setText(self.controller.browser_status())
        except Exception as exc:
            self.browser_status_label.setText(str(exc))

    def _confirm_browser_restart(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Restart managed browser?",
                "This closes only the verified Farm Merge Valet browser profile. Continue?",
            )
            is QMessageBox.StandardButton.Yes
        ):
            self.controller.restart_browser()
            self._refresh_browser()

    def _build_settings(self) -> QWidget:
        page, page_layout = _page("Settings", "Changes validate and autosave automatically.")
        reset_controls = QHBoxLayout()
        reset_controls.addStretch()
        reset_settings = _secondary(QPushButton("Reset settings"))
        reset_all = _secondary(QPushButton("Reset everything"))
        reset_settings.clicked.connect(self._reset_general_settings)
        reset_all.clicked.connect(self._reset_all_settings)
        reset_controls.addWidget(reset_settings)
        reset_controls.addWidget(reset_all)
        page_layout.addLayout(reset_controls)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        self.saved_label = QLabel("Saved")
        form.addRow("Configuration", self.saved_label)

        self._add_int(form, "Reserved empty cells", "merge_empty_cell_reserve", 0, 50)
        self._add_int(form, "Producer claim open cells", "producer_claim_min_empty_cells", 1, 50)
        self._add_float(form, "Idle polling (seconds)", "idle_wait_seconds", 0, 3600)
        self._add_float(form, "Loop interval (seconds)", "loop_interval", 0.1, 60)
        self._add_float(form, "Item delay minimum", "item_action_delay_min", 0, 60)
        self._add_float(form, "Item delay maximum", "item_action_delay_max", 0, 60)
        self._add_float(form, "Crate delay minimum", "crate_delay_min", 0, 5)
        self._add_float(form, "Crate delay maximum", "crate_delay_max", 0, 5)
        self._add_text(form, "Pause / resume hotkey", "pause_hotkey")
        self._add_text(form, "Quit hotkey", "quit_hotkey")
        self._add_text(form, "Game URL", "game_url")
        self._add_text(form, "Page target", "window_title")
        self._add_int(form, "CDP port", "cdp_port", 1, 65535)
        self._add_path(form, "Browser executable", "browser_executable")
        self._add_path(form, "Browser profile directory", "browser_profile_dir")
        self._add_path(form, "Catalog directory", "catalog_dir", optional=False)
        self._add_path(form, "Atlas cache directory", "atlas_cache_dir", optional=False)

        webhook = QLineEdit()
        webhook.setEchoMode(QLineEdit.EchoMode.Password)
        webhook.setPlaceholderText("Optional Discord webhook URL")
        if self._draft.discord_webhook_url:
            webhook.setText(self._draft.discord_webhook_url.get_secret_value())
        webhook.editingFinished.connect(
            lambda: self._queue_config(discord_webhook_url=webhook.text().strip() or None)
        )
        form.addRow("Discord webhook", webhook)
        self._add_float(form, "Webhook status interval", "webhook_status_interval", 0, 3600)
        self._add_float(form, "Webhook summary interval", "webhook_summary_interval", 60, 86400)

        theme = QComboBox()
        theme.addItems(("system", "dark", "light"))
        theme.setCurrentText(self._draft.theme)
        theme.currentTextChanged.connect(lambda value: self._queue_config(theme=value))
        form.addRow("Theme", theme)
        for field, label in (
            ("browser_auto_launch", "Launch managed browser when needed"),
            ("start_minimized", "Start minimized to tray"),
            ("bot_autostart", "Start bot with application"),
            ("close_to_tray", "Close window to tray"),
            ("main_always_on_top", "Dashboard always on top"),
            ("overlay_always_on_top", "Overlay always on top"),
            ("overlay_click_through", "Overlay click-through while inactive"),
        ):
            checkbox = QCheckBox()
            checkbox.setChecked(getattr(self._draft, field))
            checkbox.toggled.connect(lambda value, name=field: self._queue_config(**{name: value}))
            form.addRow(label, checkbox)
        self._add_opacity(form, "Dashboard inactive opacity", "main_unfocused_opacity")
        self._add_opacity(form, "Dashboard focused opacity", "main_focused_opacity")
        self._add_opacity(form, "Overlay inactive opacity", "overlay_unfocused_opacity")
        self._add_opacity(form, "Overlay focused opacity", "overlay_focused_opacity")
        scroll.setWidget(content)
        page_layout.addWidget(scroll, 1)
        return page

    def _add_int(
        self, form: QFormLayout, label: str, field: str, minimum: int, maximum: int
    ) -> None:
        control = QSpinBox()
        control.setRange(minimum, maximum)
        control.setValue(getattr(self._draft, field))
        control.valueChanged.connect(lambda value, name=field: self._queue_config(**{name: value}))
        form.addRow(label, control)

    def _add_float(
        self, form: QFormLayout, label: str, field: str, minimum: float, maximum: float
    ) -> None:
        control = QDoubleSpinBox()
        control.setRange(minimum, maximum)
        control.setDecimals(2)
        control.setValue(getattr(self._draft, field))
        control.valueChanged.connect(lambda value, name=field: self._queue_config(**{name: value}))
        form.addRow(label, control)

    def _add_text(self, form: QFormLayout, label: str, field: str) -> None:
        control = QLineEdit(str(getattr(self._draft, field)))
        control.editingFinished.connect(
            lambda widget=control, name=field: self._queue_config(**{name: widget.text().strip()})
        )
        form.addRow(label, control)

    def _add_path(
        self, form: QFormLayout, label: str, field: str, *, optional: bool = True
    ) -> None:
        value = getattr(self._draft, field)
        control = QLineEdit(str(value) if value is not None else "")

        def update() -> None:
            text = control.text().strip()
            self._queue_config(**{field: Path(text) if text else (None if optional else value)})

        control.editingFinished.connect(update)
        form.addRow(label, control)

    def _add_opacity(self, form: QFormLayout, label: str, field: str) -> None:
        control = QSlider(Qt.Orientation.Horizontal)
        control.setRange(25, 100)
        control.setValue(round(getattr(self._draft, field) * 100))
        control.valueChanged.connect(
            lambda value, name=field: self._queue_config(**{name: value / 100})
        )
        form.addRow(label, control)

    def _build_logs(self) -> QWidget:
        page, layout = _page(
            "Logs", "Structured application events from the shared logging pipeline."
        )
        toolbar = QHBoxLayout()
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setFont(QFont("Cascadia Mono", 10))
        self.log_filter = QComboBox()
        self.log_filter.addItems(("DEBUG", "INFO", "WARNING", "ERROR"))
        self.log_filter.setCurrentText(self._draft.log_level)
        self.log_filter.currentTextChanged.connect(
            lambda value: self._queue_config(log_level=value)
        )
        clear = _secondary(QPushButton("Clear"))
        clear.clicked.connect(lambda: self.log_view.clear())
        toolbar.addWidget(QLabel("Minimum level"))
        toolbar.addWidget(self.log_filter)
        toolbar.addStretch()
        toolbar.addWidget(clear)
        layout.addLayout(toolbar)
        layout.addWidget(self.log_view, 1)
        return page

    def _confirm_reset(self, title: str, message: str) -> bool:
        return (
            QMessageBox.question(
                self,
                title,
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            is QMessageBox.StandardButton.Yes
        )

    def _reset_general_settings(self) -> None:
        if not self._confirm_reset(
            "Reset settings?",
            "Restore browser, automation timing, notification, and appearance settings? "
            "Item and shop policies will be preserved.",
        ):
            return
        policy_fields = {
            "item_policy_defaults",
            "item_category_defaults",
            "item_default_overrides",
            "item_policy_overrides",
            "shop_default_enabled",
            "recipe_default_enabled",
            "shop_overrides",
            "recipe_overrides",
        }
        defaults = AppConfig()
        changes = {
            field: getattr(defaults, field)
            for field in AppConfig.model_fields
            if field != "schema_version" and field not in policy_fields
        }
        self._replace_config(self._draft.model_copy(update=changes), {3, 4})

    def _reset_all_settings(self) -> None:
        if not self._confirm_reset(
            "Reset everything?",
            "Restore every Farm Merge Valet setting and remove all item, shop, and recipe "
            "overrides?",
        ):
            return
        self._replace_config(AppConfig(), {1, 2, 3, 4})

    def _replace_config(self, config: AppConfig, page_indexes: set[int]) -> None:
        self._save_timer.stop()
        try:
            self.controller.store.replace(config)
        except (OSError, ValueError) as exc:
            self._show_error(f"Could not reset settings: {exc}")
            return
        configure_logging(config.log_level)
        self.overlay.setVisible(config.overlay_visible)
        self.overlay_button.setText(
            "Hide compact overlay" if config.overlay_visible else "Show compact overlay"
        )
        self._rebuild_pages(page_indexes)

    def _rebuild_pages(self, page_indexes: set[int]) -> None:
        builders = {
            1: self._build_items,
            2: self._build_shops,
            3: self._build_browser,
            4: self._build_settings,
        }
        current = self.navigation.currentRow()
        for index in sorted(page_indexes):
            old_page = self.pages.widget(index)
            if old_page is None:
                continue
            self.pages.removeWidget(old_page)
            self.pages.insertWidget(index, builders[index]())
            old_page.deleteLater()
        self.navigation.setCurrentRow(current)

    def _queue_config(self, **changes: object) -> None:
        try:
            candidate = self._draft.model_copy(update=changes)
            self._draft = AppConfig.model_validate(candidate.model_dump())
        except ValidationError as exc:
            self.saved_label.setText(f"Invalid: {exc.errors()[0]['msg']}")
            return
        self.saved_label.setText("Saving…")
        self._save_timer.start()
        self._apply_appearance()

    def _flush_config(self) -> None:
        try:
            self.controller.store.replace(self._draft)
        except (OSError, ValueError) as exc:
            self.saved_label.setText("Save failed")
            self._show_error(f"Could not save settings: {exc}")
            return
        self.saved_label.setText("Saved")
        configure_logging(self._draft.log_level)

    def _config_changed(self, config: AppConfig) -> None:
        self._draft = config
        self.overlay.apply_config(config)
        self._apply_appearance()

    def _apply_appearance(self) -> None:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app, self._draft.theme)
        always_on_top = Qt.WindowType.WindowStaysOnTopHint
        if bool(self.windowFlags() & always_on_top) != self._draft.main_always_on_top:
            visible = self.isVisible()
            self.setWindowFlag(always_on_top, self._draft.main_always_on_top)
            if visible:
                self.show()
        self._apply_main_opacity()

    def _apply_main_opacity(self) -> None:
        self.setWindowOpacity(
            self._draft.main_focused_opacity
            if self.isActiveWindow()
            else self._draft.main_unfocused_opacity
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange:
            self._apply_main_opacity()

    def _status_changed(self, status: ApplicationStatus) -> None:
        self.mode_value.setText(status.mode)
        self.browser_value.setText(status.browser)
        self.runtime_value.setText(status.runtime)
        self.phase_value.setText(status.phase)
        self.activity_value.setText(status.last_activity)
        self.overlay.status.setText(f"{status.mode} · {status.phase}")
        if hasattr(self, "tray"):
            self.tray.setToolTip(f"Farm Merge Valet — {status.mode}")

    def _running_changed(self, running: bool, _paused: bool) -> None:
        self.start_button.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.stop_button.setEnabled(running)

    def _append_log(self, timestamp: str, level: str, message: str, levelno: int) -> None:
        minimum = getattr(logging, self.log_filter.currentText(), logging.INFO)
        if levelno >= minimum:
            self.log_view.appendPlainText(f"[{timestamp}] {level:<8} {message}")
        self.overlay.append_log(timestamp, level, message, levelno)

    def _toggle_overlay(self) -> None:
        visible = not self.overlay.isVisible()
        self.overlay.setVisible(visible)
        self.overlay_button.setText("Hide compact overlay" if visible else "Show compact overlay")
        self._queue_config(overlay_visible=visible)

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(_app_icon(), self)
        menu = self.tray.contextMenu()
        if menu is None:
            from PySide6.QtWidgets import QMenu

            menu = QMenu(self)
            self.tray.setContextMenu(menu)
        show_action = QAction("Show dashboard", self)
        start_action = QAction("Start bot", self)
        pause_action = QAction("Pause / resume", self)
        overlay_action = QAction("Toggle compact overlay", self)
        quit_action = QAction("Quit", self)
        show_action.triggered.connect(self._show_dashboard)
        start_action.triggered.connect(self.controller.start_bot)
        pause_action.triggered.connect(self.controller.toggle_pause)
        overlay_action.triggered.connect(self._toggle_overlay)
        quit_action.triggered.connect(self.quit_application)
        menu.addActions((show_action, start_action, pause_action, overlay_action))
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.activated.connect(lambda _reason: self._show_dashboard())
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _show_dashboard(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Farm Merge Valet", message)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._really_quit and self._draft.close_to_tray and self.tray.isVisible():
            event.ignore()
            self.hide()
            return
        self.controller.shutdown()
        event.accept()

    def quit_application(self) -> None:
        self._really_quit = True
        self.controller.shutdown()
        QApplication.quit()


def run_application(store: ConfigStore | None = None) -> int:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv)
    app.setApplicationName("Farm Merge Valet")
    app.setWindowIcon(_app_icon())
    app.setQuitOnLastWindowClosed(False)
    config_store = store or ConfigStore()
    try:
        config = config_store.load()
    except (OSError, ValueError) as exc:
        answer = QMessageBox.critical(
            None,
            "Configuration could not be loaded",
            f"{exc}\n\nReset to safe defaults? The existing file will not be overwritten "
            "unless you choose Reset.",
            QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer is not QMessageBox.StandardButton.Reset:
            return 2
        config = config_store.reset()
    configure_logging(config.log_level)
    apply_theme(app, config.theme)
    controller = ApplicationController(config_store)
    window = MainWindow(controller)
    signal.signal(signal.SIGINT, lambda *_args: window.quit_application())
    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(lambda: None)
    timer.start()
    with logging_sink(controller.log_handler):
        if config.start_minimized and window.tray.isVisible():
            window.hide()
        else:
            window.show()
        if config.overlay_visible:
            window.overlay.show()
        if config.bot_autostart:
            controller.start_bot()
        return app.exec()
