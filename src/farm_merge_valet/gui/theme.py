"""Application-owned semantic Qt themes."""
# ruff: noqa: E501

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication


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
    selected="#0969da",
    disabled="#d0d7de",
    disabled_text="#57606a",
    scroll="#afb8c1",
    scroll_hover="#8c959f",
    scroll_pressed="#6e7781",
)

_SYSTEM = _Colors(
    window="palette(window)",
    surface="palette(base)",
    surface_subtle="palette(alternate-base)",
    input="palette(base)",
    text="palette(text)",
    muted="palette(mid)",
    border="palette(mid)",
    primary="palette(highlight)",
    primary_hover="palette(highlight)",
    selected="palette(highlight)",
    disabled="palette(midlight)",
    disabled_text="palette(mid)",
    scroll="palette(mid)",
    scroll_hover="palette(dark)",
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
QWidget {{ background: {colors.surface_subtle}; color: {colors.text}; font-size: 13px; }}
QMainWindow, QDialog {{ background: {colors.window}; }}
QLabel#pageTitle {{ font-size: 22px; font-weight: 600; }}
QLabel#pageSubtitle, QLabel#metricLabel {{ color: {colors.muted}; }}
QLabel#metricValue, QLabel#overlayStatus {{ font-size: 16px; font-weight: 600; }}
QFrame#card, QFrame#metricCard {{
    background: {colors.surface}; border: 1px solid {colors.border}; border-radius: 10px;
}}
QFrame#metricCard {{ min-height: 62px; }}
QGroupBox {{ border: 1px solid {colors.border}; border-radius: 8px; margin-top: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; font-weight: 600; }}
QLabel#saveStatus {{ color: {colors.muted}; font-weight: 600; }}
QPushButton {{
    background: {colors.primary}; color: white; border: 1px solid {colors.primary};
    border-radius: 6px; padding: 7px 14px; font-weight: 600;
}}
QPushButton:hover {{ background: {colors.primary_hover}; }}
QPushButton:disabled {{
    background: {colors.disabled}; color: {colors.disabled_text}; border-color: {colors.disabled};
}}
QPushButton[secondary="true"] {{
    background: {colors.surface}; color: {colors.text}; border: 1px solid {colors.border};
}}
QPushButton[secondary="true"]:hover {{ background: {colors.surface_subtle}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {colors.input}; border: 1px solid {colors.border}; border-radius: 6px; padding: 6px;
}}
QLineEdit[invalid="true"], QComboBox[invalid="true"],
QSpinBox[invalid="true"], QDoubleSpinBox[invalid="true"] {{ border: 2px solid #cf222e; }}
QPushButton:focus, QLineEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QAbstractItemView:focus {{
    border: 2px solid {colors.selected};
}}
QListWidget#navigation {{
    background: {colors.window}; border: none; border-right: 1px solid {colors.border}; padding: 8px;
}}
QListWidget#navigation::item {{ padding: 10px; border-radius: 6px; }}
QListWidget#navigation::item:hover {{ background: {colors.surface}; }}
QListWidget#navigation::item:selected {{ background: {colors.selected}; color: white; }}
QTableWidget, QTreeWidget, QPlainTextEdit {{
    background: {colors.input}; alternate-background-color: {colors.surface};
    border: 1px solid {colors.border}; gridline-color: {colors.border};
    selection-background-color: {colors.selected};
}}
QHeaderView::section {{
    background: {colors.surface}; border: none; border-bottom: 1px solid {colors.border};
    padding: 7px; font-weight: 600;
}}
QToolTip {{ background: {colors.surface}; color: {colors.text}; border: 1px solid {colors.border}; }}
""" + _scrollbars(colors)


def apply_theme(app: QApplication, theme: str) -> None:
    app.setPalette(QPalette())
    colors = _DARK if theme == "dark" else _LIGHT if theme == "light" else _SYSTEM
    app.setStyleSheet(_stylesheet(colors))
