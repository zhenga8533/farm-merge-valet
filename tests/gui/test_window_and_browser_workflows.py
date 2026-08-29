from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent
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
    assert browser_groups["Managed browser"].isAncestorOf(
        window.browser_page.browser_advanced_section
    )
    assert browser_groups["Game data and assets"].isAncestorOf(
        window.browser_page.assets_advanced_section
    )

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


def test_runtime_state_updates_dashboard_overlay_and_tray_controls(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store))

    window._status_changed(ApplicationStatus(state=ApplicationState.PAUSED))

    assert window.dashboard_page.mode_value.text() == "Paused"
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
