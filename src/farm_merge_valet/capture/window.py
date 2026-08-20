"""Locate the host application window that the game is running inside.

The game (Farm Merge Valley) is embedded inside different host apps
(Discord desktop client, a browser tab, etc.), so we target by window
title substring rather than assuming a fixed process.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass

import pygetwindow as gw

logger = logging.getLogger(__name__)

# DWMWA_EXTENDED_FRAME_BOUNDS: the visible window rect, excluding the
# invisible resize-border padding that GetWindowRect (used internally by
# pygetwindow) includes on Windows 10/11. Using the raw GetWindowRect bounds
# causes captured frames to be offset by a few pixels and bleed in whatever
# is behind the window's edges.
_DWMWA_EXTENDED_FRAME_BOUNDS = 9


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def _visible_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = _Rect()
    result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd),
        wintypes.DWORD(_DWMWA_EXTENDED_FRAME_BOUNDS),
        ctypes.byref(rect),
        ctypes.sizeof(rect),
    )
    if result != 0:
        return None
    return rect.left, rect.top, rect.right, rect.bottom


@dataclass(frozen=True)
class WindowRegion:
    """Pixel bounding box of a located window, in screen coordinates."""

    left: int
    top: int
    width: int
    height: int

    @property
    def as_mss_region(self) -> dict[str, int]:
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}


def list_window_titles() -> list[str]:
    """Return the titles of all currently open, non-empty windows."""
    return [title for title in gw.getAllTitles() if title.strip()]


def find_window(title_substring: str) -> WindowRegion:
    """Find the first open window whose title contains `title_substring`.

    Raises:
        LookupError: if no matching window is currently open.
    """
    matches = [w for w in gw.getWindowsWithTitle(title_substring) if w.title.strip()]
    if not matches:
        raise LookupError(
            f"No open window found matching {title_substring!r}. "
            f"Open windows: {list_window_titles()}"
        )
    if len(matches) > 1:
        titles = [w.title for w in matches]
        logger.warning(
            "Multiple windows match %r; using %r. Narrow FMV_WINDOW_TITLE to avoid ambiguity. "
            "All matches: %s",
            title_substring,
            matches[0].title,
            titles,
        )
    window = matches[0]
    bounds = _visible_bounds(window._hWnd)  # noqa: SLF001
    if bounds is not None:
        left, top, right, bottom = bounds
        return WindowRegion(left=left, top=top, width=right - left, height=bottom - top)

    logger.warning(
        "Could not read extended frame bounds for %r; falling back to raw rect.", window.title
    )
    return WindowRegion(left=window.left, top=window.top, width=window.width, height=window.height)
