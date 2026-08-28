"""Native window behavior that Qt cannot update without recreating a window."""

from __future__ import annotations

import ctypes
from typing import Any

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020


def _user32() -> Any:
    return ctypes.windll.user32


def set_click_through(hwnd: int, enabled: bool) -> None:
    """Toggle Win32 click-through without changing Qt window flags."""
    user32 = _user32()
    style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    if enabled:
        style |= _WS_EX_LAYERED | _WS_EX_TRANSPARENT
    else:
        style &= ~_WS_EX_TRANSPARENT
    user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)
