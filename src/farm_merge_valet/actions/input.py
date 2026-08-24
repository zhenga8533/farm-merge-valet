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


def _smoothstep(progress: float) -> float:
    """Smooth motion whose velocity reaches zero at both endpoints."""
    return progress * progress * progress * (progress * (progress * 6 - 15) + 10)


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
    pickup_delay: float = 0.0,
    release_delay: float = 0.0,
    decelerate: bool = False,
) -> None:
    """Drag from one point to another, both relative to `region`."""
    pyautogui.moveTo(region.left + start[0], region.top + start[1])
    pressed = False
    try:
        pyautogui.mouseDown(button="left")
        pressed = True
        if pickup_delay:
            time.sleep(pickup_delay)
        if decelerate:
            pyautogui.moveTo(
                region.left + end[0],
                region.top + end[1],
                duration=duration,
                tween=_smoothstep,
            )
        else:
            pyautogui.moveTo(
                region.left + end[0],
                region.top + end[1],
                duration=duration,
            )
        if release_delay:
            time.sleep(release_delay)
    finally:
        if pressed:
            pyautogui.mouseUp(button="left")


def scroll(region: WindowRegion, x: int, y: int, clicks: int) -> None:
    """Scroll the mouse wheel at coordinates relative to `region`'s top-left
    corner. Negative `clicks` scrolls down/zooms out (pyautogui convention).
    """
    pyautogui.moveTo(region.left + x, region.top + y)
    pyautogui.scroll(clicks)


def press(key: str) -> None:
    pyautogui.press(key)
