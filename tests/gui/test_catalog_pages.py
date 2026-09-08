from __future__ import annotations

import os
import time
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QStyleOptionViewItem,
    QWidget,
)

from farm_merge_valet.automation.runtime import BuildingRepairState, BuildingRequirement
from farm_merge_valet.catalog.models import (
    CatalogItem,
    ItemCatalog,
    RecipeIngredient,
    RecipeMetadata,
)
from farm_merge_valet.catalog.store import write_item_catalog
from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.gui.components.catalog_icon_delegate import (
    STRUCTURE_ICON_SIZE,
    CatalogIconDelegate,
)
from farm_merge_valet.gui.controller import (
    ApplicationController,
)
from farm_merge_valet.gui.main_window import MainWindow
from farm_merge_valet.gui.pages.shops import IngredientReservesDialog
from tests.marketplace_fixtures import marketplace_catalog


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
        },
        marketplace_offers=marketplace_catalog(),
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
    assert not window.buildings_page.catalog_onboarding.isHidden()
    assert window.items_page.table.isHidden()
    assert window.shops_page.tree.isHidden()
    assert window.buildings_page.tree.isHidden()
    assert window.items_page.catalog_onboarding.setup_button.text() == ("Open game and synchronize")

    write_item_catalog(catalog_dir / "catalog.json", _catalog())
    controller.catalog_refreshed.emit(3)
    app.processEvents()

    assert window.items_page.catalog_onboarding.isHidden()
    assert window.shops_page.catalog_onboarding.isHidden()
    assert window.buildings_page.catalog_onboarding.isHidden()
    assert not window.items_page.table.isHidden()
    assert not window.shops_page.tree.isHidden()
    assert window.items_page.table.topLevelItemCount() == 3

    window.quit_application()
    app.processEvents()


def test_building_requirements_use_catalog_tiers_and_support_repair_policies(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    catalog = ItemCatalog(
        {
            "bakery": CatalogItem(
                "bakery",
                "bakery",
                "shops/bakery",
                "shops",
                "Bakery",
                None,
                False,
                None,
                None,
                None,
                frozenset({"shop"}),
            ),
            "wood_2": CatalogItem(
                "wood_2",
                "wood",
                "resources/wood",
                "resources",
                "Wood",
                2,
                True,
                "wood_3",
                None,
                None,
                frozenset({"mergeable"}),
            ),
        },
        marketplace_offers=marketplace_catalog(),
    )
    write_item_catalog(catalog_dir / "catalog.json", catalog)
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    window = MainWindow(ApplicationController(store))

    window.buildings_page.set_building_repairs(
        (
            BuildingRepairState(
                "bakery",
                0,
                True,
                True,
                False,
                False,
                (BuildingRequirement("wood_2", 3, 1),),
            ),
        )
    )

    bakery = window.buildings_page.tree.topLevelItem(0)
    assert bakery is not None
    assert bakery.text(0) == "Bakery"
    assert bakery.childCount() == 1
    assert window.buildings_page.tree.iconSize() == STRUCTURE_ICON_SIZE
    assert isinstance(window.buildings_page.tree.itemDelegate(), CatalogIconDelegate)
    requirement = bakery.child(0)
    assert requirement.text(0) == "Wood"
    assert requirement.icon(0).isNull() is False or requirement.toolTip(0) == "wood_2"
    type_cell = window.buildings_page.tree.itemWidget(requirement, 1)
    assert type_cell is not None
    assert "Tier 2" in type_cell.findChild(QLabel).text()

    window.buildings_page._set_enabled("bakery", False)
    assert not window.buildings_page._config.building_repair_enabled("bakery")
    window.quit_application()
    app.processEvents()


def test_marketplace_page_defaults_free_claims_on_and_groups_collapsed(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    write_item_catalog(catalog_dir / "catalog.json", _catalog())
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    window = MainWindow(ApplicationController(store))

    page = window.marketplace_page
    marketplace_automation_toggle = window.settings_page.controls["marketplace_automation_enabled"]
    assert marketplace_automation_toggle.isChecked()
    assert page.tree.topLevelItemCount() == 2
    assert page.tree.columnCount() == 3
    assert not hasattr(page, "refresh_requested")
    assert not hasattr(page, "_live")
    assert len(page._toggles) == 2
    assert sum(toggle.isChecked() for toggle in page._toggles.values()) == 1
    assert len(page._group_toggles) == 2
    assert all(
        page._toggles[offer.policy_key].isChecked() == (offer.payment_type == "free")
        for offer in marketplace_catalog()
    )

    assert all(
        not page.tree.topLevelItem(row).isExpanded() for row in range(page.tree.topLevelItemCount())
    )
    assert page._group_toggles["Free Claims"].checkState() == Qt.CheckState.Checked
    assert page._group_toggles["Ingredients"].checkState() == Qt.CheckState.Unchecked
    ingredients = next(
        page.tree.topLevelItem(row)
        for row in range(page.tree.topLevelItemCount())
        if page.tree.topLevelItem(row).text(0) == "Ingredients"
    )
    assert ingredients is not None
    wheat = next(
        ingredients.child(row)
        for row in range(ingredients.childCount())
        if ingredients.child(row).text(0).startswith("Wheat")
    )
    assert wheat is not None and wheat.text(0) == "Wheat \u00d79"
    cost_badge = page.tree.itemWidget(wheat, 1)
    assert cost_badge is not None
    gem_label = cost_badge.findChild(QLabel)
    assert gem_label.text() == "9 Gems"
    assert gem_label.property("tone") == "gems"
    free_claims = next(
        page.tree.topLevelItem(row)
        for row in range(page.tree.topLevelItemCount())
        if page.tree.topLevelItem(row).text(0) == "Free Claims"
    )
    free_label = page.tree.itemWidget(free_claims.child(0), 1).findChild(QLabel)
    assert free_label.text() == "Free"
    assert free_label.property("tone") == "free"

    page._group_toggles["Ingredients"].click()
    assert all(page._toggles[key].isChecked() for key in page._group_policy_keys["Ingredients"])
    assert page._group_toggles["Ingredients"].checkState() == Qt.CheckState.Checked
    ingredient_key = page._group_policy_keys["Ingredients"][0]
    page._toggles[ingredient_key].click()
    assert page._group_toggles["Ingredients"].checkState() == Qt.CheckState.Unchecked
    page._group_toggles["Free Claims"].click()
    assert all(not page._toggles[key].isChecked() for key in page._group_policy_keys["Free Claims"])
    assert all(
        page._config.marketplace_policy_overrides[key] is False
        for key in page._group_policy_keys["Free Claims"]
    )
    selected_offers = dict(page._config.marketplace_policy_overrides)
    marketplace_automation_toggle.click()
    assert not window._draft.marketplace_automation_enabled
    assert page._config.marketplace_policy_overrides == selected_offers

    window.quit_application()
    app.processEvents()


def test_marketplace_page_prompts_for_sync_when_catalog_has_no_offers(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    write_item_catalog(catalog_dir / "catalog.json", ItemCatalog({}))
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    window = MainWindow(ApplicationController(store))

    assert not window.marketplace_page.catalog_onboarding.isHidden()
    assert window.marketplace_page.tree.isHidden()
    assert window.marketplace_page.tree.topLevelItemCount() == 0

    window.quit_application()
    app.processEvents()


def test_marketplace_bulk_toggle_targets_only_filtered_offers(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    write_item_catalog(catalog_dir / "catalog.json", _catalog())
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    page = window.marketplace_page

    page.toolbar.search.setText("Wheat")
    page._set_all(2, True)

    assert page._config.marketplace_policy_enabled("flash:flash_deal_ingredient:wheat")
    assert page._config.marketplace_policy_enabled("free:gems_5_no_ads")

    window.quit_application()
    app.processEvents()


def test_ingredient_reserves_editor_uses_catalog_names_and_preserves_unknown_overrides() -> None:
    app = QApplication.instance() or QApplication([])
    catalog = _catalog()
    recipe = CatalogItem(
        "bread",
        "bread",
        "products/bread",
        "products",
        "Bread",
        None,
        False,
        None,
        None,
        None,
        frozenset(),
        recipe=RecipeMetadata("bakery", 60, (RecipeIngredient("milk", 2),), ("bread",)),
    )
    catalog = ItemCatalog({**catalog.items, "bread": recipe})
    parent = QWidget()
    dialog = IngredientReservesDialog(
        AppConfig(
            shop_ingredient_reserve_default=3,
            shop_ingredient_reserves={"milk": 7, "seasonal_item": 9},
        ),
        catalog,
        parent,
    )

    use_default, reserve = dialog.controls["milk"]
    assert not use_default.isChecked()
    assert reserve.value() == 7
    use_default.setChecked(True)
    dialog.default_control.setValue(5)

    assert dialog.values() == (5, {"seasonal_item": 9})
    dialog.close()
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

    assert window.items_page.table.topLevelItemCount() == 0
    assert window.shops_page.tree.topLevelItemCount() == 0
    controller.catalog_refreshed.emit(3)
    app.processEvents()
    assert window.items_page.table.topLevelItemCount() == 0
    assert window.shops_page.tree.topLevelItemCount() == 0

    window.navigation.setCurrentRow(1)
    assert not window.items_page.loading_state.isHidden()
    deadline = time.monotonic() + 1
    while not window.items_page.loading_state.isHidden() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert window.items_page.table.topLevelItemCount() == 3
    assert window.shops_page.tree.topLevelItemCount() == 0
    assert progress_reads == []

    window.navigation.setCurrentRow(2)
    assert not window.shops_page.loading_state.isHidden()
    deadline = time.monotonic() + 1
    while not window.shops_page.loading_state.isHidden() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert window.shops_page.loading_state.isHidden()

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

    coin = window.items_page.table.topLevelItem(0)
    assert coin.childCount() == 0
    window.items_page.search.setText("Tier 2")
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
    window.items_page.search.clear()
    assert coin.isExpanded()
    assert all(not coin.child(index).isHidden() for index in range(coin.childCount()))

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
    gem = replace(
        wheat,
        game_id="gem_1",
        family_id="gem",
        policy_key="currencies/gem/tier/1",
        category="currencies",
        display_name="Crystal",
        asset_alias="gem",
        asset_path="currencies/gem/gem_1.png",
    )
    crate = replace(
        wheat,
        game_id="crate_1",
        family_id="crate",
        policy_key="resources/crate/tier/1",
        category="resources",
        display_name="Crate",
        asset_alias="crate",
        asset_path="resources/crate/crate_1.png",
    )
    energy = replace(
        wheat,
        game_id="energy_1",
        family_id="energy",
        policy_key="currencies/energy/tier/1",
        category="currencies",
        display_name="Energy",
        asset_alias="energy",
        asset_path="currencies/energy/energy_1.png",
    )
    event_energy = replace(
        wheat,
        game_id="time_limited_event_energy_1",
        family_id="time_limited_event_energy",
        policy_key="currencies/time_limited_event_energy/tier/1",
        category="currencies",
        display_name="Event Energy",
        asset_alias="event_energy",
        asset_path="currencies/event_energy/event_energy_1.png",
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
    catalog = ItemCatalog(
        {
            **base.items,
            "wheat_1": wheat,
            "gem_1": gem,
            "crate_1": crate,
            "energy_1": energy,
            "time_limited_event_energy_1": event_energy,
            "bakery": shop,
            "bread": recipe,
        },
        marketplace_offers=marketplace_catalog(),
    )
    write_item_catalog(catalog_dir / "catalog.json", catalog)

    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    controller = ApplicationController(store)
    window = MainWindow(controller)

    wheat_item = next(
        window.items_page.table.topLevelItem(row)
        for row in range(window.items_page.table.topLevelItemCount())
        if window.items_page.table.topLevelItem(row).text(0) == "Wheat"
    )
    bakery = window.shops_page.tree.topLevelItem(0)
    assert wheat_item.icon(0).isNull()
    assert bakery is not None and bakery.icon(0).isNull()
    assert bakery.childCount() == 0
    bakery.setExpanded(True)
    assert bakery.childCount() == 1 and bakery.child(0).icon(0).isNull()

    for relative_path in (
        wheat.asset_path,
        gem.asset_path,
        crate.asset_path,
        energy.asset_path,
        event_energy.asset_path,
        shop.asset_path,
        recipe.asset_path,
    ):
        assert relative_path is not None
        path = catalog_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        pixmap = QPixmap(16, 16)
        pixmap.fill(QColor("#238636"))
        assert pixmap.save(str(path))

    controller.assets_refreshed.emit()
    app.processEvents()

    wheat_item = next(
        window.items_page.table.topLevelItem(row)
        for row in range(window.items_page.table.topLevelItemCount())
        if window.items_page.table.topLevelItem(row).text(0) == "Wheat"
    )
    assert not wheat_item.icon(0).isNull()
    assert window.items_page.table.iconSize() == QSize(40, 40)
    assert wheat_item.sizeHint(0).height() == 52
    bakery = window.shops_page.tree.topLevelItem(0)
    assert bakery is not None and not bakery.icon(0).isNull()
    assert bakery.childCount() == 1 and not bakery.child(0).icon(0).isNull()
    assert window.shops_page.tree.iconSize() == QSize(100, 54)
    delegate = window.shops_page.tree.itemDelegate()
    shop_option = QStyleOptionViewItem()
    recipe_option = QStyleOptionViewItem()
    delegate.initStyleOption(shop_option, window.shops_page.tree.indexFromItem(bakery))
    delegate.initStyleOption(recipe_option, window.shops_page.tree.indexFromItem(bakery.child(0)))
    assert shop_option.decorationSize == QSize(100, 54)
    assert recipe_option.decorationSize == QSize(40, 40)
    marketplace_ingredients = next(
        window.marketplace_page.tree.topLevelItem(row)
        for row in range(window.marketplace_page.tree.topLevelItemCount())
        if window.marketplace_page.tree.topLevelItem(row).text(0) == "Ingredients"
    )
    marketplace_wheat = next(
        marketplace_ingredients.child(row)
        for row in range(marketplace_ingredients.childCount())
        if marketplace_ingredients.child(row).text(0).startswith("Wheat")
    )
    assert not marketplace_wheat.icon(0).isNull()
    assert window.marketplace_page.tree.iconSize() == QSize(40, 40)
    assert marketplace_wheat.sizeHint(0).height() == 64
    free_claims = next(
        window.marketplace_page.tree.topLevelItem(row)
        for row in range(window.marketplace_page.tree.topLevelItemCount())
        if window.marketplace_page.tree.topLevelItem(row).text(0) == "Free Claims"
    )
    assert all(
        not free_claims.child(row).icon(0).isNull() for row in range(free_claims.childCount())
    )
    assert (
        window.items_page.table.objectName() == window.shops_page.tree.objectName() == "policyView"
    )
    assert window.items_page.table.alternatingRowColors()
    assert window.shops_page.tree.alternatingRowColors()
    assert window.items_page.table.selectionBehavior() == window.shops_page.tree.selectionBehavior()
    shop_badge = window.shops_page.tree.itemWidget(bakery, 1)
    recipe_badge = window.shops_page.tree.itemWidget(bakery.child(0), 1)
    assert shop_badge is not None and shop_badge.findChild(QLabel).text() == "Shop"
    assert recipe_badge is not None and recipe_badge.findChild(QLabel).text() == "Recipe"
    assert bakery.sizeHint(0).height() == 64
    assert bakery.child(0).sizeHint(0).height() == 64

    window.quit_application()
    app.processEvents()
