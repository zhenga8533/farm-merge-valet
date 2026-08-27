"""Application-owned semantic Qt themes."""
# ruff: noqa: E501

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget


@dataclass(frozen=True)
class _Colors:
    window: str
    surface: str
    surface_subtle: str
    input: str
    text: str
    muted: str
    border: str
    primary: str
    primary_hover: str
    danger: str
    danger_hover: str
    selected: str
    disabled: str
    disabled_text: str
    scroll: str
    scroll_hover: str
    scroll_pressed: str


_DARK = _Colors(
    window="#0d1117",
    surface="#161b22",
    surface_subtle="#11151b",
    input="#0d1117",
    text="#e6edf3",
    muted="#a5afba",
    border="#30363d",
    primary="#238636",
    primary_hover="#2ea043",
    danger="#da3633",
    danger_hover="#f85149",
    selected="#1f6feb",
    disabled="#30363d",
    disabled_text="#8b949e",
    scroll="#484f58",
    scroll_hover="#6e7681",
    scroll_pressed="#8b949e",
)

_LIGHT = _Colors(
    window="#ffffff",
    surface="#ffffff",
    surface_subtle="#f6f8fa",
    input="#ffffff",
    text="#1f2328",
    muted="#57606a",
    border="#d0d7de",
    primary="#1f883d",
    primary_hover="#1a7f37",
    danger="#cf222e",
    danger_hover="#a40e26",
    selected="#0969da",
    disabled="#d0d7de",
    disabled_text="#57606a",
    scroll="#afb8c1",
    scroll_hover="#8c959f",
    scroll_pressed="#6e7781",
)

_PALETTE_COLORS = _Colors(
    window="palette(window)",
    surface="palette(button)",
    surface_subtle="palette(alternate-base)",
    input="palette(base)",
    text="palette(text)",
    muted="palette(placeholder-text)",
    border="palette(mid)",
    primary="palette(link)",
    primary_hover="palette(link-visited)",
    danger="#cf222e",
    danger_hover="#a40e26",
    selected="palette(highlight)",
    disabled="palette(midlight)",
    disabled_text="palette(placeholder-text)",
    scroll="palette(dark)",
    scroll_hover="palette(shadow)",
    scroll_pressed="palette(highlight)",
)


def _scrollbars(colors: _Colors) -> str:
    return f"""
QScrollBar:vertical {{ background: transparent; border: none; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {colors.scroll}; border-radius: 4px; min-height: 28px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: {colors.scroll_hover}; }}
QScrollBar::handle:vertical:pressed {{ background: {colors.scroll_pressed}; }}
QScrollBar:horizontal {{ background: transparent; border: none; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {colors.scroll}; border-radius: 4px; min-width: 28px; margin: 2px; }}
QScrollBar::handle:horizontal:hover {{ background: {colors.scroll_hover}; }}
QScrollBar::handle:horizontal:pressed {{ background: {colors.scroll_pressed}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QAbstractScrollArea::corner {{ background: transparent; border: none; }}
"""


def _stylesheet(colors: _Colors) -> str:
    return f"""
QWidget {{ color: {colors.text}; font-size: 10pt; }}
QMainWindow, QDialog {{ background: {colors.window}; }}
QLabel#pageTitle {{ font-size: 17pt; font-weight: 600; }}
QLabel#pageSubtitle, QLabel#metricLabel {{ color: {colors.muted}; }}
QLabel#metricValue, QLabel#overlayStatus {{ font-size: 12pt; font-weight: 600; }}
QFrame#card, QFrame#metricCard, QFrame#catalogOnboarding {{
    background: {colors.surface}; border: 1px solid {colors.border}; border-radius: 10px;
}}
QFrame#metricCard {{ min-height: 62px; }}
QFrame#catalogOnboarding {{ min-height: 210px; }}
QLabel#onboardingTitle {{ font-size: 14pt; font-weight: 600; }}
QLabel#onboardingStatus {{ color: {colors.muted}; }}
QLabel#onboardingStatus[status="error"] {{ color: {colors.danger}; }}
QProgressBar {{
    background: {colors.surface_subtle}; border: 1px solid {colors.border};
    border-radius: 4px; min-height: 7px; max-height: 7px;
}}
QProgressBar::chunk {{ background: {colors.primary}; border-radius: 3px; }}
QGroupBox {{ border: 1px solid {colors.border}; border-radius: 8px; margin-top: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; font-weight: 600; }}
QLabel#saveStatus {{ color: {colors.muted}; font-weight: 600; }}
QLabel#saveStatus[status="success"] {{ color: {colors.primary}; }}
QLabel#saveStatus[status="error"] {{ color: {colors.danger}; }}
QPushButton {{
    background: {colors.primary}; color: white; border: 1px solid {colors.primary};
    border-radius: 6px; padding: 7px 14px; font-weight: 600;
}}
QPushButton:hover {{ background: {colors.primary_hover}; }}
QPushButton[danger="true"] {{
    background: {colors.danger}; border-color: {colors.danger};
}}
QPushButton[danger="true"]:hover {{ background: {colors.danger_hover}; }}
QPushButton:disabled {{
    background: {colors.disabled}; color: {colors.disabled_text}; border-color: {colors.disabled};
}}
QPushButton[secondary="true"] {{
    background: {colors.surface}; color: {colors.text}; border: 1px solid {colors.border};
}}
QPushButton[secondary="true"]:hover {{ background: {colors.surface_subtle}; }}
QPushButton[shortcutButton="true"] {{ padding: 0; }}
QPushButton QLabel#actionButtonLabel:enabled {{
    background: transparent; color: white; font-weight: 600;
}}
QPushButton[secondary="true"] QLabel#actionButtonLabel:enabled {{ color: {colors.text}; }}
QPushButton QLabel#shortcutKeycap:enabled {{
    background: rgba(255, 255, 255, 38); color: white;
    border: 1px solid rgba(255, 255, 255, 90); border-radius: 4px;
    padding: 1px 5px; font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 8pt; font-weight: 600;
}}
QPushButton[secondary="true"] QLabel#shortcutKeycap:enabled {{
    background: {colors.surface_subtle}; color: {colors.muted}; border-color: {colors.border};
}}
QPushButton QLabel#actionButtonLabel:disabled,
QPushButton QLabel#shortcutKeycap:disabled {{ color: {colors.disabled_text}; }}
QPushButton QLabel#shortcutKeycap:disabled {{
    background: transparent; border-color: {colors.disabled_text};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {colors.input}; border: 1px solid {colors.border}; border-radius: 6px; padding: 6px;
}}
QComboBox {{ padding-right: 38px; }}
QComboBox::drop-down {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 32px; background: transparent; border: none;
    border-left: 1px solid {colors.border}; border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
}}
QComboBox::drop-down:hover {{ background: {colors.surface_subtle}; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{
    background: {colors.input}; color: {colors.text}; border: 1px solid {colors.border};
    selection-background-color: {colors.selected}; selection-color: white;
    outline: none; padding: 4px;
}}
QSpinBox, QDoubleSpinBox {{ padding-right: 30px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 24px; background: transparent;
    border: none; border-left: 1px solid {colors.border};
    border-bottom: 1px solid {colors.border}; border-top-right-radius: 5px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 24px; background: transparent;
    border: none; border-left: 1px solid {colors.border};
    border-bottom-right-radius: 5px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {colors.surface_subtle};
}}
QSpinBox::up-arrow, QSpinBox::down-arrow,
QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {{
    image: none; width: 0; height: 0;
}}
QLineEdit[invalid="true"], QComboBox[invalid="true"],
QSpinBox[invalid="true"], QDoubleSpinBox[invalid="true"] {{ border: 2px solid #cf222e; }}
QPushButton:focus, QLineEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QAbstractItemView:focus {{
    border: 2px solid {colors.selected};
}}
QSlider::groove:horizontal {{
    height: 6px; background: {colors.border}; border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {colors.selected}; border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {colors.input}; border: 2px solid {colors.selected};
    width: 14px; margin: -6px 0; border-radius: 9px;
}}
QSlider::handle:horizontal:hover, QSlider:focus::handle:horizontal {{
    background: {colors.surface_subtle};
}}
QSlider:disabled::sub-page:horizontal {{ background: {colors.disabled}; }}
QSlider:disabled::handle:horizontal {{
    background: {colors.surface_subtle}; border-color: {colors.disabled_text};
}}
QListWidget#navigation {{
    background: {colors.window}; border: none; border-right: 1px solid {colors.border};
    padding: 8px; outline: none;
}}
QListWidget#navigation::item {{ padding: 10px; border-radius: 6px; }}
QListWidget#navigation::item:hover {{ background: {colors.surface}; }}
QListWidget#navigation::item:selected {{ background: {colors.selected}; color: white; }}
QTableWidget, QTreeWidget, QPlainTextEdit {{
    background: {colors.input}; alternate-background-color: {colors.surface};
    border: 1px solid {colors.border}; gridline-color: {colors.border};
    selection-background-color: {colors.selected};
}}
QTableWidget#policyView, QTreeWidget#policyView {{
    background: {colors.input}; alternate-background-color: {colors.surface_subtle};
    border: 1px solid {colors.border}; border-radius: 8px;
    selection-background-color: {colors.selected};
}}
QTableWidget#policyView::item, QTreeWidget#policyView::item {{
    border: none; border-bottom: 1px solid {colors.border}; padding: 8px 10px;
}}
QTableWidget#policyView::item:hover, QTreeWidget#policyView::item:hover {{
    background: {colors.surface};
}}
QTableWidget#policyView::item:selected, QTreeWidget#policyView::item:selected {{
    background: {colors.selected}; color: white;
}}
QTableWidget#policyView::item:disabled, QTreeWidget#policyView::item:disabled {{
    color: {colors.muted};
}}
QWidget[policyCell="true"] {{ background: transparent; }}
QLabel#policyBadge {{
    background: {colors.surface}; color: {colors.muted};
    border: 1px solid {colors.border}; border-radius: 8px;
    padding: 3px 8px; font-size: 8pt; font-weight: 600;
}}
QLabel#policyUnavailable {{
    font-size: 12pt; font-weight: 600;
}}
QLabel#policyStatus {{
    background: {colors.surface_subtle}; color: {colors.muted};
    border: 1px solid {colors.border}; border-radius: 8px;
    padding: 3px 8px; font-size: 8pt; font-weight: 600;
}}
QLabel#policyStatus[tone="applied"] {{
    color: {colors.primary}; border-color: {colors.primary};
}}
QCheckBox[policyToggle="true"] {{
    background: transparent; spacing: 8px; font-weight: 600;
}}
QHeaderView#policyHeader {{ background: {colors.surface}; }}
QHeaderView#policyHeader::section {{
    background: {colors.surface}; color: {colors.text};
    border: none; border-bottom: 1px solid {colors.border};
    padding: 0 8px; min-height: 42px; font-weight: 600;
}}
QHeaderView#policyHeader::up-arrow,
QHeaderView#policyHeader::down-arrow {{
    image: none; width: 0; height: 0;
}}
QTableCornerButton::section {{
    background: {colors.surface}; border: none; border-bottom: 1px solid {colors.border};
}}
QHeaderView::section {{
    background: {colors.surface}; border: none; border-bottom: 1px solid {colors.border};
    padding: 7px; font-weight: 600;
}}
QToolTip {{ background: {colors.surface}; color: {colors.text}; border: 1px solid {colors.border}; }}
""" + _scrollbars(colors)


def _system_colors(app: QApplication) -> _Colors:
    scheme = app.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return _DARK
    if scheme == Qt.ColorScheme.Light:
        return _LIGHT

    system_window = app.style().standardPalette().color(QPalette.ColorRole.Window)
    return _DARK if system_window.lightness() < 128 else _LIGHT


def apply_theme(app: QApplication, theme: str) -> None:
    colors = (
        _DARK
        if theme == "dark"
        else _LIGHT
        if theme == "light"
        else _system_colors(app)
    )
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, colors.window),
        (QPalette.ColorRole.WindowText, colors.text),
        (QPalette.ColorRole.Base, colors.input),
        (QPalette.ColorRole.AlternateBase, colors.surface_subtle),
        (QPalette.ColorRole.ToolTipBase, colors.surface),
        (QPalette.ColorRole.ToolTipText, colors.text),
        (QPalette.ColorRole.Text, colors.text),
        (QPalette.ColorRole.Button, colors.surface),
        (QPalette.ColorRole.ButtonText, colors.text),
        (QPalette.ColorRole.Highlight, colors.selected),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.PlaceholderText, colors.muted),
        (QPalette.ColorRole.Mid, colors.border),
        (QPalette.ColorRole.Midlight, colors.disabled),
        (QPalette.ColorRole.Dark, colors.scroll),
        (QPalette.ColorRole.Shadow, colors.scroll_hover),
        (QPalette.ColorRole.Link, colors.primary),
        (QPalette.ColorRole.LinkVisited, colors.primary_hover),
    ):
        palette.setColor(role, QColor(color))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            role,
            QColor(colors.disabled_text),
        )
    app.setProperty("fmvTheme", theme)
    app.setPalette(palette)
    stylesheet = _stylesheet(_PALETTE_COLORS)
    if app.styleSheet() != stylesheet:
        app.setStyleSheet(stylesheet)


def refresh_widget_theme(widget: QWidget, theme: str) -> None:
    """Refresh one visible widget subtree after an application palette change."""

    def needs_repolish(child: QWidget) -> bool:
        if child.objectName() in {"policyBadge", "policyStatus"}:
            return True
        if type(child).__name__ in {"PolicyCheckBox", "_BulkCheckBox"}:
            return False
        ancestor = child.parentWidget()
        while ancestor is not None and ancestor is not widget:
            if ancestor.property("policyCell") is True:
                return False
            ancestor = ancestor.parentWidget()
        return child.property("policyCell") is not True

    for child in (widget, *widget.findChildren(QWidget)):
        if not needs_repolish(child):
            continue
        child.style().unpolish(child)
        child.style().polish(child)
        child.update()
    widget.setProperty("fmvTheme", theme)
