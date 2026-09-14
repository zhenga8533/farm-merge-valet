from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QGroupBox,
    QLabel,
    QPushButton,
)

from farm_merge_valet.catalog.models import CatalogItem, ItemCatalog, RecipeMetadata
from farm_merge_valet.catalog.store import write_item_catalog
from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.gui.components.hotkey_edit import HotkeyEdit
from farm_merge_valet.gui.controller import (
    ApplicationController,
)
from farm_merge_valet.gui.main_window import MainWindow


def _catalog() -> ItemCatalog:
    return ItemCatalog(
        {
            "wheat_1": CatalogItem(
                "wheat_1",
                "wheat",
                "crops/wheat",
                "crops",
                "Wheat",
                1,
                True,
                "wheat_2",
                None,
                None,
                frozenset({"mergeable"}),
            ),
            "milk": CatalogItem(
                "milk",
                "milk",
                "ingredients/milk",
                "ingredients",
                "Milk",
                None,
                False,
                None,
                None,
                None,
                frozenset({"collectable", "ingredient"}),
            ),
            "coin_1": CatalogItem(
                "coin_1",
                "coin",
                "currencies/coin",
                "currencies",
                "Coin",
                1,
                False,
                None,
                None,
                None,
                frozenset({"collectable"}),
            ),
        }
    )


def test_gui_changes_autosave_to_the_config_store(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    controller = ApplicationController(store)
    window = MainWindow(controller)

    window._queue_config(theme="dark", idle_wait_seconds=45.0)
    window._flush_config()

    saved = ConfigStore(store.path).load()
    assert saved.theme == "dark"
    assert saved.idle_wait_seconds == 45.0

    window.quit_application()
    app.processEvents()


def test_registered_form_controls_refresh_from_config(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    config = AppConfig(
        close_to_tray=False,
        browser="edge",
        cdp_port=9333,
        loop_interval=2.5,
        theme="dark",
        pause_hotkey=None,
        main_unfocused_opacity=0.75,
    )

    window.browser_page.apply_config(config)
    window.settings_page.apply_config(config)

    assert window.browser_page.browser_choice.current_value() == "edge"
    assert window.browser_page.controls["cdp_port"].value() == 9333
    assert window.settings_page.controls["loop_interval"].value() == 2.5
    assert window.settings_page.controls["theme"].current_value() == "dark"
    assert window.settings_page.controls["pause_hotkey"].value is None
    assert window.settings_page.controls["main_unfocused_opacity"].value() == 75
    assert window.settings_page.opacity_labels["main_unfocused_opacity"].text() == "75%"

    window.quit_application()
    app.processEvents()


def test_config_changes_refresh_only_affected_gui_sections(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    refreshed: list[str] = []

    monkeypatch.setattr(
        window.items_page,
        "apply_config",
        lambda *_args, **_kwargs: refreshed.append("items"),
    )
    monkeypatch.setattr(
        window.shops_page,
        "apply_config",
        lambda *_args, **_kwargs: refreshed.append("shops"),
    )
    monkeypatch.setattr(
        window.browser_page,
        "apply_config",
        lambda *_args, **_kwargs: refreshed.append("browser"),
    )
    monkeypatch.setattr(
        window.settings_page,
        "apply_config",
        lambda *_args, **_kwargs: refreshed.append("settings"),
    )

    window._queue_config(idle_wait_seconds=45.0)
    window._flush_config()

    assert refreshed == ["settings"]
    window.quit_application()
    app.processEvents()


def test_debounced_autosave_completes_through_background_saver(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    saved = QSignalSpy(window._config_saver.saved)

    window._queue_config(idle_wait_seconds=45.0)

    assert saved.wait(3000)
    assert store.current.idle_wait_seconds == 45.0
    assert window.settings_page.saved_label.text() == "Saved"
    window.quit_application()
    app.processEvents()


def test_settings_are_grouped_and_include_start_paused(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    settings_page = window.pages.widget(window._NAVIGATION.index("Settings"))
    assert settings_page is not None
    assert window.minimumWidth() == 1000

    section_groups = {group.title(): group for group in settings_page.findChildren(QGroupBox)}
    section_titles = set(section_groups)
    ordered_sections = [group.title() for group in settings_page.findChildren(QGroupBox)]
    labels = {label.text() for label in settings_page.findChildren(QLabel)}

    assert section_titles == {
        "Automation",
        "Event islands",
        "Board behavior",
        "Resource safeguards",
        "Building repairs",
        "Farm visits and visitors",
        "Keyboard shortcuts",
        "Startup and shutdown",
        "Notifications",
        "Appearance",
    }
    assert ordered_sections == [
        "Automation",
        "Board behavior",
        "Resource safeguards",
        "Building repairs",
        "Farm visits and visitors",
        "Event islands",
        "Notifications",
        "Startup and shutdown",
        "Keyboard shortcuts",
        "Appearance",
    ]
    assert window.settings_page.findChild(QLabel, "settingsVersion") is None
    assert window.dashboard_page.version_label.text().startswith("Farm Merge Valet ")
    assert "Start each bot run paused" in labels
    assert "Dismiss routine game popups automatically" in labels
    assert "Automatically pop stored items" in labels
    assert "Automatically claim supply crates" in labels
    assert "Emergency merge-3 maximum tier" in labels
    assert "Allow obstacle energy spending" in labels
    assert "Preserve resources for building repairs" in labels
    assert "Prioritize shops over other repairs" in labels
    assert "Prioritize cheaper repairs" in labels
    assert "Prioritize obstacles with needed repair materials" in labels
    assert "Automate item actions" in labels
    assert "Automate shops" in labels
    assert "Automate marketplace purchases" in labels
    assert "Claim rewards left by visitors" in labels
    assert "Automate event islands" in labels
    assert "Enable newly supported events by default" in labels
    assert "Automatically claim event rewards" in labels
    assert "Minimum energy before visiting" in labels
    assert "Expand farm land automatically" in labels
    assert "Maximum coins per expansion" in labels
    assert "Maximum crystals per expansion" in labels
    assert "Close managed browser when quitting" not in labels
    assert "Reset all" in {
        button.text() for button in window.settings_page.findChildren(QPushButton)
    }
    assert all("_" not in title and "&" not in title for title in section_titles)
    for section in (
        window.settings_page.planning_advanced_section,
        window.settings_page.repairs_advanced_section,
        window.settings_page.timing_advanced_section,
        window.settings_page.notifications_advanced_section,
        window.settings_page.appearance_advanced_section,
    ):
        assert not section.expanded
        assert section.objectName() == "advancedSection"
        assert section.content.objectName() == "advancedSectionContent"
        assert section.toggle.accessibleName()
        section.toggle.click()
        assert section.expanded
        assert not section.content.isHidden()
    assert section_groups["Board behavior"].isAncestorOf(
        window.settings_page.controls["land_expansion_max_coin_cost"]
    )
    assert section_groups["Board behavior"].isAncestorOf(
        window.settings_page.controls["land_expansion_max_gem_cost"]
    )
    for field in (
        "item_automation_enabled",
        "shop_automation_enabled",
        "marketplace_automation_enabled",
        "auto_dismiss_overlays",
    ):
        assert section_groups["Automation"].isAncestorOf(window.settings_page.controls[field])
    assert section_groups["Automation"].isAncestorOf(window.settings_page.timing_advanced_section)
    for field in ("farm_visit_automation_enabled", "auto_claim_friend_rewards"):
        assert section_groups["Farm visits and visitors"].isAncestorOf(
            window.settings_page.controls[field]
        )
    assert section_groups["Board behavior"].isAncestorOf(
        window.settings_page.controls["land_expansion_automation_enabled"]
    )
    for field in (
        "event_automation_enabled",
        "event_default_enabled",
        "auto_claim_event_rewards",
        "event_visit_energy_threshold",
    ):
        assert section_groups["Event islands"].isAncestorOf(window.settings_page.controls[field])
    for field in (
        "merge_empty_cell_reserve",
        "emergency_merge_three_max_tier",
        "producer_interact_min_empty_cells",
    ):
        assert window.settings_page.planning_advanced_section.isAncestorOf(
            window.settings_page.controls[field]
        )
    for field in (
        "preserve_building_repair_resources",
        "prioritize_repair_shops",
        "prioritize_cheaper_repairs",
        "prioritize_obstacle_repair_resources",
    ):
        assert section_groups["Building repairs"].isAncestorOf(window.settings_page.controls[field])
    assert section_groups["Notifications"].isAncestorOf(
        window.settings_page.notifications_advanced_section
    )
    assert not window.settings_page.notifications_advanced_section.isAncestorOf(
        window.settings_page.controls["webhook_notification_profile"]
    )
    assert not window.settings_page.notifications_advanced_section.isAncestorOf(
        window.settings_page.controls["webhook_include_charts"]
    )
    assert section_groups["Appearance"].isAncestorOf(
        window.settings_page.appearance_advanced_section
    )
    for control in window.settings_page.controls.values():
        accessible = (
            control.display.accessibleName()
            if isinstance(control, HotkeyEdit)
            else control.accessibleName()
        )
        assert accessible

    window.quit_application()
    app.processEvents()


def test_scoped_resets_preserve_other_policy_sections(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("farm_merge_valet.config.user_cache_root", lambda: tmp_path / "cache")
    store = ConfigStore(tmp_path / "config.json")
    store.replace(
        AppConfig(
            close_to_tray=False,
            theme="dark",
            browser="edge",
            cdp_port=9333,
            item_automation_enabled=False,
            shop_automation_enabled=False,
            marketplace_automation_enabled=False,
            preserve_building_repair_resources=False,
            close_managed_browser_on_exit=True,
            log_level="DEBUG",
            overlay_visible=True,
            item_policy_defaults={"interact": True},
            item_policy_overrides={"ingredients/milk": {"interact": False}},
            shop_default_enabled=False,
            recipe_default_enabled=False,
            shop_overrides={"bakery": True},
            shop_ingredient_reserve_default=4,
            shop_ingredient_reserves={"wheat": 8},
        )
    )
    window = MainWindow(ApplicationController(store))
    monkeypatch.setattr(window, "_confirm_reset", lambda *_args: True)

    window.items_page.configuration_header.reset_button.click()
    after_items = ConfigStore(store.path).load()
    assert after_items.item_policy_defaults == AppConfig().item_policy_defaults
    assert after_items.item_category_defaults == AppConfig().item_category_defaults
    assert after_items.item_policy_overrides == {}
    assert not after_items.item_automation_enabled
    assert not after_items.shop_default_enabled
    assert after_items.theme == "dark"
    assert window.items_page.saved_label.text() == "Defaults restored"
    assert window.items_page.saved_label.property("status") == "success"

    window.shops_page.configuration_header.reset_button.click()
    after_shops = ConfigStore(store.path).load()
    assert after_shops.shop_default_enabled
    assert after_shops.recipe_default_enabled
    assert after_shops.shop_overrides == {}
    assert after_shops.shop_ingredient_reserve_default == 0
    assert after_shops.shop_ingredient_reserves == {}
    assert not after_shops.shop_automation_enabled
    assert after_shops.theme == "dark"
    assert window.shops_page.saved_label.text() == "Defaults restored"
    assert window.shops_page.saved_label.property("status") == "success"

    window.settings_page.configuration_header.reset_button.click()
    after_settings = ConfigStore(store.path).load()
    assert after_settings.theme == AppConfig().theme
    assert after_settings.item_automation_enabled
    assert after_settings.shop_automation_enabled
    assert after_settings.marketplace_automation_enabled
    assert after_settings.preserve_building_repair_resources
    assert after_settings.item_category_defaults == AppConfig().item_category_defaults
    assert after_settings.shop_default_enabled
    assert after_settings.browser == "edge"
    assert after_settings.cdp_port == 9333
    assert after_settings.close_managed_browser_on_exit
    assert after_settings.log_level == "DEBUG"
    assert after_settings.overlay_visible
    assert window.settings_page.saved_label.text() == "Defaults restored"
    assert window.settings_page.saved_label.property("status") == "success"

    window._queue_config(theme="dark")
    window._flush_config()
    window.browser_page.configuration_header.reset_button.click()
    after_browser = ConfigStore(store.path).load()
    assert after_browser.browser == AppConfig().browser
    assert after_browser.cdp_port == AppConfig().cdp_port
    assert not after_browser.close_managed_browser_on_exit
    assert after_browser.theme == "dark"
    assert after_browser.item_category_defaults == AppConfig().item_category_defaults
    assert after_browser.shop_default_enabled
    assert window.browser_page.saved_label.text() == "Defaults restored"
    assert window.browser_page.saved_label.property("status") == "success"

    window.settings_page.reset_all_button.click()
    assert ConfigStore(store.path).load() == AppConfig()
    assert window.settings_page.saved_label.text() == "Defaults restored"

    window.quit_application()
    app.processEvents()


def test_shop_bulk_toggle_updates_all_shops_and_recipes(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    shop = CatalogItem(
        game_id="bakery",
        family_id="bakery",
        policy_key="shops/bakery",
        category="shops",
        display_name="Bakery",
        tier=None,
        mergeable=False,
        merge_target=None,
        asset_alias=None,
        asset_path=None,
        capabilities=frozenset({"shop"}),
        available_recipe_ids=("bread",),
    )
    recipe = CatalogItem(
        game_id="bread",
        family_id="bread",
        policy_key="shop_products/bread",
        category="shop_products",
        display_name="Bread",
        tier=None,
        mergeable=False,
        merge_target=None,
        asset_alias=None,
        asset_path=None,
        capabilities=frozenset({"recipe"}),
        recipe=RecipeMetadata("bakery", 60, (), ()),
    )
    write_item_catalog(catalog_dir / "catalog.json", ItemCatalog({"bakery": shop, "bread": recipe}))
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    shop_automation_toggle = window.settings_page.controls["shop_automation_enabled"]
    assert shop_automation_toggle.isChecked()

    bakery = window.shops_page.tree.topLevelItem(0)
    assert bakery is not None and bakery.childCount() == 0
    assert not bakery.isExpanded()
    window.shops_page.search.setText("bread")
    QTest.qWait(150)
    assert bakery.childCount() == 0
    assert not bakery.isHidden()
    assert not bakery.isExpanded()
    bakery.setExpanded(True)
    assert bakery.childCount() == 1
    assert not bakery.child(0).isHidden()
    window.shops_page.search.setText("missing")
    QTest.qWait(150)
    assert bakery.isHidden()
    window.shops_page.search.clear()
    assert not bakery.isHidden()
    assert bakery.isExpanded()
    recipe_cell = window.shops_page.tree.itemWidget(bakery.child(0), 3)
    recipe_toggle = recipe_cell.findChild(QCheckBox) if recipe_cell is not None else None
    assert recipe_toggle is not None
    recipe_toggle.setChecked(False)
    window._flush_config()
    assert not ConfigStore(store.path).load().recipe_overrides["bread"]

    recipe_toggle.setChecked(True)
    window._flush_config()
    window.shops_page.search.setText("bread")
    QTest.qWait(150)
    window.shops_page._set_all(2, False)
    window._flush_config()
    filtered = ConfigStore(store.path).load()
    assert filtered.shop_overrides == {}
    assert not filtered.recipe_overrides["bread"]
    window.shops_page.search.clear()

    window.shops_page._set_all(2, False)
    assert window.shops_page.saved_label.text() == "Saving…"
    window._flush_config()

    saved = ConfigStore(store.path).load()
    assert not saved.shop_overrides["bakery"]
    assert not saved.recipe_overrides["bread"]
    assert window.shops_page.saved_label.text() == "Saved"
    assert window.shops_page.tree.columnCount() == 4
    assert window.shops_page.tree.isSortingEnabled()
    selected_shops = dict(window.shops_page._config.shop_overrides)
    selected_recipes = dict(window.shops_page._config.recipe_overrides)
    shop_automation_toggle.click()
    assert not window._draft.shop_automation_enabled
    assert window.shops_page._config.shop_overrides == selected_shops
    assert window.shops_page._config.recipe_overrides == selected_recipes
    bakery = window.shops_page.tree.topLevelItem(0)
    assert bakery is not None
    window.shops_page.expansion_controls.collapse_button.click()
    assert not bakery.isExpanded()
    window.shops_page.expansion_controls.expand_button.click()
    assert bakery.isExpanded()

    window.quit_application()
    app.processEvents()


def test_policy_sort_preferences_are_loaded_and_persisted(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(
        AppConfig(
            close_to_tray=False,
            items_sort_column="category",
            items_sort_descending=True,
            shops_sort_column="enabled",
            shops_sort_descending=True,
        )
    )
    window = MainWindow(ApplicationController(store))

    assert window.items_page.bulk_header.sortIndicatorSection() == 1
    assert not window.items_page.bulk_header.isSortIndicatorShown()
    assert window.items_page.bulk_header.sortIndicatorOrder() is Qt.SortOrder.DescendingOrder
    assert window.shops_page.bulk_header.sortIndicatorSection() == 3
    assert not window.shops_page.bulk_header.isSortIndicatorShown()
    assert window.shops_page.bulk_header.sortIndicatorOrder() is Qt.SortOrder.DescendingOrder

    window.items_page.bulk_header.setSortIndicator(6, Qt.SortOrder.AscendingOrder)
    window.shops_page.bulk_header.setSortIndicator(1, Qt.SortOrder.AscendingOrder)
    window._flush_config()

    saved = ConfigStore(store.path).load()
    assert saved.items_sort_column == "interact"
    assert not saved.items_sort_descending
    assert saved.shops_sort_column == "type"
    assert not saved.shops_sort_descending

    window.quit_application()
    app.processEvents()


def test_coupled_settings_keep_the_draft_until_the_pair_is_valid(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    minimum = window.settings_page.controls["item_action_delay_min"]
    maximum = window.settings_page.controls["item_action_delay_max"]
    assert isinstance(minimum, QDoubleSpinBox)
    assert isinstance(maximum, QDoubleSpinBox)

    minimum.setValue(10.0)
    minimum.editingFinished.emit()

    assert minimum.value() == 10.0
    assert minimum.property("invalid") is True
    assert maximum.property("invalid") is True
    assert window.settings_page.saved_label.text().startswith("Invalid:")
    assert store.current.item_action_delay_min == AppConfig().item_action_delay_min

    maximum.setValue(11.0)
    maximum.editingFinished.emit()
    window._flush_config()

    saved = ConfigStore(store.path).load()
    assert saved.item_action_delay_min == 10.0
    assert saved.item_action_delay_max == 11.0
    assert minimum.property("invalid") is False
    assert maximum.property("invalid") is False

    assert window._replace_config(AppConfig(close_to_tray=False))
    window.quit_application()
    app.processEvents()


def test_decimal_setting_accepts_fractional_keyboard_input(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    control = window.settings_page.controls["loop_interval"]
    assert isinstance(control, QDoubleSpinBox)

    control.lineEdit().selectAll()
    QTest.keyClicks(control.lineEdit(), "0.25")
    QTest.keyClick(control.lineEdit(), Qt.Key.Key_Return)
    window._flush_config()

    assert control.value() == 0.25
    assert ConfigStore(store.path).load().loop_interval == 0.25

    window.quit_application()
    app.processEvents()


def test_settings_controls_match_model_range_and_parent_feature_state(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False, loop_interval=0.25))
    window = MainWindow(ApplicationController(store))
    page = window.settings_page

    assert page.controls["loop_interval"].minimum() == 0.25
    assert page.controls["loop_interval"].value() == 0.25
    assert not page.controls["event_default_enabled"].isEnabled()
    assert not page.controls["auto_claim_event_rewards"].isEnabled()
    assert not page.controls["event_visit_energy_threshold"].isEnabled()
    assert not page.controls["land_expansion_max_coin_cost"].isEnabled()
    assert not page.controls["land_expansion_max_gem_cost"].isEnabled()
    assert not page.controls["webhook_summary_interval"].isEnabled()
    assert not page._control_labels["land_expansion_max_coin_cost"].isEnabled()
    assert not page._control_labels["webhook_notification_profile"].isEnabled()
    assert not page.notifications_hint.isHidden()
    assert page.controls["prioritize_repair_shops"].isEnabled()

    page.event_automation_toggle.click()
    assert page.controls["event_default_enabled"].isEnabled()
    assert page.controls["auto_claim_event_rewards"].isEnabled()
    assert page.controls["event_visit_energy_threshold"].isEnabled()
    page.land_expansion_toggle.click()
    assert page.controls["land_expansion_max_coin_cost"].isEnabled()
    assert page._control_labels["land_expansion_max_coin_cost"].isEnabled()
    page.building_repairs_toggle.click()
    assert not page.controls["prioritize_repair_shops"].isEnabled()
    assert not page.controls["prioritize_cheaper_repairs"].isEnabled()
    assert not page.controls["prioritize_obstacle_repair_resources"].isEnabled()
    webhook = page.controls["discord_webhook_url"]
    webhook.setText("https://example.test/webhook")
    assert page.controls["webhook_summary_interval"].isEnabled()
    assert page._control_labels["webhook_notification_profile"].isEnabled()
    assert page.notifications_hint.isHidden()

    window.quit_application()
    app.processEvents()


def test_hotkey_recorders_validate_conflicts_and_update_action_hints(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    controller = ApplicationController(store)
    window = MainWindow(controller)
    pause = window.settings_page.controls["pause_hotkey"]
    assert isinstance(pause, HotkeyEdit)

    pause.value_changed.emit("f8")

    assert pause.value == "f9"
    assert pause.display.property("invalid") is True
    assert window.settings_page.saved_label.text().startswith("Invalid:")

    pause.value_changed.emit("ctrl+shift+p")
    window._flush_config()

    assert ConfigStore(store.path).load().pause_hotkey == "ctrl+shift+p"
    assert window.dashboard_page.pause_button.action_text == "Pause"
    assert window.dashboard_page.pause_button.shortcut_label.text() == "Ctrl+Shift+P"
    assert window.pause_action.text() == "Pause bot\tCtrl+Shift+P"

    window.quit_application()
    app.processEvents()
