from __future__ import annotations

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QCheckBox, QGroupBox, QLabel, QStyleOptionViewItem

from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.core.catalog_store import write_item_catalog
from farm_merge_valet.core.item_catalog import CatalogItem, ItemCatalog, RecipeMetadata
from farm_merge_valet.gui.application import MainWindow
from farm_merge_valet.gui.controller import (
    ApplicationController,
    ApplicationState,
    ApplicationStatus,
)


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


def test_item_policy_table_only_enables_applicable_controls(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    write_item_catalog(catalog_dir / "catalog.json", _catalog())
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    controller = ApplicationController(store)
    window = MainWindow(controller)

    assert window.item_table.rowCount() == 2
    rows = {
        window.item_table.item(row, 0).text(): row for row in range(window.item_table.rowCount())
    }
    wheat_merge = window.item_table.cellWidget(rows["Wheat"], 3).findChild(QCheckBox)
    wheat_claim = window.item_table.cellWidget(rows["Wheat"], 5).findChild(QCheckBox)
    milk_merge = window.item_table.cellWidget(rows["Milk"], 3).findChild(QCheckBox)
    milk_claim = window.item_table.cellWidget(rows["Milk"], 5).findChild(QCheckBox)

    assert wheat_merge is not None and wheat_merge.isEnabled()
    assert wheat_claim is not None and not wheat_claim.isEnabled()
    assert milk_merge is not None and not milk_merge.isEnabled()
    assert milk_claim is not None and milk_claim.isEnabled() and milk_claim.isChecked()

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


def test_settings_are_grouped_and_include_start_paused(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    settings_page = window.pages.widget(window._NAVIGATION.index("Settings"))
    assert settings_page is not None

    section_titles = {group.title() for group in settings_page.findChildren(QGroupBox)}
    labels = {label.text() for label in settings_page.findChildren(QLabel)}

    assert section_titles == {
        "Automation",
        "Controls & startup",
        "Browser & assets",
        "Notifications",
        "Appearance",
    }
    assert "Start automation paused" in labels

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
    for relative_path in (wheat.asset_path, shop.asset_path, recipe.asset_path):
        assert relative_path is not None
        path = catalog_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        pixmap = QPixmap(16, 16)
        pixmap.fill(QColor("#238636"))
        assert pixmap.save(str(path))

    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    window = MainWindow(ApplicationController(store))

    wheat_row = next(
        row
        for row in range(window.item_table.rowCount())
        if window.item_table.item(row, 0).text() == "Wheat"
    )
    assert not window.item_table.item(wheat_row, 0).icon().isNull()
    assert window.item_table.iconSize() == QSize(40, 40)
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

    window.quit_application()
    app.processEvents()


def test_scoped_resets_preserve_other_policy_sections(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(
        AppConfig(
            close_to_tray=False,
            theme="dark",
            item_policy_defaults={"claim": True},
            item_policy_overrides={"ingredients/milk": {"claim": False}},
            shop_default_enabled=False,
            recipe_default_enabled=False,
            shop_overrides={"bakery": True},
        )
    )
    window = MainWindow(ApplicationController(store))
    monkeypatch.setattr(window, "_confirm_reset", lambda *_args: True)

    window._reset_item_policies()
    after_items = ConfigStore(store.path).load()
    assert after_items.item_policy_defaults == AppConfig().item_policy_defaults
    assert after_items.item_category_defaults == AppConfig().item_category_defaults
    assert after_items.item_policy_overrides == {}
    assert not after_items.shop_default_enabled
    assert after_items.theme == "dark"

    window._reset_shop_policies()
    after_shops = ConfigStore(store.path).load()
    assert after_shops.shop_default_enabled
    assert after_shops.recipe_default_enabled
    assert after_shops.shop_overrides == {}
    assert after_shops.theme == "dark"

    window._reset_general_settings()
    after_settings = ConfigStore(store.path).load()
    assert after_settings.theme == AppConfig().theme
    assert after_settings.item_category_defaults == AppConfig().item_category_defaults
    assert after_settings.shop_default_enabled

    window.quit_application()
    app.processEvents()


def test_individual_item_reset_returns_to_category_default(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    write_item_catalog(catalog_dir / "catalog.json", _catalog())
    store = ConfigStore(tmp_path / "config.json")
    store.replace(
        AppConfig(
            catalog_dir=catalog_dir,
            close_to_tray=False,
            item_policy_overrides={"ingredients/milk": {"claim": False}},
        )
    )
    window = MainWindow(ApplicationController(store))

    window._reset_item("ingredients/milk")
    window._flush_config()

    saved = ConfigStore(store.path).load()
    assert "ingredients/milk" not in saved.item_policy_overrides
    assert saved.item_policy("ingredients/milk").claim

    window.quit_application()
    app.processEvents()


def test_runtime_state_updates_dashboard_overlay_and_tray_controls(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))

    window._status_changed(ApplicationStatus(state=ApplicationState.PAUSED))

    assert window.mode_value.text() == "Paused"
    assert window.pause_button.text() == "Resume"
    assert window.overlay.pause_button.text() == "Resume"
    assert window.pause_action.text() == "Resume bot"
    assert not window.start_action.isEnabled()
    assert window.stop_action.isEnabled()
    assert not window.browser_page.restart_button.isEnabled()

    window.quit_application()
    app.processEvents()


def test_invalid_coupled_setting_reverts_the_edited_control(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    minimum = window.settings_page.controls["item_action_delay_min"]

    minimum.setValue(10.0)

    assert minimum.value() == AppConfig().item_action_delay_min
    assert minimum.property("invalid") is True
    assert window.saved_label.text().startswith("Invalid:")

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
