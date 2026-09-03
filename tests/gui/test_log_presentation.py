from __future__ import annotations

import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.gui.controller import ApplicationController
from farm_merge_valet.gui.overlay import CompactOverlay
from farm_merge_valet.gui.pages.logs import LogsPage
from farm_merge_valet.gui.theme import apply_theme
from farm_merge_valet.observability.logging import FMV_CONTEXT_ATTRIBUTE, FMV_EVENT_ATTRIBUTE


def test_logs_page_replays_filtered_entries_with_severity_colors() -> None:
    app = QApplication.instance() or QApplication([])
    apply_theme(app, "dark")
    page = LogsPage("INFO")

    page.append("12:00:00", "DEBUG", "diagnostic", logging.DEBUG)
    page.append("12:00:01", "WARNING", "careful", logging.WARNING)
    page.append("12:00:02", "ERROR", "failed", logging.ERROR)

    assert "diagnostic" not in page.view.toPlainText()
    html = page.view.document().toHtml().lower()
    assert "#d29922" in html
    assert "#f85149" in html

    page.apply_log_level("DEBUG")

    assert "diagnostic" in page.view.toPlainText()


def test_log_multiline_content_uses_aligned_plain_text() -> None:
    _app = QApplication.instance() or QApplication([])
    page = LogsPage("DEBUG")

    page.append("12:00:00", "ERROR", "failed\ntraceback detail", logging.ERROR)

    assert page.view.toPlainText() == (
        "[12:00:00] ERROR    failed\n"
        "                    traceback detail"
    )


def test_log_export_status_uses_semantic_success_and_error_tones() -> None:
    _app = QApplication.instance() or QApplication([])
    page = LogsPage("INFO")

    page.set_save_result("C:/logs/output.log")
    assert page.save_status.text() == "Saved"
    assert page.save_status.property("status") == "success"
    assert page.save_status.toolTip() == "C:/logs/output.log"

    page.set_save_error("disk full")
    assert page.save_status.text() == "Save failed"
    assert page.save_status.property("status") == "error"
    assert page.save_status.toolTip() == "disk full"


def test_compact_overlay_uses_configured_level_and_replays_recent_entries() -> None:
    _app = QApplication.instance() or QApplication([])
    overlay = CompactOverlay(AppConfig(log_level="INFO"))

    overlay.append_log("12:00:00", "DEBUG", "diagnostic", logging.DEBUG)

    assert overlay.logs.toPlainText() == ""

    overlay.apply_config(AppConfig(log_level="DEBUG"))

    assert "diagnostic" in overlay.logs.toPlainText()


def test_controller_formats_tracebacks_without_putting_them_in_status(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    controller = ApplicationController(ConfigStore(tmp_path / "config.json"))
    received: list[tuple[str, str, str, int]] = []
    controller.log_received.connect(
        lambda timestamp, level, message, levelno: received.append(
            (timestamp, level, message, levelno)
        )
    )
    try:
        raise ValueError("broken")
    except ValueError:
        record = logging.LogRecord(
            "farm_merge_valet.test",
            logging.ERROR,
            __file__,
            1,
            "Operation failed.",
            (),
            sys.exc_info(),
        )
    setattr(record, FMV_EVENT_ATTRIBUTE, "test.failed")
    setattr(record, FMV_CONTEXT_ATTRIBUTE, {})

    controller._on_record(record)

    assert "Traceback (most recent call last)" in received[0][2]
    assert "ValueError: broken" in received[0][2]
    assert controller.status.last_activity == "Operation failed."
    controller.shutdown()
    app.processEvents()
