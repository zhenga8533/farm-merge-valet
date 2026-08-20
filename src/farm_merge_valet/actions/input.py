"""Mouse/keyboard input simulation, scoped to a target window region.

All coordinates taken by this module's functions are relative to the
target window (as returned by vision matching against a captured frame),
and are translated to absolute screen coordinates here.
"""

from __future__ import annotations

import time

import pyautogui

from farm_merge_valet.capture.window import WindowRegion

# Safety net: moving the mouse to a screen corner aborts automation.
pyautogui.FAILSAFE = True


def click(region: WindowRegion, x: int, y: int, *, duration: float = 0.1) -> None:
    """Click at coordinates relative to `region`'s top-left corner."""
    pyautogui.moveTo(region.left + x, region.top + y, duration=duration)
    pyautogui.click()


def drag(
    region: WindowRegion,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    duration: float = 0.3,
) -> None:
    """Drag from one point to another, both relative to `region`."""
    pyautogui.moveTo(region.left + start[0], region.top + start[1])
    pyautogui.dragTo(region.left + end[0], region.top + end[1], duration=duration, button="left")


def sleep(seconds: float) -> None:
    time.sleep(seconds)
