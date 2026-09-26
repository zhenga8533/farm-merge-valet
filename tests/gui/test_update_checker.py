from __future__ import annotations

import httpx
import pytest
from PySide6.QtWidgets import QApplication

from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.gui.components.update_notes import UpdateNotesDialog
from farm_merge_valet.gui.controller import ApplicationController
from farm_merge_valet.gui.main_window import MainWindow
from farm_merge_valet.gui.services import update_checker
from farm_merge_valet.gui.services.update_checker import (
    AvailableUpdate,
    ReleaseNotes,
    available_update,
    changelog_notes,
    notes_markdown,
    releases_since,
)

_RELEASE_URL = "https://github.com/zhenga8533/farm-merge-valet/releases/tag/{tag}"


def _release(tag: str, body: str = "", **fields: object) -> dict[str, object]:
    return {"tag_name": tag, "html_url": _RELEASE_URL.format(tag=tag), "body": body, **fields}


def test_newer_releases_are_collected_newest_first() -> None:
    payload = [_release("v0.2.0", "- Second"), _release("v0.1.0"), _release("v0.3.0", "- Third")]

    assert available_update(payload, "0.1.0") == AvailableUpdate(
        "0.3.0",
        _RELEASE_URL.format(tag="v0.3.0"),
        (
            ReleaseNotes("0.3.0", _RELEASE_URL.format(tag="v0.3.0"), "- Third"),
            ReleaseNotes("0.2.0", _RELEASE_URL.format(tag="v0.2.0"), "- Second"),
        ),
    )


@pytest.mark.parametrize("tag", ["v0.1.0", "v0.0.9"])
def test_current_or_older_release_is_ignored(tag: str) -> None:
    assert available_update([_release(tag)], "0.1.0") is None


def test_untrusted_draft_and_prerelease_entries_are_ignored() -> None:
    payload = [
        {**_release("v0.4.0"), "html_url": "https://example.test/release"},
        _release("v0.3.0", draft=True),
        _release("v0.2.0", prerelease=True),
    ]

    assert available_update(payload, "0.1.0") is None


def test_malformed_release_response_is_rejected() -> None:
    with pytest.raises(ValueError, match="not a list"):
        available_update({}, "0.1.0")
    with pytest.raises(ValueError, match="missing its tag or URL"):
        available_update([{}], "0.1.0")


def test_generated_pull_request_list_is_removed_from_notes() -> None:
    body = (
        "- Fixed a stall.\n\n## What's Changed\n* Bump ruff by @dependabot\n\n"
        "**Full Changelog**: https://github.com/compare/v1...v2"
    )

    assert changelog_notes(body) == "- Fixed a stall."
    assert changelog_notes("**Full Changelog**: link") == ""


def test_notes_markdown_lists_each_release() -> None:
    releases = (ReleaseNotes("0.3.0", "url", "- Third"), ReleaseNotes("0.2.0", "url", ""))

    assert notes_markdown(releases) == "## 0.3.0\n\n- Third\n\n## 0.2.0\n\n_No release notes._"


def test_release_history_includes_the_running_version() -> None:
    payload = [_release("v0.1.0", "- First"), _release("v0.2.0", "- Second"), _release("v0.0.9")]

    assert [release.version for release in releases_since(payload, "0.1.0")] == [
        "0.2.0",
        "0.1.0",
    ]
    assert releases_since(payload, "not-a-version") == ()


def test_unreachable_release_feed_is_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def not_found(url: str, **_: object) -> httpx.Response:
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(update_checker.httpx, "get", not_found)
    with pytest.raises(httpx.HTTPStatusError):
        update_checker.fetch_available_update("0.1.0")


def test_update_notes_dialog_renders_release_markdown() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = UpdateNotesDialog(
        "What's new", "Summary", "## 0.3.0\n\n- Fixed a stall.", offer_download=False
    )

    assert "Fixed a stall." in dialog.notes.toPlainText()
    assert dialog.download_button is None

    dialog.deleteLater()
    app.processEvents()


def test_update_banner_can_skip_the_available_version(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store), eager_catalog_pages=False)
    update = AvailableUpdate("9.0.0", _RELEASE_URL.format(tag="v9.0.0"), ())
    banner = window.dashboard_page.update_banner

    window._show_update_available(update)
    assert not banner.isHidden()
    assert "9.0.0" in window.dashboard_page.update_label.text()

    window.dashboard_page.update_skip_requested.emit()
    assert banner.isHidden()
    assert window._draft.skipped_update_version == "9.0.0"

    window._show_update_available(update)
    assert banner.isHidden()

    window._show_update_available(AvailableUpdate("9.1.0", update.url, ()))
    assert not banner.isHidden()

    window.quit_application()
    app.processEvents()


def test_dashboard_link_shows_release_notes_for_the_running_version(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_to_tray=False))
    window = MainWindow(ApplicationController(store), eager_catalog_pages=False)
    shown: list[UpdateNotesDialog] = []
    requested: list[str] = []

    def record(dialog: UpdateNotesDialog) -> int:
        shown.append(dialog)
        return 0

    def load(version: str) -> bool:
        requested.append(version)
        return True

    monkeypatch.setattr(UpdateNotesDialog, "exec", record)
    monkeypatch.setattr(window._release_notes_loader, "load", load)
    link = window.dashboard_page.release_notes_link

    link.linkActivated.emit("release-notes")
    assert requested
    assert "Loading" in link.text()

    window._show_release_notes((ReleaseNotes("0.2.8", "url", "- Added release notes."),))
    assert "What's new" in link.text()
    assert "Added release notes." in shown[-1].notes.toPlainText()
    assert shown[-1].download_button is None

    window._show_release_notes(())
    assert "releases page" in shown[-1].notes.toPlainText()

    window.quit_application()
    app.processEvents()
