from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from farm_merge_valet.gui.theme import apply_theme


@pytest.mark.parametrize(
    ("theme", "handle_color"),
    (
        ("system", "palette(mid)"),
        ("dark", "#484f58"),
        ("light", "#afb8c1"),
    ),
)
def test_themes_apply_consistent_modern_scrollbars(theme: str, handle_color: str) -> None:
    app = QApplication.instance() or QApplication([])

    apply_theme(app, theme)

    stylesheet = app.styleSheet()
    assert "QScrollBar:vertical" in stylesheet
    assert "width: 12px" in stylesheet
    assert "QScrollBar:horizontal" in stylesheet
    assert "height: 12px" in stylesheet
    assert "min-height: 28px" in stylesheet
    assert handle_color in stylesheet

    app.setStyleSheet("")
