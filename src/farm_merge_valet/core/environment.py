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

from farm_merge_valet.actions.input import drag, press, scroll
from farm_merge_valet.capture.window import WindowRegion, find_window
from farm_merge_valet.cdp.client import CdpConnectionError
from farm_merge_valet.cdp.reddit_host import expand_game, read_fullscreen_state
from farm_merge_valet.config import settings
from farm_merge_valet.core.viewport import viewport_layout

logger = logging.getLogger(__name__)

_SETTLE_DELAY = 0.3
_FULLSCREEN_SETTLE_DELAY = 1.0

# Repeated bursts avoid OS/app wheel-event coalescing. Extra bursts are
# harmless after the game reaches its minimum zoom.
_ZOOM_OUT_CLICKS = -300
_ZOOM_OUT_REPEATS = 5
_SCROLL_TO_BOTTOM_REPEATS = 5

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
    """One or more pan gestures in the viewport's dedicated right-side lane.

    `toward_bottom` picks the drag direction: confirmed empirically
    against the live game, dragging from lower to higher on screen pans
    the view toward the *top* of the board, so panning toward the
    bottom drags the other way (higher to lower on screen) instead.
    """
    x, cy = viewport_layout(region.width, region.height).pan_anchor
    start, end = (cy - 300, cy + 300) if toward_bottom else (cy + 300, cy - 300)
    for _ in range(repeats):
        drag(region, (x, start), (x, end), duration=0.15)
    time.sleep(_SETTLE_DELAY)


def scroll_to_bottom(region: WindowRegion) -> None:
    """Pan the view down to the bottom of the board -- its fixed starting
    area (see module docstring)."""
    pan(region, toward_bottom=True, repeats=_SCROLL_TO_BOTTOM_REPEATS)


def ensure_reddit_fullscreen() -> WindowRegion:
    """Idempotently enable Chrome F11 mode and Reddit's expanded game view."""
    state = read_fullscreen_state(settings.cdp_port, settings.window_title)
    if state is None:
        raise CdpConnectionError("Could not read Reddit fullscreen state.")

    if not state.browser_fullscreen:
        logger.info("Entering Chrome fullscreen mode.")
        press("f11")
        time.sleep(_FULLSCREEN_SETTLE_DELAY)
        find_window(settings.window_title)

    result = expand_game(settings.cdp_port, settings.window_title)
    if result == "not-found":
        raise CdpConnectionError("Could not find Reddit's game fullscreen control.")
    if result == "expanded":
        logger.info("Expanding the Reddit game view.")
        time.sleep(_FULLSCREEN_SETTLE_DELAY)

    verified = read_fullscreen_state(settings.cdp_port, settings.window_title)
    if verified is None or not verified.browser_fullscreen or verified.game_expanded is not True:
        raise CdpConnectionError("Reddit/Chrome fullscreen initialization did not complete.")
    return find_window(settings.window_title)


def initialize_environment(region: WindowRegion) -> None:
    """Force the game into a known, stable state: fully zoomed out, panned
    to the bottom of the board. Zoom is settled first, since zooming can
    itself shift the visible area (typically toward the zoom center),
    which would undo a scroll done beforehand.
    """
    logger.info("Initializing Reddit/Chrome fullscreen environment.")
    region = ensure_reddit_fullscreen()
    logger.info("Zooming out and scrolling to the board bottom.")
    zoom_out_fully(region)
    scroll_to_bottom(region)
