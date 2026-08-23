"""Normalize the game view by zooming fully out and panning to the board's
bottom.

Scene geometry is calibrated dynamically, but a normalized view maximizes the
visible board area and reduces the amount of panning needed during a run.

Assumes the user doesn't interact with the game while the bot is running.
If they do -- or the bot is paused and resumed, since that's an explicit
opportunity for them to have scrolled/zoomed -- `initialize_environment`
should be re-run to re-establish the known state.
"""

from __future__ import annotations

import logging
import time

from farm_merge_valet.actions.input import drag, scroll
from farm_merge_valet.capture.window import WindowRegion

logger = logging.getLogger(__name__)

_SETTLE_DELAY = 0.3

# Repeated bursts avoid OS/app wheel-event coalescing. Extra bursts are
# harmless after the game reaches its minimum zoom.
_ZOOM_OUT_CLICKS = -300
_ZOOM_OUT_REPEATS = 5
_SCROLL_TO_BOTTOM_REPEATS = 5

# Screen-space column reserved for panning, between board content and the
# game's right-side controls. This avoids dragging across a real tile.
_DRAG_ANCHOR_X = 1780


def zoom_out_fully(region: WindowRegion) -> None:
    """Zoom out to the game's own minimum zoom via repeated mouse-wheel
    scrolls down -- `scroll()`'s pyautogui convention is that negative
    `clicks` is a downward scroll, confirmed empirically to zoom out
    (rather than in) in this game. See `_ZOOM_OUT_REPEATS` for why this is
    more than one call."""
    cx, cy = region.width // 2, region.height // 2
    for _ in range(_ZOOM_OUT_REPEATS):
        scroll(region, cx, cy, _ZOOM_OUT_CLICKS)
        time.sleep(_SETTLE_DELAY)


def pan(region: WindowRegion, *, toward_bottom: bool, repeats: int = 1) -> None:
    """One or more single-drag pan gestures, anchored at `_DRAG_ANCHOR_X`
    rather than screen-center so the drag can never land on (and
    accidentally interact with) a real tile.

    `toward_bottom` picks the drag direction: confirmed empirically
    against the live game, dragging from lower to higher on screen pans
    the view toward the *top* of the board, so panning toward the
    bottom drags the other way (higher to lower on screen) instead.
    """
    x = min(_DRAG_ANCHOR_X, region.width - 1)
    cy = region.height // 2
    start, end = (cy + 300, cy - 300) if toward_bottom else (cy - 300, cy + 300)
    for _ in range(repeats):
        drag(region, (x, start), (x, end), duration=0.15)
    time.sleep(_SETTLE_DELAY)


def scroll_to_bottom(region: WindowRegion) -> None:
    """Pan the view down to the bottom of the board -- its fixed starting
    area (see module docstring)."""
    pan(region, toward_bottom=True, repeats=_SCROLL_TO_BOTTOM_REPEATS)


def initialize_environment(region: WindowRegion) -> None:
    """Force the game into a known, stable state: fully zoomed out, panned
    to the bottom of the board. Zoom is settled first, since zooming can
    itself shift the visible area (typically toward the zoom center),
    which would undo a scroll done beforehand.
    """
    logger.info("Initializing environment: zooming out and scrolling to bottom.")
    zoom_out_fully(region)
    scroll_to_bottom(region)
