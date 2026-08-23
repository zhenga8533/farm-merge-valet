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
from threading import Event

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
_PAN_DISTANCE_RATIO = 0.28


def zoom_out_fully(region: WindowRegion, stop_event: Event | None = None) -> bool:
    """Zoom out to the game's own minimum zoom via repeated mouse-wheel
    scrolls down -- `scroll()`'s pyautogui convention is that negative
    `clicks` is a downward scroll, confirmed empirically to zoom out
    (rather than in) in this game. See `_ZOOM_OUT_REPEATS` for why this is
    more than one call."""
    cx, cy = region.width // 2, region.height // 2
    for _ in range(_ZOOM_OUT_REPEATS):
        if stop_event is not None and stop_event.is_set():
            return False
        scroll(region, cx, cy, _ZOOM_OUT_CLICKS)
        if stop_event is not None:
            if stop_event.wait(_SETTLE_DELAY):
                return False
        else:
            time.sleep(_SETTLE_DELAY)
    return True


def pan(
    region: WindowRegion,
    *,
    toward_bottom: bool,
    repeats: int = 1,
    stop_event: Event | None = None,
) -> bool:
    """One or more pan gestures in the viewport's dedicated right-side lane.

    `toward_bottom` picks the drag direction: confirmed empirically
    against the live game, dragging from lower to higher on screen pans
    the view toward the *top* of the board, so panning toward the
    bottom drags the other way (higher to lower on screen) instead.
    """
    x, cy = viewport_layout(region.width, region.height).pan_anchor
    distance = min(round(region.height * _PAN_DISTANCE_RATIO), cy, region.height - 1 - cy)
    start, end = (cy - distance, cy + distance) if toward_bottom else (cy + distance, cy - distance)
    for _ in range(repeats):
        if stop_event is not None and stop_event.is_set():
            return False
        drag(region, (x, start), (x, end), duration=0.15)
    if stop_event is not None:
        return not stop_event.wait(_SETTLE_DELAY)
    time.sleep(_SETTLE_DELAY)
    return True


def scroll_to_bottom(region: WindowRegion, stop_event: Event | None = None) -> bool:
    """Pan the view down to the bottom of the board -- its fixed starting
    area (see module docstring)."""
    return pan(
        region,
        toward_bottom=True,
        repeats=_SCROLL_TO_BOTTOM_REPEATS,
        stop_event=stop_event,
    )


def ensure_reddit_fullscreen(stop_event: Event | None = None) -> WindowRegion | None:
    """Idempotently enable Chrome F11 mode and Reddit's expanded game view."""
    if stop_event is not None and stop_event.is_set():
        return None
    state = read_fullscreen_state(settings.cdp_port, settings.window_title)
    if state is None:
        raise CdpConnectionError("Could not read Reddit fullscreen state.")
    if stop_event is not None and stop_event.is_set():
        return None

    if not state.browser_fullscreen:
        logger.info("Entering Chrome fullscreen mode.")
        press("f11")
        if stop_event is not None:
            if stop_event.wait(_FULLSCREEN_SETTLE_DELAY):
                return None
        else:
            time.sleep(_FULLSCREEN_SETTLE_DELAY)
        find_window(settings.window_title)

    if stop_event is not None and stop_event.is_set():
        return None
    result = expand_game(settings.cdp_port, settings.window_title)
    if result == "not-found":
        raise CdpConnectionError("Could not find Reddit's game fullscreen control.")
    if result == "expanded":
        logger.info("Expanding the Reddit game view.")
        if stop_event is not None:
            if stop_event.wait(_FULLSCREEN_SETTLE_DELAY):
                return None
        else:
            time.sleep(_FULLSCREEN_SETTLE_DELAY)

    if stop_event is not None and stop_event.is_set():
        return None
    verified = read_fullscreen_state(settings.cdp_port, settings.window_title)
    if verified is None or not verified.browser_fullscreen or verified.game_expanded is not True:
        raise CdpConnectionError("Reddit/Chrome fullscreen initialization did not complete.")
    if stop_event is not None and stop_event.is_set():
        return None
    return find_window(settings.window_title)


def initialize_environment(stop_event: Event | None = None) -> bool:
    """Force the game into a known, stable state: fully zoomed out, panned
    to the bottom of the board. Zoom is settled first, since zooming can
    itself shift the visible area (typically toward the zoom center),
    which would undo a scroll done beforehand.
    """
    logger.info("Initializing Reddit/Chrome fullscreen environment.")
    region = ensure_reddit_fullscreen(stop_event)
    if region is None:
        return False
    logger.info("Zooming out and scrolling to the board bottom.")
    return zoom_out_fully(region, stop_event) and scroll_to_bottom(region, stop_event)
