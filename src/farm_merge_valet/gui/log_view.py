"""Shared presentation for application logs rendered by Qt."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtGui import QColor, QFontDatabase, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit

from farm_merge_valet.gui.theme import warning_color


@dataclass(frozen=True)
class LogEntry:
    timestamp: str
    level: str
    message: str
    levelno: int


def configure_log_view(view: QPlainTextEdit, *, maximum_blocks: int) -> None:
    view.setReadOnly(True)
    view.setMaximumBlockCount(maximum_blocks)
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(10)
    view.setFont(font)


def minimum_level(level: str) -> int:
    value = getattr(logging, level, logging.INFO)
    return value if isinstance(value, int) else logging.INFO


def append_log_entry(view: QPlainTextEdit, entry: LogEntry) -> None:
    cursor = QTextCursor(view.document())
    cursor.movePosition(QTextCursor.MoveOperation.End)
    if not view.document().isEmpty():
        cursor.insertBlock()

    timestamp_format = QTextCharFormat()
    timestamp_format.setForeground(view.palette().placeholderText().color())
    cursor.insertText(f"[{entry.timestamp}] ", timestamp_format)

    level_format = QTextCharFormat()
    level_format.setForeground(_level_color(view, entry.levelno))
    level_format.setFontWeight(700)
    cursor.insertText(f"{entry.level:<8} ", level_format)

    message_format = QTextCharFormat()
    message_format.setForeground(view.palette().text().color())
    lines = entry.message.splitlines() or [""]
    cursor.insertText(lines[0], message_format)
    continuation_prefix = " " * 20
    for line in lines[1:]:
        cursor.insertBlock()
        cursor.insertText(continuation_prefix, timestamp_format)
        cursor.insertText(line, message_format)

    view.setTextCursor(cursor)
    view.ensureCursorVisible()


def render_log_entries(
    view: QPlainTextEdit,
    entries: Iterable[LogEntry],
    threshold: int,
) -> None:
    view.clear()
    for entry in entries:
        if entry.levelno >= threshold:
            append_log_entry(view, entry)


def _level_color(view: QPlainTextEdit, levelno: int) -> QColor:
    dark = view.palette().base().color().lightness() < 128
    if levelno >= logging.ERROR:
        return QColor("#f85149" if dark else "#cf222e")
    if levelno >= logging.WARNING:
        return warning_color(view.palette())
    if levelno >= logging.INFO:
        return view.palette().highlight().color()
    return view.palette().placeholderText().color()
