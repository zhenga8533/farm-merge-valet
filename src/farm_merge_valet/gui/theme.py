"""Application-owned Qt theme tokens."""
# ruff: noqa: E501

from __future__ import annotations

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

_DARK = """
QWidget { background: #11151b; color: #e6edf3; font-family: "Segoe UI"; font-size: 13px; }
QMainWindow, QDialog { background: #0d1117; }
QFrame#card { background: #161b22; border: 1px solid #2b3440; border-radius: 10px; }
QPushButton { background: #238636; border: none; border-radius: 6px; padding: 7px 14px; font-weight: 600; }
QPushButton:hover { background: #2ea043; } QPushButton:disabled { background: #30363d; color: #8b949e; }
QPushButton[secondary="true"] { background: #21262d; border: 1px solid #30363d; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 6px; }
QListWidget { background: #0d1117; border: none; padding: 8px; }
QListWidget::item { padding: 10px; border-radius: 6px; } QListWidget::item:selected { background: #1f6feb; }
QTableWidget, QPlainTextEdit { background: #0d1117; alternate-background-color: #161b22; border: 1px solid #30363d; gridline-color: #30363d; }
QHeaderView::section { background: #161b22; border: none; border-bottom: 1px solid #30363d; padding: 7px; font-weight: 600; }
QTabWidget::pane { border: 1px solid #30363d; } QTabBar::tab { padding: 8px 14px; background: #161b22; } QTabBar::tab:selected { background: #1f6feb; }
QToolTip { background: #21262d; color: #e6edf3; border: 1px solid #30363d; }
"""

_LIGHT = """
QWidget { background: #f6f8fa; color: #1f2328; font-family: "Segoe UI"; font-size: 13px; }
QMainWindow, QDialog { background: #ffffff; }
QFrame#card { background: #ffffff; border: 1px solid #d0d7de; border-radius: 10px; }
QPushButton { background: #1f883d; color: white; border: none; border-radius: 6px; padding: 7px 14px; font-weight: 600; }
QPushButton:hover { background: #1a7f37; } QPushButton:disabled { background: #d0d7de; color: #57606a; }
QPushButton[secondary="true"] { background: #f6f8fa; color: #1f2328; border: 1px solid #d0d7de; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: white; border: 1px solid #d0d7de; border-radius: 6px; padding: 6px; }
QListWidget { background: #f6f8fa; border: none; padding: 8px; }
QListWidget::item { padding: 10px; border-radius: 6px; } QListWidget::item:selected { background: #0969da; color: white; }
QTableWidget, QPlainTextEdit { background: white; alternate-background-color: #f6f8fa; border: 1px solid #d0d7de; gridline-color: #d0d7de; }
QHeaderView::section { background: #f6f8fa; border: none; border-bottom: 1px solid #d0d7de; padding: 7px; font-weight: 600; }
"""


def apply_theme(app: QApplication, theme: str) -> None:
    if theme == "system":
        app.setStyleSheet("")
        app.setPalette(QPalette())
    else:
        app.setStyleSheet(_DARK if theme == "dark" else _LIGHT)
