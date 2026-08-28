from __future__ import annotations

import os
import time
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QGroupBox,
    QLabel,
    QPushButton,
    QStyleOptionViewItem,
)

from farm_merge_valet.browser import BrowserKind, BrowserStatus
from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.core.catalog_store import write_item_catalog
from farm_merge_valet.core.item_catalog import CatalogItem, ItemCatalog, RecipeMetadata
from farm_merge_valet.core.upgrade_progress import UpgradeProgress, UpgradeTargetProgress
from farm_merge_valet.gui.application import MainWindow
from farm_merge_valet.gui.controller import (
    ApplicationController,
    ApplicationState,
    ApplicationStatus,
)
from farm_merge_valet.gui.hotkey_edit import HotkeyEdit
from farm_merge_valet.gui.policy_view import POLICY_SORT_ROLE


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


def test_missing_catalog_shows_shared_onboarding_and_refreshes_when_discovered(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    controller = ApplicationController(store)
    window = MainWindow(controller)

    assert not window.items_page.catalog_onboarding.isHidden()
    assert not window.shops_page.catalog_onboarding.isHidden()
    assert window.items_page.table.isHidden()
    assert window.shops_page.tree.isHidden()
    assert window.items_page.catalog_onboarding.setup_button.text() == ("Open game and synchronize")

    write_item_catalog(catalog_dir / "catalog.json", _catalog())
    controller.catalog_refreshed.emit(3)
    app.processEvents()

    assert window.items_page.catalog_onboarding.isHidden()
    assert window.shops_page.catalog_onboarding.isHidden()
    assert not window.items_page.table.isHidden()
    assert not window.shops_page.tree.isHidden()
    assert window.item_table.topLevelItemCount() == 3

    window.quit_application()
    app.processEvents()


def test_catalog_pages_populate_lazily_and_ignore_hidden_refreshes(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    write_item_catalog(catalog_dir / "catalog.json", _catalog())
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    controller = ApplicationController(store)
    progress_reads: list[bool] = []
    monkeypatch.setattr(
        "farm_merge_valet.cdp.upgrade_progress.read_upgrade_progress",
        lambda *_args: progress_reads.append(True),
    )
    window = MainWindow(controller, eager_catalog_pages=False)

    assert window.item_table.topLevelItemCount() == 0
    assert window.shop_tree.topLevelItemCount() == 0
    controller.catalog_refreshed.emit(3)
    app.processEvents()
    assert window.item_table.topLevelItemCount() == 0
    assert window.shop_tree.topLevelItemCount() == 0

    window.navigation.setCurrentRow(1)
    assert not window.items_page.loading_state.isHidden()
    time.sleep(0.02)
    app.processEvents()
    assert window.item_table.topLevelItemCount() == 3
    assert window.shop_tree.topLevelItemCount() == 0
    assert progress_reads == []

    window.quit_application()
    app.processEvents()


def test_item_policy_table_only_enables_applicable_controls(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    write_item_catalog(catalog_dir / "catalog.json", _catalog())
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    controller = ApplicationController(store)
    window = MainWindow(controller)

    assert window.item_table.topLevelItemCount() == 3
    assert window.item_table.columnCount() == 7
    assert "Use default" not in {
        button.text() for button in window.items_page.findChildren(QPushButton)
    }
    rows = {
        window.item_table.topLevelItem(row).text(0): window.item_table.topLevelItem(row)
        for row in range(window.item_table.topLevelItemCount())
    }
    wheat_merge = window.item_table.itemWidget(rows["Wheat"], 3).findChild(QCheckBox)
    wheat_interact = window.item_table.itemWidget(rows["Wheat"], 5).findChild(QCheckBox)
    milk_merge = window.item_table.itemWidget(rows["Milk"], 3).findChild(QCheckBox)
    milk_interact = window.item_table.itemWidget(rows["Milk"], 5).findChild(QCheckBox)
    coin_interact = window.item_table.itemWidget(rows["Coin"], 5).findChild(QCheckBox)

    assert wheat_merge is not None and wheat_merge.isEnabled()
    assert wheat_interact is None
    assert milk_merge is None
    assert milk_interact is not None and milk_interact.isEnabled() and milk_interact.isChecked()
    assert coin_interact is not None and coin_interact.isEnabled() and not coin_interact.isChecked()
    assert window.item_table.itemWidget(rows["Wheat"], 5).findChild(QLabel).text() == "—"
    window.item_table.setCurrentItem(rows["Wheat"])
    assert all(rows["Wheat"].text(column) == "" for column in range(1, 7))
    assert rows["Wheat"].data(3, POLICY_SORT_ROLE) is True
    category_badge = window.item_table.itemWidget(rows["Wheat"], 1)
    assert category_badge is not None
    assert window.item_table.columnWidth(1) >= category_badge.sizeHint().width()
    for column in range(1, 7):
        widget = window.item_table.itemWidget(rows["Wheat"], column)
        if widget is not None:
            assert rows["Wheat"].sizeHint(column).width() >= widget.sizeHint().width()

    window.quit_application()
    app.processEvents()


def test_item_tiers_sort_numerically(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    coin_1 = _catalog().items["coin_1"]
    catalog = ItemCatalog(
        {
            "coin_1": coin_1,
            "coin_2": replace(coin_1, game_id="coin_2", tier=2),
            "coin_10": replace(coin_1, game_id="coin_10", tier=10),
        }
    )
    write_item_catalog(catalog_dir / "catalog.json", catalog)
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    window = MainWindow(ApplicationController(store))

    coin = window.item_table.topLevelItem(0)
    assert coin.childCount() == 0
    window.item_search.setText("Tier 2")
    QTest.qWait(150)
    assert coin.childCount() == 0
    assert not coin.isHidden()
    assert not coin.isExpanded()
    coin.setExpanded(True)
    assert [coin.child(index).text(0) for index in range(coin.childCount())] == [
        "Tier 1",
        "Tier 2",
        "Tier 10",
    ]
    assert coin.child(0).isHidden()
    assert not coin.child(1).isHidden()
    window.item_search.clear()
    assert coin.isExpanded()
    assert all(not coin.child(index).isHidden() for index in range(coin.childCount()))

    window.quit_application()
    app.processEvents()


def test_item_families_expand_into_independent_tier_policies(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    wheat_1 = replace(
        _catalog().items["wheat_1"], capabilities=frozenset({"mergeable", "shovelable"})
    )
    wheat_2 = replace(
        wheat_1,
        game_id="wheat_2",
        tier=2,
        mergeable=False,
        merge_target=None,
        capabilities=frozenset({"merge-result", "shovelable"}),
    )
    upgrade_1 = replace(
        wheat_1,
        game_id="upgrade_card_1",
        family_id="upgrade_card",
        policy_key="upgrade_cards/upgrade_card",
        category="upgrade_cards",
        display_name="Upgrade Card",
        asset_path=None,
        asset_alias=None,
        mergeable=False,
        merge_target=None,
        capabilities=frozenset({"clickable", "upgradeCard"}),
    )
    upgrade_2 = replace(
        upgrade_1,
        game_id="upgrade_card_2",
        tier=2,
        mergeable=False,
        merge_target=None,
        capabilities=frozenset({"clickable", "upgradeCard"}),
    )
    write_item_catalog(
        catalog_dir / "catalog.json",
        ItemCatalog(
            {
                "wheat_1": wheat_1,
                "wheat_2": wheat_2,
                "upgrade_card_1": upgrade_1,
                "upgrade_card_2": upgrade_2,
            }
        ),
    )
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    window.items_page.set_upgrade_progress(
        UpgradeProgress((UpgradeTargetProgress("wheat", "wheat_1", 2),))
    )

    roots = {
        window.item_table.topLevelItem(index).text(0): window.item_table.topLevelItem(index)
        for index in range(window.item_table.topLevelItemCount())
    }
    wheat = roots["Wheat"]
    assert "Wheat Upgrade Cards" not in roots
    wheat.setExpanded(True)
    cards = next(
        wheat.child(index)
        for index in range(wheat.childCount())
        if wheat.child(index).text(0) == "Upgrade cards"
    )
    cards.setExpanded(True)
    assert wheat.childCount() == 3
    assert cards.childCount() == 2
    assert [wheat.child(index).text(0) for index in range(2)] == ["Tier 1", "Tier 2"]
    assert [cards.child(index).text(0) for index in range(2)] == ["Tier 1", "Tier 2"]
    assert window.item_table.itemWidget(cards, 5).findChild(QLabel).text() == "Applied"
    assert window.item_table.itemWidget(cards.child(0), 5).findChild(QLabel).text() == "Applied"
    assert window.item_table.itemWidget(cards.child(1), 5).findChild(QLabel).text() == "Applied"
    assert window.item_table.itemWidget(cards.child(0), 5).findChild(QCheckBox) is None
    assert window.item_table.itemWidget(wheat.child(1), 3).findChild(QLabel).text() == "—"
    remove = window.item_table.itemWidget(wheat.child(0), 6).findChild(QCheckBox)
    assert remove is not None
    remove.setChecked(True)
    window._flush_config()
    assert ConfigStore(store.path).load().item_policy("crops/wheat/tier/1").always_remove
    assert window.item_table.isSortingEnabled()
    window.items_page.expansion_controls.expand_button.click()
    assert wheat.isExpanded()
    assert cards.isExpanded()
    window.items_page.expansion_controls.collapse_button.click()
    assert not wheat.isExpanded()
    assert not cards.isExpanded()

    window.quit_application()
    app.processEvents()


def test_obstacle_variants_group_and_expose_clear_interaction_only(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    base = _catalog().items["wheat_1"]
    rock_small = replace(
        base,
        game_id="rock_small",
        family_id="rock_small",
        policy_key="obstacles/rock_small",
        category="obstacles",
        display_name="Rock Small",
        tier=None,
        mergeable=False,
        merge_target=None,
        capabilities=frozenset({"mapSource", "source"}),
        traits=frozenset({"source-clearable", "fixed"}),
        group_id="rock",
        group_name="Rocks",
        variant_label="Small · Fixed",
    )
    rock_movable = replace(
        rock_small,
        game_id="rock_small_moveable",
        family_id="rock_small_moveable",
        policy_key="obstacles/rock_small_moveable",
        capabilities=frozenset({"mapSource", "source", "movable"}),
        traits=frozenset({"source-clearable", "movable"}),
        variant_label="Small · Movable",
    )
    write_item_catalog(
        catalog_dir / "catalog.json",
        ItemCatalog({rock_small.game_id: rock_small, rock_movable.game_id: rock_movable}),
    )
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    window = MainWindow(ApplicationController(store))

    root = window.item_table.topLevelItem(0)
    assert root.text(0) == "Rocks"
    root.setExpanded(True)
    assert [root.child(index).text(0) for index in range(root.childCount())] == [
        "Small · Fixed",
        "Small · Movable",
    ]
    for index in range(root.childCount()):
        interact = window.item_table.itemWidget(root.child(index), 5).findChild(QCheckBox)
        assert interact is not None and interact.isChecked()
        assert interact.accessibleName().endswith(": clear")
        remove_cell = window.item_table.itemWidget(root.child(index), 6)
        assert remove_cell.findChild(QCheckBox) is None
        assert remove_cell.findChild(QLabel).text() == "—"

    window.quit_application()
    app.processEvents()


def test_animal_upgrade_progress_uses_product_identity_but_nests_under_producer(
    tmp_path,
) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    cow = replace(
        _catalog().items["wheat_1"],
        game_id="cow_4",
        family_id="cow",
        policy_key="animals/cow",
        category="animals",
        display_name="Cow",
        tier=4,
        merge_target=None,
        capabilities=frozenset({"merge-result", "harvestable"}),
    )
    card_1 = replace(
        cow,
        game_id="upgrade_card_1",
        family_id="upgrade_card",
        policy_key="upgrade_cards/upgrade_card",
        category="upgrade_cards",
        display_name="Upgrade Card",
        tier=1,
        capabilities=frozenset({"upgradeCard"}),
    )
    card_2 = replace(card_1, game_id="upgrade_card_2", tier=2)
    card_3 = replace(card_1, game_id="upgrade_card_3", tier=3)
    write_item_catalog(
        catalog_dir / "catalog.json",
        ItemCatalog(
            {
                "cow_4": cow,
                "upgrade_card_1": card_1,
                "upgrade_card_2": card_2,
                "upgrade_card_3": card_3,
            }
        ),
    )
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    window = MainWindow(ApplicationController(store))

    window.items_page.set_upgrade_progress(
        UpgradeProgress((UpgradeTargetProgress("milk", "cow_4", 1),))
    )

    cow_root = window.item_table.topLevelItem(0)
    assert cow_root.text(0) == "Cow"
    cards = next(
        cow_root.child(index)
        for index in range(cow_root.childCount())
        if cow_root.child(index).text(0) == "Upgrade cards"
    )
    cards.setExpanded(True)
    assert cards.data(0, Qt.ItemDataRole.UserRole) == "upgrade_cards/upgrade_card/milk"
    assert window.item_table.itemWidget(cards.child(0), 5).findChild(QLabel).text() == "Applied"
    pending_interact = window.item_table.itemWidget(cards.child(1), 5).findChild(QCheckBox)
    assert pending_interact is not None and not pending_interact.isChecked()
    tier_three_interact = window.item_table.itemWidget(cards.child(2), 5).findChild(QCheckBox)
    assert tier_three_interact is not None and tier_three_interact.isChecked()
    pending_interact.setChecked(True)
    window._flush_config()
    assert (
        ConfigStore(store.path)
        .load()
        .item_policy("upgrade_cards/upgrade_card/milk/tier/2")
        .interact
    )

    window.quit_application()
    app.processEvents()


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

    section_titles = {group.title() for group in settings_page.findChildren(QGroupBox)}
    labels = {label.text() for label in settings_page.findChildren(QLabel)}

    assert section_titles == {
        "Automation",
        "Controls and startup",
        "Notifications",
        "Appearance",
        "Application",
    }
    assert "Version" in labels
    assert "Start automation paused" in labels
    assert "Reset all" in {
        button.text() for button in window.settings_page.findChildren(QPushButton)
    }
    assert all("_" not in title and "&" not in title for title in section_titles)
    assert not window.settings_page.automation_advanced_section.expanded
    assert not window.settings_page.notifications_advanced_section.expanded
    assert not window.settings_page.appearance_advanced_section.expanded
    for section in (
        window.settings_page.automation_advanced_section,
        window.settings_page.notifications_advanced_section,
        window.settings_page.appearance_advanced_section,
    ):
        assert section.toggle.accessibleName()
        section.toggle.click()
        assert section.expanded
        assert not section.content.isHidden()
    for control in window.settings_page.controls.values():
        accessible = (
            control.display.accessibleName()
            if isinstance(control, HotkeyEdit)
            else control.accessibleName()
        )
        assert accessible

    window.quit_application()
    app.processEvents()


def test_browser_configuration_is_consolidated_on_browser_page(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    settings_page = window.pages.widget(window._NAVIGATION.index("Settings"))

    assert set(window.browser_page.controls) == {
        "browser",
        "browser_auto_launch",
        "browser_executable",
        "browser_profile_dir",
        "game_url",
        "window_title",
        "cdp_port",
        "catalog_dir",
        "atlas_cache_dir",
    }
    assert settings_page is not None
    assert "Reset browser configuration" in {
        button.text() for button in window.browser_page.findChildren(QPushButton)
    }
    assert "Browser and assets" not in {
        group.title() for group in settings_page.findChildren(QGroupBox)
    }
    assert all(control.accessibleName() for control in window.browser_page.controls.values())
    assert not window.browser_page.advanced_section.expanded
    window.browser_page.advanced_section.toggle.click()
    assert window.browser_page.advanced_section.expanded

    window.browser_page._request("cdp_port", 9333)
    window._flush_config()

    assert ConfigStore(store.path).load().cdp_port == 9333

    window.quit_application()
    app.processEvents()


def test_browser_actions_follow_managed_browser_and_runtime_state(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    page = window.browser_page

    assert page.browser_action_button.text() == "Launch managed browser"
    assert page.browser_action_button.isEnabled()
    assert not page.restart_button.isEnabled()

    page.set_browser_status(
        BrowserStatus(
            running=True,
            compatible=True,
            managed=True,
            kind=BrowserKind.CHROME,
        )
    )

    assert page.browser_action_button.text() == "Stop managed browser"
    assert page.browser_action_button.property("danger") is True
    assert page.restart_button.isEnabled()

    page.set_runtime_active(True)
    assert not page.browser_action_button.isEnabled()
    assert not page.restart_button.isEnabled()
    assert not page.game_sync_button.isEnabled()

    window.quit_application()
    app.processEvents()


def test_activation_changes_only_refresh_window_focus_state(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    controller = ApplicationController(store)
    window = MainWindow(controller)
    main_focus_updates: list[bool] = []
    overlay_focus_updates: list[bool] = []

    monkeypatch.setattr(window, "_apply_main_opacity", lambda: main_focus_updates.append(True))
    monkeypatch.setattr(
        window,
        "_apply_appearance",
        lambda: (_ for _ in ()).throw(AssertionError("activation reapplied window flags")),
    )
    monkeypatch.setattr(
        window.overlay,
        "_apply_focus_state",
        lambda: overlay_focus_updates.append(True),
    )

    event = QEvent(QEvent.Type.ActivationChange)
    window.changeEvent(event)
    window.overlay.changeEvent(event)

    assert main_focus_updates == [True]
    assert overlay_focus_updates == [True]

    window.quit_application()
    app.processEvents()


def test_catalog_sprites_are_shown_for_items_shops_and_recipes(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    base = _catalog()
    wheat = replace(
        base.items["wheat_1"],
        asset_alias="wheat",
        asset_path="crops/wheat/wheat_1.png",
    )
    shop = CatalogItem(
        game_id="bakery",
        family_id="bakery",
        policy_key="shops/bakery",
        category="shops",
        display_name="Bakery",
        tier=None,
        mergeable=False,
        merge_target=None,
        asset_alias="bakery",
        asset_path="shops/bakery/building/bakery.png",
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
        asset_alias="bread",
        asset_path="shops/bakery/recipes/bread.png",
        capabilities=frozenset({"recipe"}),
        recipe=RecipeMetadata("bakery", 60, (), ()),
    )
    catalog = ItemCatalog({**base.items, "wheat_1": wheat, "bakery": shop, "bread": recipe})
    write_item_catalog(catalog_dir / "catalog.json", catalog)

    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    controller = ApplicationController(store)
    window = MainWindow(controller)

    wheat_item = next(
        window.item_table.topLevelItem(row)
        for row in range(window.item_table.topLevelItemCount())
        if window.item_table.topLevelItem(row).text(0) == "Wheat"
    )
    bakery = window.shop_tree.topLevelItem(0)
    assert wheat_item.icon(0).isNull()
    assert bakery is not None and bakery.icon(0).isNull()
    assert bakery.childCount() == 0
    bakery.setExpanded(True)
    assert bakery.childCount() == 1 and bakery.child(0).icon(0).isNull()

    for relative_path in (wheat.asset_path, shop.asset_path, recipe.asset_path):
        assert relative_path is not None
        path = catalog_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        pixmap = QPixmap(16, 16)
        pixmap.fill(QColor("#238636"))
        assert pixmap.save(str(path))

    controller.assets_refreshed.emit()
    app.processEvents()

    wheat_item = next(
        window.item_table.topLevelItem(row)
        for row in range(window.item_table.topLevelItemCount())
        if window.item_table.topLevelItem(row).text(0) == "Wheat"
    )
    assert not wheat_item.icon(0).isNull()
    assert window.item_table.iconSize() == QSize(40, 40)
    assert wheat_item.sizeHint(0).height() == 52
    bakery = window.shop_tree.topLevelItem(0)
    assert bakery is not None and not bakery.icon(0).isNull()
    assert bakery.childCount() == 1 and not bakery.child(0).icon(0).isNull()
    assert window.shop_tree.iconSize() == QSize(100, 54)
    delegate = window.shop_tree.itemDelegate()
    shop_option = QStyleOptionViewItem()
    recipe_option = QStyleOptionViewItem()
    delegate.initStyleOption(shop_option, window.shop_tree.indexFromItem(bakery))
    delegate.initStyleOption(recipe_option, window.shop_tree.indexFromItem(bakery.child(0)))
    assert shop_option.decorationSize == QSize(100, 54)
    assert recipe_option.decorationSize == QSize(40, 40)
    assert window.item_table.objectName() == window.shop_tree.objectName() == "policyView"
    assert window.item_table.alternatingRowColors()
    assert window.shop_tree.alternatingRowColors()
    assert window.item_table.selectionBehavior() == window.shop_tree.selectionBehavior()
    shop_badge = window.shop_tree.itemWidget(bakery, 1)
    recipe_badge = window.shop_tree.itemWidget(bakery.child(0), 1)
    assert shop_badge is not None and shop_badge.findChild(QLabel).text() == "Shop"
    assert recipe_badge is not None and recipe_badge.findChild(QLabel).text() == "Recipe"
    assert bakery.sizeHint(0).height() == 64
    assert bakery.child(0).sizeHint(0).height() == 64

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
            item_policy_defaults={"interact": True},
            item_policy_overrides={"ingredients/milk": {"interact": False}},
            shop_default_enabled=False,
            recipe_default_enabled=False,
            shop_overrides={"bakery": True},
        )
    )
    window = MainWindow(ApplicationController(store))
    monkeypatch.setattr(window, "_confirm_reset", lambda *_args: True)

    window.items_page.configuration_header.reset_button.click()
    after_items = ConfigStore(store.path).load()
    assert after_items.item_policy_defaults == AppConfig().item_policy_defaults
    assert after_items.item_category_defaults == AppConfig().item_category_defaults
    assert after_items.item_policy_overrides == {}
    assert not after_items.shop_default_enabled
    assert after_items.theme == "dark"
    assert window.items_page.saved_label.text() == "Defaults restored"
    assert window.items_page.saved_label.property("status") == "success"

    window.shops_page.configuration_header.reset_button.click()
    after_shops = ConfigStore(store.path).load()
    assert after_shops.shop_default_enabled
    assert after_shops.recipe_default_enabled
    assert after_shops.shop_overrides == {}
    assert after_shops.theme == "dark"
    assert window.shops_page.saved_label.text() == "Defaults restored"
    assert window.shops_page.saved_label.property("status") == "success"

    window.settings_page.configuration_header.reset_button.click()
    after_settings = ConfigStore(store.path).load()
    assert after_settings.theme == AppConfig().theme
    assert after_settings.item_category_defaults == AppConfig().item_category_defaults
    assert after_settings.shop_default_enabled
    assert after_settings.browser == "edge"
    assert after_settings.cdp_port == 9333
    assert window.settings_page.saved_label.text() == "Defaults restored"
    assert window.settings_page.saved_label.property("status") == "success"

    window._queue_config(theme="dark")
    window._flush_config()
    window.browser_page.configuration_header.reset_button.click()
    after_browser = ConfigStore(store.path).load()
    assert after_browser.browser == AppConfig().browser
    assert after_browser.cdp_port == AppConfig().cdp_port
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


def test_item_bulk_toggle_targets_full_catalog_independent_of_filter(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    write_item_catalog(catalog_dir / "catalog.json", _catalog())
    store = ConfigStore(tmp_path / "config.json")
    store.replace(
        AppConfig(
            catalog_dir=catalog_dir,
            close_to_tray=False,
        )
    )
    window = MainWindow(ApplicationController(store))

    window.item_search.setText("Wheat")
    window.items_page._set_all(2, False)
    assert window.items_page.saved_label.text() == "Saving…"
    window._flush_config()

    saved = ConfigStore(store.path).load()
    assert not saved.item_policy("crops/wheat").enabled
    assert not saved.item_policy("ingredients/milk").enabled
    assert window.items_page.saved_label.text() == "Saved"

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

    bakery = window.shop_tree.topLevelItem(0)
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
    recipe_cell = window.shop_tree.itemWidget(bakery.child(0), 2)
    recipe_toggle = recipe_cell.findChild(QCheckBox) if recipe_cell is not None else None
    assert recipe_toggle is not None
    recipe_toggle.setChecked(False)
    window._flush_config()
    assert not ConfigStore(store.path).load().recipe_overrides["bread"]

    window.shops_page._set_all(2, False)
    assert window.shops_page.saved_label.text() == "Saving…"
    window._flush_config()

    saved = ConfigStore(store.path).load()
    assert not saved.shop_overrides["bakery"]
    assert not saved.recipe_overrides["bread"]
    assert window.shops_page.saved_label.text() == "Saved"
    assert window.shop_tree.columnCount() == 3
    assert window.shop_tree.isSortingEnabled()
    bakery = window.shop_tree.topLevelItem(0)
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
    assert window.shops_page.bulk_header.sortIndicatorSection() == 2
    assert not window.shops_page.bulk_header.isSortIndicatorShown()
    assert window.shops_page.bulk_header.sortIndicatorOrder() is Qt.SortOrder.DescendingOrder

    window.items_page.bulk_header.setSortIndicator(5, Qt.SortOrder.AscendingOrder)
    window.shops_page.bulk_header.setSortIndicator(1, Qt.SortOrder.AscendingOrder)
    window._flush_config()

    saved = ConfigStore(store.path).load()
    assert saved.items_sort_column == "interact"
    assert not saved.items_sort_descending
    assert saved.shops_sort_column == "type"
    assert not saved.shops_sort_descending

    window.quit_application()
    app.processEvents()


def test_runtime_state_updates_dashboard_overlay_and_tray_controls(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))

    window._status_changed(ApplicationStatus(state=ApplicationState.PAUSED))

    assert window.mode_value.text() == "Paused"
    assert window.run_button.action_text == "Stop"
    assert window.run_button.shortcut_label.text() == "F8"
    assert window.run_button.property("danger") is True
    assert window.run_button.shortcut_label.objectName() == "shortcutKeycap"
    assert window.overlay.run_button.action_text == "Stop"
    assert window.overlay.run_button.shortcut_label.text() == "F8"
    assert window.overlay.run_button.property("danger") is True
    assert window.pause_button.action_text == "Resume"
    assert window.pause_button.shortcut_label.text() == "F9"
    assert window.overlay.pause_button.action_text == "Resume"
    assert window.overlay.pause_button.shortcut_label.text() == "F9"
    assert window.pause_action.text() == "Resume bot\tF9"
    assert window.run_action.isEnabled()
    assert window.run_action.text() == "Stop bot\tF8"
    assert not window.browser_page.restart_button.isEnabled()

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
    assert window.saved_label.text().startswith("Invalid:")
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


def test_item_policy_columns_fit_the_default_window_width(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    write_item_catalog(catalog_dir / "catalog.json", _catalog())
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    window.resize(1100, 740)
    window.show()
    window.navigation.setCurrentRow(window._NAVIGATION.index("Items"))
    app.processEvents()

    assert window.item_table.horizontalScrollBar().maximum() == 0
    assert [window.item_table.columnWidth(column) for column in range(2, 7)] == [
        92,
        78,
        88,
        92,
        88,
    ]

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
    assert window.saved_label.text().startswith("Invalid:")

    pause.value_changed.emit("ctrl+shift+p")
    window._flush_config()

    assert ConfigStore(store.path).load().pause_hotkey == "ctrl+shift+p"
    assert window.pause_button.action_text == "Pause"
    assert window.pause_button.shortcut_label.text() == "Ctrl+Shift+P"
    assert window.pause_action.text() == "Pause bot\tCtrl+Shift+P"

    window.quit_application()
    app.processEvents()


def test_close_without_close_to_tray_requests_a_real_quit(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    controller = ApplicationController(store)
    window = MainWindow(controller)
    shutdown_requested: list[bool] = []
    real_shutdown = controller.shutdown
    monkeypatch.setattr(controller, "shutdown", lambda: shutdown_requested.append(True))

    window.close()
    app.processEvents()

    assert window._really_quit
    assert shutdown_requested == [True]
    real_shutdown()
    app.processEvents()
