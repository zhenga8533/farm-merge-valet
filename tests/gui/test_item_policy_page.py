from __future__ import annotations

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QPushButton,
)

from farm_merge_valet.catalog.models import CatalogItem, ItemCatalog
from farm_merge_valet.catalog.store import write_item_catalog
from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.core.upgrade_progress import UpgradeProgress, UpgradeTargetProgress
from farm_merge_valet.gui.components.policy_view import POLICY_SORT_ROLE
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


def test_item_policy_table_only_enables_applicable_controls(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    write_item_catalog(catalog_dir / "catalog.json", _catalog())
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    controller = ApplicationController(store)
    window = MainWindow(controller)

    assert window.items_page.table.topLevelItemCount() == 3
    assert window.items_page.table.columnCount() == 7
    assert "Use default" not in {
        button.text() for button in window.items_page.findChildren(QPushButton)
    }
    rows = {
        window.items_page.table.topLevelItem(row).text(0): window.items_page.table.topLevelItem(row)
        for row in range(window.items_page.table.topLevelItemCount())
    }
    wheat_merge = window.items_page.table.itemWidget(rows["Wheat"], 3).findChild(QCheckBox)
    wheat_interact = window.items_page.table.itemWidget(rows["Wheat"], 5).findChild(QCheckBox)
    milk_merge = window.items_page.table.itemWidget(rows["Milk"], 3).findChild(QCheckBox)
    milk_interact = window.items_page.table.itemWidget(rows["Milk"], 5).findChild(QCheckBox)
    coin_interact = window.items_page.table.itemWidget(rows["Coin"], 5).findChild(QCheckBox)

    assert wheat_merge is not None and wheat_merge.isEnabled()
    assert wheat_interact is None
    assert milk_merge is None
    assert milk_interact is not None and milk_interact.isEnabled() and milk_interact.isChecked()
    assert coin_interact is not None and coin_interact.isEnabled() and not coin_interact.isChecked()
    assert window.items_page.table.itemWidget(rows["Wheat"], 5).findChild(QLabel).text() == "—"
    window.items_page.table.setCurrentItem(rows["Wheat"])
    assert all(rows["Wheat"].text(column) == "" for column in range(1, 7))
    assert rows["Wheat"].data(3, POLICY_SORT_ROLE) is True
    category_badge = window.items_page.table.itemWidget(rows["Wheat"], 1)
    assert category_badge is not None
    assert window.items_page.table.columnWidth(1) >= category_badge.sizeHint().width()
    for column in range(1, 7):
        widget = window.items_page.table.itemWidget(rows["Wheat"], column)
        if widget is not None:
            assert rows["Wheat"].sizeHint(column).width() >= widget.sizeHint().width()

    window.quit_application()
    app.processEvents()


def test_reward_container_exposes_open_policy_control(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    chest = CatalogItem(
        "reward_crate_stickerbook",
        "reward_crate_stickerbook",
        "rewards/reward_crate_stickerbook",
        "rewards",
        "Reward Crate Stickerbook",
        None,
        False,
        None,
        None,
        None,
        frozenset({"clickable", "crateReward", "movable"}),
        traits=frozenset({"container", "movable"}),
    )
    write_item_catalog(catalog_dir / "catalog.json", ItemCatalog({chest.game_id: chest}))
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    window = MainWindow(ApplicationController(store))

    row = window.items_page.table.topLevelItem(0)
    interact = window.items_page.table.itemWidget(row, 5).findChild(QCheckBox)

    assert interact is not None and interact.isEnabled() and interact.isChecked()
    assert interact.accessibleName().endswith(": open")

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
        window.items_page.table.topLevelItem(index).text(0): window.items_page.table.topLevelItem(
            index
        )
        for index in range(window.items_page.table.topLevelItemCount())
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
    assert window.items_page.table.itemWidget(cards, 5).findChild(QLabel).text() == "Applied"
    assert (
        window.items_page.table.itemWidget(cards.child(0), 5).findChild(QLabel).text() == "Applied"
    )
    assert (
        window.items_page.table.itemWidget(cards.child(1), 5).findChild(QLabel).text() == "Applied"
    )
    assert window.items_page.table.itemWidget(cards.child(0), 5).findChild(QCheckBox) is None
    assert window.items_page.table.itemWidget(wheat.child(1), 3).findChild(QLabel).text() == "—"
    remove = window.items_page.table.itemWidget(wheat.child(0), 6).findChild(QCheckBox)
    assert remove is not None
    remove.setChecked(True)
    window._flush_config()
    assert ConfigStore(store.path).load().item_policy("crops/wheat/tier/1").always_remove
    assert window.items_page.table.isSortingEnabled()
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

    root = window.items_page.table.topLevelItem(0)
    assert root.text(0) == "Rocks"
    root.setExpanded(True)
    assert [root.child(index).text(0) for index in range(root.childCount())] == [
        "Small · Fixed",
        "Small · Movable",
    ]
    for index in range(root.childCount()):
        interact = window.items_page.table.itemWidget(root.child(index), 5).findChild(QCheckBox)
        assert interact is not None and interact.isChecked()
        assert interact.accessibleName().endswith(": clear")
        remove_cell = window.items_page.table.itemWidget(root.child(index), 6)
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

    cow_root = window.items_page.table.topLevelItem(0)
    assert cow_root.text(0) == "Cow"
    cards = next(
        cow_root.child(index)
        for index in range(cow_root.childCount())
        if cow_root.child(index).text(0) == "Upgrade cards"
    )
    cards.setExpanded(True)
    assert cards.data(0, Qt.ItemDataRole.UserRole) == "upgrade_cards/upgrade_card/milk"
    assert (
        window.items_page.table.itemWidget(cards.child(0), 5).findChild(QLabel).text() == "Applied"
    )
    pending_interact = window.items_page.table.itemWidget(cards.child(1), 5).findChild(QCheckBox)
    assert pending_interact is not None and not pending_interact.isChecked()
    tier_three_interact = window.items_page.table.itemWidget(cards.child(2), 5).findChild(QCheckBox)
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

    window.items_page.search.setText("Wheat")
    window.items_page._set_all(2, False)
    assert window.items_page.saved_label.text() == "Saving…"
    window._flush_config()

    saved = ConfigStore(store.path).load()
    assert not saved.item_policy("crops/wheat").enabled
    assert not saved.item_policy("ingredients/milk").enabled
    assert window.items_page.saved_label.text() == "Saved"

    window.quit_application()
    app.processEvents()


def test_policy_table_headers_fit_controls_and_sort_indicators(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    write_item_catalog(catalog_dir / "catalog.json", _catalog())
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    window.resize(1100, 740)
    window.show()
    for page_name, header in (
        ("Items", window.items_page.bulk_header),
        ("Shops", window.shops_page.bulk_header),
    ):
        window.navigation.setCurrentRow(window._NAVIGATION.index(page_name))
        app.processEvents()
        for column, control in header._controls.items():
            assert control.width() >= control.sizeHint().width()
            expected_left = (
                header.sectionViewportPosition(column)
                + (header.sectionSize(column) - control.width()) // 2
            )
            assert control.x() == expected_left
            section_right = header.sectionViewportPosition(column) + header.sectionSize(column)
            assert control.geometry().right() < section_right - 14

        last_column = max(header._controls)
        last_control = header._controls[last_column]

        def late_geometry_change(control=last_control) -> None:
            control.move(0, control.y())

        header.geometriesChanged.connect(late_geometry_change)
        header.geometriesChanged.emit()
        assert last_control.x() == 0
        app.processEvents()
        header.geometriesChanged.disconnect(late_geometry_change)
        expected_left = (
            header.sectionViewportPosition(last_column)
            + (header.sectionSize(last_column) - last_control.width()) // 2
        )
        assert last_control.x() == expected_left

    window.quit_application()
    app.processEvents()
