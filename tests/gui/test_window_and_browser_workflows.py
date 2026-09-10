from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QPushButton,
)

from farm_merge_valet.browser import BrowserKind, BrowserStatus
from farm_merge_valet.catalog.models import CatalogItem, ItemCatalog
from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.gui.controller import (
    ApplicationController,
    ApplicationState,
    ApplicationStatus,
)
from farm_merge_valet.gui.main_window import MainWindow
from farm_merge_valet.gui.services.catalog_freshness import (
    CatalogFreshness,
    CatalogFreshnessState,
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


def test_browser_configuration_is_consolidated_on_browser_page(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    settings_page = window.pages.widget(window._NAVIGATION.index("Settings"))

    assert set(window.browser_page.controls) == {
        "game_portal",
        "game_url",
        "browser",
        "browser_auto_launch",
        "auto_recover_game",
        "browser_executable",
        "browser_profile_dir",
        "window_title",
        "cdp_port",
        "close_managed_browser_on_exit",
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
    browser_groups = {group.title(): group for group in window.browser_page.findChildren(QGroupBox)}
    for section in (
        window.browser_page.browser_advanced_section,
        window.browser_page.assets_advanced_section,
    ):
        assert not section.expanded
        assert section.objectName() == "advancedSection"
        assert section.content.objectName() == "advancedSectionContent"
        section.toggle.click()
        assert section.expanded
    assert browser_groups["Browser connection"].isAncestorOf(
        window.browser_page.browser_advanced_section
    )
    assert browser_groups["Game data"].isAncestorOf(window.browser_page.assets_advanced_section)
    assert window.browser_page.assets_advanced_section.isAncestorOf(
        window.browser_page.cache_clear_button
    )
    assert window.browser_page.browser_advanced_section.isAncestorOf(
        window.browser_page.controls["close_managed_browser_on_exit"]
    )
    assert window.browser_page.game_sync_button.text() == "Synchronize"
    assert window.browser_page.cache_clear_button.text() == "Clear cache"
    assert window.browser_page.cache_clear_button.property("danger") is True

    window.browser_page.portal_choice.set_current_value("yahoo")
    window._flush_config()
    selected = ConfigStore(store.path).load()
    assert selected.game_portal.value == "yahoo"
    assert selected.game_url == "https://www.yahoo.com/games/play/farm-merge-valley"
    assert selected.window_title == ""

    custom_url = "https://www.yahoo.com/games/play/custom-farm-merge-valley"
    window.browser_page.controls["game_url"].setText(custom_url)
    window.browser_page.controls["game_url"].editingFinished.emit()
    window._flush_config()
    assert ConfigStore(store.path).load().game_url == custom_url

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

    assert page.browser_action_button.text() == "Launch browser"
    assert page.restart_button.text() == "Restart"
    assert page.refresh_button.text() == "Refresh"
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

    assert page.browser_action_button.text() == "Stop browser"
    assert page.browser_action_button.property("danger") is True
    assert page.restart_button.isEnabled()

    page.set_runtime_active(True)
    assert not page.browser_action_button.isEnabled()
    assert not page.restart_button.isEnabled()
    assert not page.game_sync_button.isEnabled()
    assert not page.cache_clear_button.isEnabled()

    page.set_catalog_freshness(
        CatalogFreshness(
            CatalogFreshnessState.OUTDATED,
            "Game data update available · Synchronize to refresh assets",
        )
    )
    assert page.game_sync_button.text() == "Update"
    assert "update available" in page.game_sync_status_label.text().casefold()

    page.set_catalog_freshness(
        CatalogFreshness(CatalogFreshnessState.CURRENT, "Catalog is current")
    )
    assert page.game_sync_button.text() == "Synchronize"

    window.quit_application()
    app.processEvents()


def test_disabling_clicked_browser_action_does_not_focus_disclosure(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))
    page = window.browser_page
    window.show()
    page.refresh_button.setFocus(Qt.FocusReason.MouseFocusReason)

    page.set_busy(True)
    app.processEvents()

    assert not page.browser_advanced_section.toggle.hasFocus()
    assert page.browser_advanced_section.toggle.property("keyboardFocus") is not True

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


def test_runtime_state_updates_dashboard_overlay_and_tray_controls(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))

    window._status_changed(ApplicationStatus(state=ApplicationState.PAUSED))

    assert window.dashboard_page.mode_value.text() == "Paused"
    assert window.dashboard_page.mode_value.property("state") == "paused"
    assert not window.dashboard_page.guidance_value.isHidden()
    assert window.dashboard_page.version_label.text().startswith("Farm Merge Valet ")
    assert window.dashboard_page.run_button.action_text == "Stop"
    assert window.dashboard_page.run_button.shortcut_label.text() == "F8"
    assert window.dashboard_page.run_button.property("danger") is True
    assert window.dashboard_page.run_button.shortcut_label.objectName() == "shortcutKeycap"
    assert window.overlay.run_button.action_text == "Stop"
    assert window.overlay.run_button.shortcut_label.text() == "F8"
    assert window.overlay.run_button.property("danger") is True
    assert window.dashboard_page.pause_button.action_text == "Resume"
    assert window.dashboard_page.pause_button.shortcut_label.text() == "F9"
    assert window.overlay.pause_button.action_text == "Resume"
    assert window.overlay.pause_button.shortcut_label.text() == "F9"
    assert window.pause_action.text() == "Resume bot\tF9"
    assert window.run_action.isEnabled()
    assert window.run_action.text() == "Stop bot\tF8"
    assert not window.browser_page.restart_button.isEnabled()

    window._status_changed(ApplicationStatus(state=ApplicationState.RUNNING))
    assert not window.dashboard_page.guidance_value.isHidden()

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
