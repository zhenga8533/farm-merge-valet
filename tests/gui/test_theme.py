from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from farm_merge_valet.gui.action_button import ActionButton
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


@pytest.mark.parametrize(
    ("theme", "window", "text", "base"),
    (
        ("light", "#ffffff", "#1f2328", "#ffffff"),
        ("dark", "#0d1117", "#e6edf3", "#0d1117"),
    ),
)
def test_owned_themes_use_semantic_palettes_without_child_backplates(
    theme: str, window: str, text: str, base: str
) -> None:
    app = QApplication.instance() or QApplication([])

    apply_theme(app, theme)

    palette = app.palette()
    assert palette.color(QPalette.ColorRole.Window).name() == window
    assert palette.color(QPalette.ColorRole.Text).name() == text
    assert palette.color(QPalette.ColorRole.Base).name() == base
    assert "QWidget { background:" not in app.styleSheet()
    assert "QWidget#appPage { background:" in app.styleSheet()

    app.setStyleSheet("")


def test_form_controls_share_application_owned_affordances() -> None:
    app = QApplication.instance() or QApplication([])
    apply_theme(app, "light")

    stylesheet = app.styleSheet()
    assert "QComboBox::drop-down" in stylesheet
    assert "QComboBox::down-arrow { image: none" in stylesheet
    assert "QSlider::groove:horizontal" in stylesheet
    assert "QSlider::handle:horizontal" in stylesheet

    app.setStyleSheet("")


@pytest.mark.parametrize(
    ("theme", "text", "muted", "disabled"),
    (
        ("light", "#1f2328", "#57606a", "#57606a"),
        ("dark", "#e6edf3", "#a5afba", "#8b949e"),
    ),
)
def test_action_button_labels_preserve_state_contrast(
    theme: str, text: str, muted: str, disabled: str
) -> None:
    app = QApplication.instance() or QApplication([])
    apply_theme(app, theme)
    primary = ActionButton("Start")
    primary.set_action("Start", "f8")
    secondary = ActionButton("Pause", secondary=True)
    secondary.set_action("Pause", "f9")
    unavailable = ActionButton("Pause", secondary=True)
    unavailable.set_action("Pause", "f9")
    unavailable.setEnabled(False)
    for button in (primary, secondary, unavailable):
        button.show()
    app.processEvents()

    assert primary.action_label.palette().color(QPalette.ColorRole.WindowText).name() == "#ffffff"
    assert primary.shortcut_label.palette().color(QPalette.ColorRole.WindowText).name() == "#ffffff"
    assert secondary.action_label.palette().color(QPalette.ColorRole.WindowText).name() == text
    assert secondary.shortcut_label.palette().color(QPalette.ColorRole.WindowText).name() == muted
    assert (
        unavailable.action_label.palette().color(QPalette.ColorRole.WindowText).name() == disabled
    )

    for button in (primary, secondary, unavailable):
        button.close()
    app.setStyleSheet("")
